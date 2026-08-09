# brain/weather.py -- 3-day forecast: fetch, cache, serve; plus weather-aware greeting text.
#
# THE ONE ONLINE FEATURE in an offline-first robot, so the whole design is about making the network
# invisible to everything else. A daemon thread ("weather-poll") owns every socket; every consumer -- the
# forecast card, the greeting, the LLM context note, the spoken answer -- reads a snapshot out of memory and
# NEVER touches the network. That is not tidiness, it is the product rule: the greeting path is synchronous
# (say() blocks through Piper + aplay), so a fetch on that path would put a DNS timeout between a visitor
# arriving and Jarvis saying hello. See docs/weather.md.
#
# DEGRADATION LADDER, in the order it is actually walked:
#   fresh forecast    -> card on the sleep screen, advice in greetings, exact spoken answers
#   network down      -> the last-good forecast keeps serving, from memory and from disk across a reboot
#   older than STALE_S-> get() returns None: the card reverts to the honest Zzz face, greetings drop the
#                        advice sentence, and the spoken answer becomes "I can't reach the weather service".
#   never configured  -> one explanatory log line at startup and nothing else ever happens.
# A stale forecast is dropped rather than shown because being confidently wrong about tomorrow is worse
# than saying nothing -- the robot speaks these numbers out loud to people who then dress for them.
#
# PROVIDER ABSTRACTION. Open-Meteo's WMO weather codes, its URL and its JSON shape live inside
# _OpenMeteoProvider and NOWHERE else; it returns normalized days ({date, condition, tmax, tmin}) where
# `condition` is one of the seven CONDITIONS below, which are also the gif basenames. Swapping provider =
# one new class with the same fetch(), one _PROVIDERS entry, one env value.
#
# Pure stdlib -- safe to import anywhere, on any board, with no camera, face panel or network.

import json
import logging
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

log = logging.getLogger("robot.weather")

_TRUE = {"1", "true", "yes", "on"}

# The seven canonical conditions. These strings ARE the gif basenames in brain/weather_icons/ -- a typo here
# is a missing icon, so weather_screen.py and the tests import this tuple rather than re-spelling them.
CONDITIONS = ("Sunny", "PartlyCloudy", "Cloudy",
              "SunnyRain", "PartlyCloudyRain", "CloudyRain", "Thunderstorms")

# Spoken/written wording per condition (the gif basenames are CamelCase; people are not).
_CONDITION_WORDS = {
    "Sunny":            "sunny",
    "PartlyCloudy":     "partly cloudy",
    "Cloudy":           "cloudy",
    "SunnyRain":        "sunny with showers",
    "PartlyCloudyRain": "showers",
    "CloudyRain":       "rain",
    "Thunderstorms":    "thunderstorms",
}

# Conditions that mean "you will get wet" -- they outrank the temperature bands in advice selection.
# PUBLIC: brain/social.py composes job-aware advice off the same distinction, and a module reaching into
# another's underscore names is a rename away from breaking silently.
WET_CONDITIONS = ("SunnyRain", "PartlyCloudyRain", "CloudyRain")
_WET = WET_CONDITIONS          # legacy alias, used inside this module

DEFAULT_TZ = "Australia/Melbourne"

# The display needs today + 2. We ask for FOUR so that a cache fetched yesterday evening still yields three
# full days after midnight rolls "Today" forward (see WeatherService.get()).
FORECAST_DAYS = 4


# --- env getters -----------------------------------------------------------------------------------------
# run_me.sh sources robot.env with `set -a`, so a key left BLANK in the file arrives as an empty string, not
# as absent. os.environ.get(key, default) would then hand back "" and every int()/float() would explode or,
# worse, a blank would silently mean 0. `get(key, "") or default` is the repo's fix (brain/pir.py:52) and the
# except clause catches the TUPLE, not bare ValueError -- five getters drifted from that exemplar once and
# mapped set-but-empty to zero.

def _env(key, default=""):
    return (os.environ.get(key, "") or default).strip()


def _envf(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _envi(key, default):
    try:
        return int(float(os.environ.get(key, "") or default))
    except (TypeError, ValueError):
        return int(default)


def enabled():
    """True when ROBOT_WEATHER_ENABLE is set (1/true/yes/on).

    Deliberately NOT ANDed with concierge.enabled(): the spoken forecast and the LLM context note are just as
    useful on the crawler car. Only the greeting hooks are concierge-shaped, and they live inside the
    concierge branch of main.py anyway."""
    return _env("ROBOT_WEATHER_ENABLE", "0").lower() in _TRUE


def night_icons_enabled():
    """Use `-night` icon variants after sunset (ROBOT_WEATHER_NIGHT_ICONS, default on).

    Costs nothing when the artwork is absent: the resolver falls through to the day icon, so this being
    on for a robot with no night gifs is identical to it being off."""
    return _env("ROBOT_WEATHER_NIGHT_ICONS", "1").lower() in _TRUE


def evening_roll_enabled():
    """After sunset, lead the card with TONIGHT's low instead of a day that is already over
    (ROBOT_WEATHER_EVENING_ROLL, default on)."""
    return _env("ROBOT_WEATHER_EVENING_ROLL", "1").lower() in _TRUE


def frost_c():
    """Min at or below this uses the Frost icon, or None to disable. Default 2 degrees.

    Measured at the reference location: 11 nights a year at or below 2C, coldest -0.1C. Rare on purpose --
    a card that looks different on eleven mornings is a card somebody actually reads."""
    v = _envf("ROBOT_WEATHER_FROST_C", 2)
    return None if v <= -90 else v


def scorcher_c():
    """Max at or above this uses the Hot icon, or None to disable. Default 35 degrees.
    Reference location: 6 days a year at or above 35C, hottest 41.6C."""
    v = _envf("ROBOT_WEATHER_SCORCHER_C", 35)
    return None if v >= 90 else v


def greeting_enabled():
    """True when weather-aware greeting TEXT is wanted (ROBOT_WEATHER_GREETING, default on).
    0 leaves the plain concierge greeting byte-for-byte unchanged while the card and the spoken answer
    still work -- they are separate surfaces, so they get a separate switch."""
    return _env("ROBOT_WEATHER_GREETING", "1").lower() in _TRUE


# --- timezone --------------------------------------------------------------------------------------------
# The first timezone-aware code in this repo. It exists because NOTHING here sets the Pi's system timezone,
# so a freshly-imaged card is usually UTC -- which in Melbourne means "good evening" at breakfast and a
# forecast card whose "Today" rolls over at 10am. One warning, then naive local time, and never an exception.

_tz_warned = [False]


def tzinfo(tz=None):
    """ZoneInfo for the configured zone, or None to mean 'use naive local time'. Warns ONCE.

    PUBLIC because brain/social.py buckets sightings on CALENDAR DAYS in this same zone -- one clock for
    "is it morning?" and "was that today?" or the two features disagree about when a day starts."""
    name = (tz or _env("ROBOT_WEATHER_TZ") or DEFAULT_TZ)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:
        if not _tz_warned[0]:
            _tz_warned[0] = True
            log.warning("weather: timezone %r unusable (%s) -- falling back to this machine's local clock, "
                        "so greetings and the day rollover follow the system timezone instead", name, e)
        return None


_tzinfo = tzinfo               # legacy alias, used inside this module


def _now_local(tz=None):
    """Current datetime in the configured zone (or naive local time on fallback)."""
    zi = _tzinfo(tz)
    return datetime.now(zi) if zi is not None else datetime.now()


def today_iso(tz=None):
    """Today's date, in the CONFIGURED zone, as 'YYYY-MM-DD' -- the key the day re-index matches on."""
    return _now_local(tz).date().isoformat()


def _hour_now(tz=None):
    """The local hour 0-23, used only to pick a morning/afternoon/evening opener."""
    return _now_local(tz).hour


def _parse_local(stamp, tz=None):
    """Open-Meteo's local ISO stamp ('2026-08-09T17:45') -> an aware datetime, or None."""
    try:
        dt = datetime.fromisoformat(str(stamp))
        if dt.tzinfo is None:
            zi = _tzinfo(tz)
            dt = dt.replace(tzinfo=zi) if zi is not None else dt
        return dt
    except (TypeError, ValueError):
        return None


def is_night(snapshot=None, now_ts=None, tz=None):
    """Is it dark RIGHT NOW? Uses today's sunrise/sunset when the forecast carries them.

    Why this is worth a real answer rather than an hour threshold: at this latitude sunset moves from
    17:12 to 19:37 across the year, so any fixed hour is wrong for months at a time. The fallback exists
    for a snapshot that predates the field (an old disk cache) or a provider that omits it -- 19:00-06:00
    is crude but never worse than pretending it is always daytime.

    Never raises: every caller is a display decision, and a display decision must not be able to break a
    poll cycle."""
    try:
        now = _now_local(tz) if now_ts is None else datetime.fromtimestamp(now_ts, _tzinfo(tz))
        days = (snapshot or {}).get("days") or []
        today = days[0] if days else {}
        rise = _parse_local(today.get("sunrise"), tz)
        set_ = _parse_local(today.get("sunset"), tz)
        if rise is not None and set_ is not None:
            # Compare on the clock, not the date: `today` is whatever the card currently calls day one,
            # and after the evening roll that is still the day whose sunset just passed.
            return not (rise.timetz() <= now.timetz() < set_.timetz())
        return not (6 <= now.hour < 19)
    except Exception as e:
        log.debug("weather: is_night failed (%s) -- assuming daytime", e)
        return False


# --- provider: Open-Meteo --------------------------------------------------------------------------------

class _OpenMeteoProvider:
    """Open-Meteo forecast client. Keyless, no account. Everything Open-Meteo-specific stops here: callers
    get normalized days and never see a WMO code.

    The transport is injected (urlopen=) rather than monkeypatched globally -- the same dependency-injection
    seam concierge.presence_probe uses, so tests/test_weather_*.py run with zero network access."""

    name = "open-meteo"
    URL = "https://api.open-meteo.com/v1/forecast"

    # WMO interpretation code -> one of CONDITIONS. Exhaustive over Open-Meteo's published table.
    # SNOW-EXCLUSION: there is no snow asset (this is a Melbourne hallway), so the snow and freezing codes
    # map to their closest RAIN equivalent -- 71..77/85/86 CAN legitimately appear in a forecast (alpine grid
    # cells, freak events) and mapping them, rather than filtering them, is what guarantees the card renders.
    _WMO_TO_CONDITION = {
        0:  "Sunny",             # clear sky
        1:  "Sunny",             # mainly clear
        2:  "PartlyCloudy",      # partly cloudy
        3:  "Cloudy",            # overcast
        45: "Cloudy",            # fog
        48: "Cloudy",            # depositing rime fog
        51: "PartlyCloudyRain",  # drizzle, light
        53: "CloudyRain",        # drizzle, moderate
        55: "CloudyRain",        # drizzle, dense
        56: "CloudyRain",        # freezing drizzle, light
        57: "CloudyRain",        # freezing drizzle, dense
        61: "PartlyCloudyRain",  # rain, slight
        63: "CloudyRain",        # rain, moderate
        65: "CloudyRain",        # rain, heavy
        66: "CloudyRain",        # freezing rain, light
        67: "CloudyRain",        # freezing rain, heavy
        71: "CloudyRain",        # snow fall, slight
        73: "CloudyRain",        # snow fall, moderate
        75: "CloudyRain",        # snow fall, heavy
        77: "Cloudy",            # snow grains -- a dry flurry, no rain analogue; grey sky is the honest one
        80: "SunnyRain",         # rain showers, slight -- the convective sun-shower, the one code that earns it
        81: "PartlyCloudyRain",  # rain showers, moderate
        82: "CloudyRain",        # rain showers, violent
        85: "CloudyRain",        # snow showers, slight
        86: "CloudyRain",        # snow showers, heavy
        95: "Thunderstorms",     # thunderstorm
        96: "Thunderstorms",     # thunderstorm with slight hail
        99: "Thunderstorms",     # thunderstorm with heavy hail
    }

    def __init__(self, urlopen=None):
        self._urlopen = urlopen or urllib.request.urlopen

    @classmethod
    def condition_for(cls, code):
        """WMO code -> condition. An unknown or future code becomes 'Cloudy': there must be NO unhandled UI
        state, and a grey sky is the least wrong thing to claim about a code we cannot read."""
        try:
            return cls._WMO_TO_CONDITION.get(int(code), "Cloudy")
        except (TypeError, ValueError):
            return "Cloudy"

    def fetch(self, lat, lon, tz, days, timeout_s):
        """Return a list of normalized day dicts, or raise. Retries transient network faults against an
        ABSOLUTE deadline (the inference.py shape) so the TOTAL wall clock stays inside timeout_s -- the
        point of a retry here is to dodge a stalled TCP read, not to wait twice as long."""
        url = self.URL + "?" + urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            # sunrise/sunset ride along in the SAME request -- no extra call, no extra rate-limit cost.
            # They are what makes "is it dark right now?" a fact rather than an hour-of-day guess, which
            # matters at this latitude: sunset moves from 17:12 to 19:37 across the year.
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "timezone": tz,                 # day boundaries come back already local -- no conversion here
            "forecast_days": int(days),
        })
        attempts = 2
        deadline = time.monotonic() + float(timeout_s)
        for i in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"weather budget of {timeout_s}s exhausted after {i} attempt(s)")
            per_attempt = min(remaining, max(remaining / (attempts - i), 4.0))
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "LocalAiRobot"})
                with self._urlopen(req, timeout=per_attempt) as resp:
                    payload = json.loads(resp.read().decode("utf-8", "replace"))
                return self._normalize(payload)
            except urllib.error.HTTPError as e:
                _log_http_error(e)          # the BODY names the bad parameter (e.g. an invalid timezone)
                raise
            except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError) as e:
                if i + 1 >= attempts:
                    raise
                log.debug("weather: fetch attempt %d/%d failed (%s) -- retrying on a fresh connection",
                          i + 1, attempts, e)
        raise TimeoutError(f"weather budget of {timeout_s}s exhausted")        # defensive

    def _normalize(self, payload):
        """Open-Meteo JSON -> [{date, condition, tmax, tmin, sunrise?, sunset?}, ...].

        Raises on a response we can't trust. sunrise/sunset are OPTIONAL: they are not part of the
        length check, so a provider that omits them still yields perfectly usable days."""
        daily = (payload or {}).get("daily") or {}
        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        tmaxs = daily.get("temperature_2m_max") or []
        tmins = daily.get("temperature_2m_min") or []
        sunrises = daily.get("sunrise") or []
        sunsets = daily.get("sunset") or []
        n = len(times)
        if not n or not (len(codes) == len(tmaxs) == len(tmins) == n):
            raise ValueError(f"weather: malformed daily arrays (time={n}, code={len(codes)}, "
                             f"max={len(tmaxs)}, min={len(tmins)})")
        out = []
        for i in range(n):
            # A provider may null out an individual day. Skip it rather than rendering a blank column --
            # get() then decides whether enough usable days remain.
            if times[i] is None or tmaxs[i] is None or tmins[i] is None:
                continue
            try:
                day = {"date": str(times[i])[:10],
                       "condition": self.condition_for(codes[i]),
                       "tmax": float(tmaxs[i]),
                       "tmin": float(tmins[i])}
                # OPTIONAL, and treated as such everywhere downstream. A provider that does not supply
                # them, and a disk cache written before this field existed, both just produce days
                # without them -- is_night() then falls back to hour thresholds rather than failing.
                if i < len(sunrises) and sunrises[i]:
                    day["sunrise"] = str(sunrises[i])
                if i < len(sunsets) and sunsets[i]:
                    day["sunset"] = str(sunsets[i])
                out.append(day)
            except (TypeError, ValueError):
                continue
        if not out:
            raise ValueError("weather: no usable days in the response")
        return out


def _log_http_error(e):
    """Log the BODY of a weather HTTP error. The status line alone hides WHY -- Open-Meteo puts the real
    reason ('Invalid timezone', an out-of-range latitude) in the response body, which urllib drops from
    str(e). Never raises: diagnostics must not mask the original error. (inference.py:74 does the same for
    the model backends.)"""
    detail = ""
    try:
        detail = e.read().decode("utf-8", "replace").strip()
    except Exception:
        pass
    log.warning("weather: provider HTTP %s %s -- response body: %s",
                getattr(e, "code", "?"), getattr(e, "reason", ""), detail[:400] or "(empty body)")


# Provider registry -- the hot-swap point. ROBOT_WEATHER_PROVIDER selects by key.
_PROVIDERS = {_OpenMeteoProvider.name: _OpenMeteoProvider}


# --- the service -----------------------------------------------------------------------------------------

class WeatherService:
    """Owns the forecast: fetches it on a daemon thread, caches it to disk, and serves the last-good
    snapshot instantly to everyone else. available=False (and get() -> None) is a fully supported state.

    Construct it even when disabled -- every method is then a cheap no-op -- but main.py only builds one
    when weather.enabled(), so the whole feature costs nothing at all when off."""

    def __init__(self, provider=None, urlopen=None, clock=time.monotonic):
        # available means "I have a forecast worth showing", not "the network is up". False first, set true
        # only once something usable is in hand (the camera.py/pir.py posture).
        self.available = False
        self.configured = False
        self.fetch_ok = 0            # monotonic counters; the summary line reports DELTAS against a base,
        self.fetch_fail = 0          # so nothing ever copies-then-zeroes them out from under a live increment
        self.asks = 0
        self.advice_greetings = 0

        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._days = []              # provider-normalized, ascending by date
        self._fetched_at = 0.0       # wall-clock epoch of the fetch that produced _days
        self._screen_ready = False
        self._warned_unavailable = False
        self._warned_cache_write = False
        self._next_fetch_at = 0.0    # monotonic; inf once a REFRESH_S=0 robot has had its one fetch
        self._last_summary_at = 0.0
        self._summary_base = {}

        self.lat = _env("ROBOT_WEATHER_LAT")
        self.lon = _env("ROBOT_WEATHER_LON")
        self.tz = _env("ROBOT_WEATHER_TZ") or DEFAULT_TZ
        self.refresh_s = max(0.0, _envf("ROBOT_WEATHER_REFRESH_S", 1800))
        self.timeout_s = max(1.0, _envf("ROBOT_WEATHER_TIMEOUT_S", 8))
        self.stale_s = max(0.0, _envf("ROBOT_WEATHER_STALE_S", 21600))
        self.cache_path = os.path.expanduser(
            _env("ROBOT_WEATHER_CACHE") or "~/.localairobot/weather_cache.json")

        want = _env("ROBOT_WEATHER_PROVIDER", _OpenMeteoProvider.name).lower()
        if provider is not None:
            self.provider = provider
        elif want in _PROVIDERS:
            self.provider = _PROVIDERS[want](urlopen=urlopen)
        else:
            self.provider = None
            log.warning("weather: unknown ROBOT_WEATHER_PROVIDER=%r -- known providers are %s. Weather "
                        "stays off (greetings fall back to time-of-day, the sleep face stays Zzz).",
                        want, ", ".join(sorted(_PROVIDERS)))
            return

        if not self._coords_ok():
            log.warning("weather: ROBOT_WEATHER_LAT/LON not set -- weather stays off (greetings fall back "
                        "to time-of-day, and the sleep face stays the normal Zzz). Set both in "
                        "config/robot.env; see docs/weather.md.")
            return
        self.configured = True
        self._load_cache()

    # ---------- configuration ----------
    def _coords_ok(self):
        try:
            float(self.lat)
            float(self.lon)
            return True
        except (TypeError, ValueError):
            return False

    # ---------- disk cache ----------
    def _load_cache(self):
        """Seed the snapshot from the last run's forecast, so a reboot during an internet outage still has
        one. ANY problem -- unreadable, bad JSON, right JSON with the wrong shape, coordinates that no longer
        match the config -- means 'no cache' plus one warning. Never raises."""
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            days = blob["days"]
            fetched_at = float(blob["fetched_at"])
            if not isinstance(days, list) or not days:
                raise ValueError("no days")
            for d in days:                       # shape-check every row before trusting any of it
                if not (isinstance(d, dict) and isinstance(d.get("date"), str)
                        and d.get("condition") in CONDITIONS):
                    raise ValueError(f"bad day row {d!r}")
                float(d["tmax"]); float(d["tmin"])
            # A cache written for different coordinates is somebody else's weather. Discard it silently
            # rather than greeting the household with the forecast for their old address.
            if (str(blob.get("lat", "")), str(blob.get("lon", ""))) != (self.lat, self.lon):
                log.info("weather: cached forecast was for different coordinates -- ignoring it")
                return
        except FileNotFoundError:
            return                                # first run; not a problem and not worth a line
        except Exception as e:
            log.warning("weather: cache at %s unusable (%s) -- starting with no forecast until the first "
                        "successful fetch", self.cache_path, e)
            return
        with self._lock:
            self._days = days
            self._fetched_at = fetched_at
        self.available = self.get() is not None
        log.info("weather: loaded a cached forecast from %s (%.1f h old)%s",
                 self.cache_path, max(0.0, time.time() - fetched_at) / 3600.0,
                 "" if self.available else " -- already too stale to use")

    def _write_cache(self, days, fetched_at):
        try:
            d = os.path.dirname(self.cache_path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"days": days, "fetched_at": fetched_at,
                           "lat": self.lat, "lon": self.lon, "tz": self.tz}, fh)
            os.replace(tmp, self.cache_path)      # atomic: a power cut can't leave a half-written cache
        except Exception as e:
            if not self._warned_cache_write:
                self._warned_cache_write = True
                log.warning("weather: cannot write the cache at %s (%s) -- the forecast still works, it "
                            "just won't survive a reboot", self.cache_path, e)

    # ---------- serving ----------
    def get(self):
        """The ONLY read path. Returns {"days": [today, tomorrow, day_after], "fetched_at", "age_s",
        "stale"} or None. Instant, lock-protected, never touches the network, never raises.

        THE MIDNIGHT RE-INDEX: days are matched by DATE against today in the configured zone, never by array
        position. A forecast fetched at 23:50 has yesterday sitting at days[0] ten minutes later; trusting
        the index would label it "Today" until the next poll -- and with ROBOT_WEATHER_REFRESH_S=0 forever.
        Asking the provider for FORECAST_DAYS=4 is what keeps three full days available after that shift."""
        try:
            with self._lock:
                days, fetched_at = list(self._days), self._fetched_at
            if not days or not fetched_at:
                return None
            age_s = max(0.0, time.time() - fetched_at)
            if self.stale_s and age_s > self.stale_s:
                return None                       # too old to speak aloud -- drop it, don't hedge it
            today = today_iso(self.tz)
            start = next((i for i, d in enumerate(days) if d.get("date") == today), None)
            if start is None or len(days) - start < 3:
                return None                       # today has rolled off the end of what we hold
            return {"days": days[start:start + 3], "fetched_at": fetched_at, "age_s": age_s,
                    "stale": bool(self.refresh_s and age_s > self.refresh_s * 2)}
        except Exception as e:                    # a read path that can raise is a read path that can kill
            log.debug("weather: get() failed (%s)", e)
            return None

    def has_screen(self):
        """_sleep_face()'s predicate ONLY: a forecast exists AND card frames are actually registered on the
        panel. The greeting and the spoken answer test get() instead, so a robot with no face at all
        (ROBOT_FACE_ENABLE=0) still speaks weather-aware greetings."""
        return self._screen_ready and self.get() is not None

    def set_screen_ready(self, ok):
        """Feedback from main.py's on_change closure: did build_frames + fc.set_frames actually succeed?
        WeatherService cannot see the Face, so the closure has to tell it."""
        self._screen_ready = bool(ok)

    # ---------- fetching ----------
    def start(self, on_change=None):
        """Spawn the poll thread. Gated on enabled + configured -- deliberately NOT on the period, because
        even ROBOT_WEATHER_REFRESH_S=0 ("fetch once") still needs an hourly tick to roll the card over at
        midnight. Returns True if a thread was started. Startup NEVER blocks: the first fetch happens on
        this thread, so a dead internet connection costs the robot nothing but a log line."""
        if not (enabled() and self.configured and self.provider is not None):
            return False
        if self._thread is not None:
            return True
        self._thread = threading.Thread(target=self._poll_loop, args=(on_change,),
                                        name="weather-poll", daemon=True)
        self._thread.start()
        log.info("weather: %s forecast for %s,%s (%s) -- refresh %s, serve-stale %.0f min, cache %s",
                 getattr(self.provider, "name", "provider"), self.lat, self.lon, self.tz,
                 f"{self.refresh_s:.0f}s" if self.refresh_s else "once at startup",
                 self.stale_s / 60.0, self.cache_path)
        return True

    def stop(self):
        self._stop.set()

    def _tick_s(self):
        """How long to sleep between cycles. Capped at an hour even when the refresh period is longer (or
        disabled) so the date re-index and the card rebuild still happen across midnight."""
        return min(self.refresh_s, 3600.0) if self.refresh_s > 0 else 3600.0

    def _poll_loop(self, on_change):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as e:                      # refresh() already swallows; belt and braces
                log.debug("weather: refresh raised (%s)", e)
            # on_change fires after EVERY cycle and the snapshot may be None -- that is what lets the card
            # fall back to the Zzz face when a forecast expires, instead of showing yesterday's "Today".
            if on_change is not None:
                try:
                    on_change(self.get())
                except Exception as e:
                    log.warning("weather: on_change hook failed (%s) -- the forecast itself is fine", e)
            self._maybe_summary()
            self._stop.wait(self._tick_s())

    def refresh(self):
        """One poll cycle. Never raises. On failure the previous snapshot keeps serving -- availability only
        drops when the data actually ages out, so a two-minute network blip is invisible."""
        if self._clock() >= self._next_fetch_at:
            try:
                days = self.provider.fetch(self.lat, self.lon, self.tz, FORECAST_DAYS, self.timeout_s)
                fetched_at = time.time()
                with self._lock:
                    self._days = days
                    self._fetched_at = fetched_at
                self._write_cache(days, fetched_at)
                self.fetch_ok += 1
                self._next_fetch_at = ((self._clock() + self.refresh_s) if self.refresh_s > 0
                                       else float("inf"))
                log.debug("weather: fetched %d day(s); today is %s", len(days), days[0].get("date"))
            except Exception as e:
                self.fetch_fail += 1
                # Retry on the NEXT tick rather than the next refresh period, so a robot that boots before
                # the Wi-Fi associates recovers in a minute instead of half an hour.
                self._next_fetch_at = self._clock() + min(self._tick_s(), 300.0)
                log.debug("weather: fetch failed (%s)", e)
        # DELIBERATELY OUTSIDE the "was a fetch due?" branch. Data goes stale on the WALL CLOCK, not on
        # fetch attempts: with ROBOT_WEATHER_REFRESH_S=0 there is exactly one fetch ever (_next_fetch_at
        # becomes inf), and with a period longer than the hourly tick most cycles skip the fetch -- so
        # reconciling only after an attempt would leave `available` stuck True long after get() had started
        # returning None, and the operator would never see the warning that says so.
        self._reconcile_availability()

    def _reconcile_availability(self):
        """Edge-triggered availability: one WARNING on entering the unusable state, one INFO on recovery.
        Never a line per failure -- this repo has demoted a 3,741-lines-a-day logger once already."""
        usable = self.get() is not None
        if usable == self.available:
            return
        self.available = usable
        if usable:
            log.info("weather: forecast available again")
            self._warned_unavailable = False
        elif not self._warned_unavailable:
            self._warned_unavailable = True
            log.warning("weather: no usable forecast (last success %s) -- greetings drop the weather line "
                        "and the sleep screen goes back to the Zzz face until it returns",
                        f"{(time.time() - self._fetched_at) / 3600.0:.1f} h ago"
                        if self._fetched_at else "never")

    # ---------- telemetry ----------
    def note_greeting(self, had_advice):
        if had_advice:
            self.advice_greetings += 1

    def _maybe_summary(self):
        """One INFO line an hour from the poll thread, reporting DELTAS. Weather keeps its own line rather
        than riding the concierge summary: that one is double-gated on concierge mode AND a live PIR
        (main.py), so it never prints on a crawler or a PIR-less robot -- exactly the builds where the
        spoken forecast still works. Quiet hours print nothing."""
        now = self._clock()
        if not self._last_summary_at:
            self._last_summary_at = now
            return
        if now - self._last_summary_at < 3600.0:
            return
        self._last_summary_at = now
        snap = {"fetch_ok": self.fetch_ok, "fetch_fail": self.fetch_fail,
                "asks": self.asks, "advice_greetings": self.advice_greetings}
        seen = {k: snap[k] - self._summary_base.get(k, 0) for k in snap}
        self._summary_base.update(snap)
        if not any(seen.values()):
            return
        snapshot = self.get()
        log.info("weather summary: %d fetch ok, %d failed, %d spoken answer(s), %d greeting(s) with a "
                 "forecast in hand -- currently %s", seen["fetch_ok"], seen["fetch_fail"], seen["asks"],
                 seen["advice_greetings"],
                 f"{snapshot['age_s'] / 60.0:.0f} min old" if snapshot else "UNAVAILABLE")

    # ---------- text surfaces ----------
    def summary_note(self):
        """The one-sentence parenthetical prepended to the LLM context every turn, or "".

        ONE SENTENCE IS A HARD CONSTRAINT, not a style note: when a prompt exceeds ROBOT_NUM_CTX the server
        keeps only the first few tokens and discards the rest, so bloat destroys the front-loaded action
        schema before it costs anything here. This also runs inside handle_command's try/except -- whose
        except SPEAKS the error aloud -- so it must never, ever raise."""
        try:
            snap = self.get()
            if not snap:
                return ""
            place = self.tz.split("/")[-1].replace("_", " ")
            d0, d1, d2 = snap["days"]
            return (f"(Current weather, {place}: today {_r(d0['tmin'])}-{_r(d0['tmax'])}C "
                    f"{_CONDITION_WORDS.get(d0['condition'], 'unsettled')}; "
                    f"tomorrow {_r(d1['tmin'])}-{_r(d1['tmax'])}C "
                    f"{_CONDITION_WORDS.get(d1['condition'], 'unsettled')}; "
                    f"{_weekday(d2['date'])} {_r(d2['tmin'])}-{_r(d2['tmax'])}C "
                    f"{_CONDITION_WORDS.get(d2['condition'], 'unsettled')}.)")
        except Exception as e:
            log.debug("weather: summary_note failed (%s)", e)
            return ""

    def spoken_summary(self):
        """The forecast as ONE spoken utterance, for the WEATHER trigger gate. Also the single source of the
        no-data apology, so the voice path and the HTTP JSON reply say the same thing with no branching at
        the call site. Never raises."""
        self.asks += 1
        try:
            snap = self.get()
            if not snap:
                return ("I can't reach the weather service right now, so I don't have a forecast to give "
                        "you. I'll keep trying in the background.")
            d0, d1, d2 = snap["days"]
            return (f"Today it's {_r(d0['tmin'])} to {_r(d0['tmax'])} degrees and "
                    f"{_CONDITION_WORDS.get(d0['condition'], 'unsettled')}. "
                    f"Tomorrow, {_r(d1['tmin'])} to {_r(d1['tmax'])} and "
                    f"{_CONDITION_WORDS.get(d1['condition'], 'unsettled')}. "
                    f"{_weekday(d2['date'])}, {_r(d2['tmin'])} to {_r(d2['tmax'])} and "
                    f"{_CONDITION_WORDS.get(d2['condition'], 'unsettled')}.")
        except Exception as e:
            log.debug("weather: spoken_summary failed (%s)", e)
            return "I'm having trouble reading the forecast just now."


def _r(v):
    """Whole degrees for display and speech. The advice bands deliberately use the UNROUNDED max."""
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _weekday(iso):
    """'2026-08-10' -> 'Monday'. Pure date arithmetic, so no timezone is involved."""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%A")
    except (TypeError, ValueError):
        return "the day after"


# --- greeting text ---------------------------------------------------------------------------------------
# Same convention as concierge.py: module-level arrays under banner comments, one random.choice per use,
# "[Name]" substituted by the caller's spoken name. A greeting is an OPENER plus (when there is a forecast)
# exactly ONE advice sentence -- never two, and never a weather report.
#
# WHY THERE ARE SEVEN ADVICE SETS AND NOT SEVEN "CONDITIONS". The seven CONDITIONS are ICON types, chosen
# for what a picture can show. Advice answers a different question -- "what should I do about it?" -- and the
# two do not line up:
#   * The three DRY conditions (Sunny/PartlyCloudy/Cloudy) are useless as advice on their own: "it's cloudy"
#     tells nobody whether to wear a coat. They split by TEMPERATURE instead -> CHILLY / PLEASANT / HOT.
#   * The three WET conditions genuinely ARE three different days, so they each keep their own voice ->
#     SUNNY_RAIN (a sun-shower you'll walk through), SHOWERS (dodgeable), RAIN (wet start to finish).
#   * Thunderstorms stands alone, because the advice is shelter, not an umbrella.
# Seven sets, seven meaningfully different things to say.
#
# WORDING NOTE: everything spoken passes through main._fix_pronunciation, a regex over
# ROBOT_TTS_PRONUNCIATIONS whose keys are people's NAMES. These lines avoid name-like words for that reason.
#
# SIZE: 50 lines per array. A hallway greeter says these to the same handful of people every single day, so
# small arrays are heard out fast -- 8 advice lines is a fortnight before someone notices the loop. At 50
# openers x 50 advice, one weather situation in one time band has 2,500 distinct greetings.

# --- Array 1: Morning openers ([Name]) ---
MORNING_OPENERS = [
    "Good morning, [Name]!",
    "Morning, [Name]! Lovely to see you.",
    "Good morning to you, [Name]!",
    "Ah, good morning, [Name]!",
    "Morning, [Name]!",
    "Good morning, [Name] -- you're up and about.",
    "Well, good morning, [Name]!",
    "Morning, [Name]! Hope you slept well.",
    "Good morning, [Name]. Off to a fine start, I hope.",
    "A very good morning to you, [Name]!",
    "Morning, [Name]! Bright and early.",
    "Good morning, [Name]! Ready for the day?",
    "Ah, there you are, [Name]. Good morning!",
    "Morning to you, [Name]!",
    "Good morning, [Name] -- nice to start the day with a familiar face.",
    "Hello and good morning, [Name]!",
    "Morning, [Name]. Sleep well?",
    "Good morning, [Name]! You're looking well.",
    "Why, good morning, [Name]!",
    "Morning, [Name]! Another day begins.",
    "Good morning, [Name]. Lovely to have you about.",
    "Top of the morning, [Name]!",
    "Morning, [Name] -- the day's all yours.",
    "Good morning, [Name]! What a way to start.",
    "Ah, [Name]. Good morning to you.",
    "Morning, [Name]! Hope it's a good one.",
    "Good morning, [Name] -- you're an early bird.",
    "Well hello, [Name], and good morning!",
    "Morning, [Name]. Here's to a good day.",
    "Good morning, [Name]! Always a pleasure this early.",
    "Rise and shine, [Name]! Good morning.",
    "Morning, [Name]! Good to see you up.",
    "Good morning, [Name]. How did you sleep?",
    "Ah, good morning, [Name] -- right on time.",
    "Morning, [Name]! Let's make it a good one.",
    "Good morning, [Name]! The day awaits.",
    "Hello, [Name], and a very good morning.",
    "Morning, [Name] -- glad you're up.",
    "Good morning, [Name]! Fresh start, fresh day.",
    "Ah, [Name]! Morning to you.",
    "Good morning, [Name]. Hope the night treated you kindly.",
    "Morning, [Name]! Nice and early.",
    "Good morning, [Name] -- lovely to see you first thing.",
    "Well, if it isn't [Name]. Good morning!",
    "Morning, [Name]! All set for the day?",
    "Good morning, [Name]! Delighted, as always.",
    "Ah, morning, [Name]. Good to see you.",
    "Good morning, [Name] -- a fine time to be up.",
    "Morning, [Name]! Hope today's a kind one.",
    "A warm good morning to you, [Name]!",
]

# --- Array 2: Afternoon openers ([Name]) ---
AFTERNOON_OPENERS = [
    "Good afternoon, [Name]!",
    "Afternoon, [Name]! Good to see you.",
    "Good afternoon to you, [Name]!",
    "Ah, [Name] -- good afternoon!",
    "Afternoon, [Name]!",
    "Good afternoon, [Name]. How's the day treating you?",
    "Well, good afternoon, [Name]!",
    "Afternoon, [Name]! Hope it's going well.",
    "Good afternoon, [Name] -- nice timing.",
    "A pleasant afternoon to you, [Name]!",
    "Afternoon, [Name]! Halfway through already.",
    "Good afternoon, [Name]! Busy day?",
    "Ah, there you are, [Name]. Good afternoon!",
    "Afternoon to you, [Name]!",
    "Good afternoon, [Name] -- lovely to see you.",
    "Hello and good afternoon, [Name]!",
    "Afternoon, [Name]. Day going to plan?",
    "Good afternoon, [Name]! You're looking well.",
    "Why, good afternoon, [Name]!",
    "Afternoon, [Name]! The day's flying by.",
    "Good afternoon, [Name]. Always a pleasure.",
    "Afternoon, [Name] -- how goes it?",
    "Good afternoon, [Name]! Perfect timing, as ever.",
    "Ah, [Name]. Good afternoon to you.",
    "Afternoon, [Name]! Hope it's been kind so far.",
    "Good afternoon, [Name] -- taking a break?",
    "Well hello, [Name], and good afternoon!",
    "Afternoon, [Name]. Good to have you by.",
    "Good afternoon, [Name]! Nice of you to drop in.",
    "Afternoon, [Name]! Everything alright?",
    "Good afternoon, [Name]. Keeping busy?",
    "Ah, good afternoon, [Name] -- right on cue.",
    "Afternoon, [Name]! Let's keep it a good one.",
    "Good afternoon, [Name]! Plenty of day left.",
    "Hello, [Name], and a very good afternoon.",
    "Afternoon, [Name] -- glad you stopped by.",
    "Good afternoon, [Name]! Lovely part of the day.",
    "Ah, [Name]! Afternoon to you.",
    "Good afternoon, [Name]. Hope the morning was kind.",
    "Afternoon, [Name]! Good to see a familiar face.",
    "Good afternoon, [Name] -- always welcome.",
    "Well, if it isn't [Name]. Good afternoon!",
    "Afternoon, [Name]! Getting much done?",
    "Good afternoon, [Name]! Delighted, as always.",
    "Ah, afternoon, [Name]. Good to see you.",
    "Good afternoon, [Name] -- a fine time of day.",
    "Afternoon, [Name]! Hope it stays a good one.",
    "A warm good afternoon to you, [Name]!",
    "Good afternoon, [Name]! Anything I can do?",
    "Afternoon, [Name] -- what brings you by?",
]

# --- Array 3: Evening openers ([Name]) ---
EVENING_OPENERS = [
    "Good evening, [Name]!",
    "Evening, [Name]! Good to see you.",
    "Good evening to you, [Name]!",
    "Ah, good evening, [Name]!",
    "Evening, [Name]!",
    "Good evening, [Name]. Winding down?",
    "Well, good evening, [Name]!",
    "Evening, [Name]! Hope it's been a good one.",
    "Good evening, [Name] -- welcome back.",
    "A very good evening to you, [Name]!",
    "Evening, [Name]! Long day?",
    "Good evening, [Name]! Home at last.",
    "Ah, there you are, [Name]. Good evening!",
    "Evening to you, [Name]!",
    "Good evening, [Name] -- lovely to see you.",
    "Hello and good evening, [Name]!",
    "Evening, [Name]. Day go alright?",
    "Good evening, [Name]! Time to put your feet up.",
    "Why, good evening, [Name]!",
    "Evening, [Name]! That's the day done.",
    "Good evening, [Name]. Always a pleasure.",
    "Evening, [Name] -- how was it?",
    "Good evening, [Name]! Perfect timing.",
    "Ah, [Name]. Good evening to you.",
    "Evening, [Name]! Hope today was kind.",
    "Good evening, [Name] -- settling in?",
    "Well hello, [Name], and good evening!",
    "Evening, [Name]. Good to have you back.",
    "Good evening, [Name]! Nice to see you home.",
    "Evening, [Name]! Everything alright?",
    "Good evening, [Name]. Busy one today?",
    "Ah, good evening, [Name] -- right on time.",
    "Evening, [Name]! Let's end it well.",
    "Good evening, [Name]! The day's earned its rest.",
    "Hello, [Name], and a very good evening.",
    "Evening, [Name] -- glad you're back.",
    "Good evening, [Name]! Quiet part of the day.",
    "Ah, [Name]! Evening to you.",
    "Good evening, [Name]. Hope the day was a good one.",
    "Evening, [Name]! Good to see a familiar face.",
    "Good evening, [Name] -- always welcome.",
    "Well, if it isn't [Name]. Good evening!",
    "Evening, [Name]! Get much done today?",
    "Good evening, [Name]! Delighted, as always.",
    "Ah, evening, [Name]. Good to see you.",
    "Good evening, [Name] -- a peaceful time of day.",
    "Evening, [Name]! Hope you can relax now.",
    "A warm good evening to you, [Name]!",
    "Good evening, [Name]! Anything I can do?",
    "Evening, [Name] -- back at last.",
]

# --- Array 4: Morning openers (name unknown) ---
MORNING_OPENERS_UNKNOWN = [
    "Good morning!",
    "Morning! Lovely to see you.",
    "Good morning to you!",
    "Ah, good morning!",
    "Morning there!",
    "Good morning -- you're up and about.",
    "Well, good morning!",
    "Morning! Hope you slept well.",
    "Good morning. Off to a fine start, I hope.",
    "A very good morning to you!",
    "Morning! Bright and early.",
    "Good morning! Ready for the day?",
    "Ah, hello there. Good morning!",
    "Morning to you!",
    "Good morning -- nice to start the day with a visitor.",
    "Hello and good morning!",
    "Morning! Sleep well?",
    "Good morning! You're looking well.",
    "Why, good morning!",
    "Morning! Another day begins.",
    "Good morning. Lovely to have someone about.",
    "Top of the morning to you!",
    "Morning -- the day's all yours.",
    "Good morning! What a way to start.",
    "Ah, good morning to you.",
    "Morning! Hope it's a good one.",
    "Good morning -- you're an early one.",
    "Well hello, and good morning!",
    "Morning. Here's to a good day.",
    "Good morning! Always a pleasure this early.",
    "Rise and shine! Good morning.",
    "Morning! Good to see someone up.",
    "Good morning. How did you sleep?",
    "Ah, good morning -- right on time.",
    "Morning! Let's make it a good one.",
    "Good morning! The day awaits.",
    "Hello, and a very good morning.",
    "Morning -- glad you're up.",
    "Good morning! Fresh start, fresh day.",
    "Ah, morning to you!",
    "Good morning. Hope the night treated you kindly.",
    "Morning! Nice and early.",
    "Good morning -- lovely to see someone first thing.",
    "Well, hello! Good morning.",
    "Morning! All set for the day?",
    "Good morning! Delighted, as always.",
    "Ah, morning. Good to see you.",
    "Good morning -- a fine time to be up.",
    "Morning! Hope today's a kind one.",
    "A warm good morning to you!",
]

# --- Array 5: Afternoon openers (name unknown) ---
AFTERNOON_OPENERS_UNKNOWN = [
    "Good afternoon!",
    "Afternoon! Good to see you.",
    "Good afternoon to you!",
    "Ah -- good afternoon!",
    "Afternoon there!",
    "Good afternoon. How's the day treating you?",
    "Well, good afternoon!",
    "Afternoon! Hope it's going well.",
    "Good afternoon -- nice timing.",
    "A pleasant afternoon to you!",
    "Afternoon! Halfway through already.",
    "Good afternoon! Busy day?",
    "Ah, hello there. Good afternoon!",
    "Afternoon to you!",
    "Good afternoon -- lovely to see you.",
    "Hello and good afternoon!",
    "Afternoon. Day going to plan?",
    "Good afternoon! You're looking well.",
    "Why, good afternoon!",
    "Afternoon! The day's flying by.",
    "Good afternoon. Always a pleasure.",
    "Afternoon -- how goes it?",
    "Good afternoon! Perfect timing, as ever.",
    "Ah, good afternoon to you.",
    "Afternoon! Hope it's been kind so far.",
    "Good afternoon -- taking a break?",
    "Well hello, and good afternoon!",
    "Afternoon. Good to have you by.",
    "Good afternoon! Nice of you to drop in.",
    "Afternoon! Everything alright?",
    "Good afternoon. Keeping busy?",
    "Ah, good afternoon -- right on cue.",
    "Afternoon! Let's keep it a good one.",
    "Good afternoon! Plenty of day left.",
    "Hello, and a very good afternoon.",
    "Afternoon -- glad you stopped by.",
    "Good afternoon! Lovely part of the day.",
    "Ah, afternoon to you!",
    "Good afternoon. Hope the morning was kind.",
    "Afternoon! Good to see a new face.",
    "Good afternoon -- always welcome.",
    "Well, hello! Good afternoon.",
    "Afternoon! Getting much done?",
    "Good afternoon! Delighted, as always.",
    "Ah, afternoon. Good to see you.",
    "Good afternoon -- a fine time of day.",
    "Afternoon! Hope it stays a good one.",
    "A warm good afternoon to you!",
    "Good afternoon! Anything I can do?",
    "Afternoon -- what brings you by?",
]

# --- Array 6: Evening openers (name unknown) ---
EVENING_OPENERS_UNKNOWN = [
    "Good evening!",
    "Evening! Good to see you.",
    "Good evening to you!",
    "Ah, good evening!",
    "Evening there!",
    "Good evening. Winding down?",
    "Well, good evening!",
    "Evening! Hope it's been a good one.",
    "Good evening -- welcome in.",
    "A very good evening to you!",
    "Evening! Long day?",
    "Good evening! Home at last, I hope.",
    "Ah, hello there. Good evening!",
    "Evening to you!",
    "Good evening -- lovely to see you.",
    "Hello and good evening!",
    "Evening. Day go alright?",
    "Good evening! Time to put your feet up.",
    "Why, good evening!",
    "Evening! That's the day done.",
    "Good evening. Always a pleasure.",
    "Evening -- how was it?",
    "Good evening! Perfect timing.",
    "Ah, good evening to you.",
    "Evening! Hope today was kind.",
    "Good evening -- settling in?",
    "Well hello, and good evening!",
    "Evening. Good to have you by.",
    "Good evening! Nice to see you.",
    "Evening! Everything alright?",
    "Good evening. Busy one today?",
    "Ah, good evening -- right on time.",
    "Evening! Let's end it well.",
    "Good evening! The day's earned its rest.",
    "Hello, and a very good evening.",
    "Evening -- glad you stopped by.",
    "Good evening! Quiet part of the day.",
    "Ah, evening to you!",
    "Good evening. Hope the day was a good one.",
    "Evening! Good to see a new face.",
    "Good evening -- always welcome.",
    "Well, hello! Good evening.",
    "Evening! Get much done today?",
    "Good evening! Delighted, as always.",
    "Ah, evening. Good to see you.",
    "Good evening -- a peaceful time of day.",
    "Evening! Hope you can relax now.",
    "A warm good evening to you!",
    "Good evening! Anything I can do?",
    "Evening -- what brings you by?",
]

# --- Array 7: CHILLY -- dry, today's max at or below ROBOT_WEATHER_CHILLY_C ---
ADVICE_CHILLY = [
    "It's a very chilly day today, isn't it? Make sure to pack your jumper.",
    "It's a cold one out there -- worth grabbing a jacket before you go.",
    "Rather nippy today, so I'd wear something warm.",
    "It's staying cold all day, so a proper coat wouldn't hurt.",
    "Bit of a shivery one today -- don't forget your jumper.",
    "It's chilly out, so wrap up warm before you head off.",
    "Cold air today. A scarf would not be an overreaction.",
    "It's a jumper sort of day, by the look of the forecast.",
    "Quite cold today, so layer up before you go out.",
    "The cold's settled in today -- best bring a coat.",
    "It's a brisk one out there. Something with sleeves, I'd suggest.",
    "Chilly all day today, so don't be caught in shirtsleeves.",
    "It's cold enough for gloves today, honestly.",
    "Rather a cold day ahead -- a warm layer would be wise.",
    "It's not warming up today, so dress for the cold.",
    "Cold out there today. Your future self will thank you for a coat.",
    "It's a proper jumper day -- the sort you regret skipping.",
    "Quite the chill today, so wrap up before you head out.",
    "It stays cold right through today. Coat weather, I'm afraid.",
    "There's a bite in the air today -- something warm, I'd say.",
    "A cold one today, so don't underdress on my account.",
    "It's chilly enough to notice today. Bring a layer.",
    "Cold today, top to bottom. Warm socks, even.",
    "It's the sort of cold that creeps up on you -- take a coat.",
    "Distinctly cold today, so a jumper at the very least.",
    "It won't get warm today, so plan for the chill.",
    "Cold weather today -- a jacket would be sensible.",
    "It's a shivery sort of day. Do wrap up.",
    "Rather cold out. I'd not leave without a coat.",
    "The chill is staying put today, so dress accordingly.",
    "It's cold enough that a scarf earns its keep today.",
    "A cold day ahead -- something cosy, I think.",
    "It stays chilly today, so layers are your friend.",
    "Cold one today. Don't let any sunshine fool you.",
    "It's brisk out there. A warm coat, if you have one handy.",
    "Chilly today, so I'd keep the jumper on.",
    "A cold day in store -- best bundle up.",
    "It's properly cold today. Hat weather, even.",
    "The cold's here to stay today, so dress warm.",
    "Rather chilly. I'd take a jacket if I were heading out.",
    "It's a cold sort of day, so mind the draughts.",
    "Cold air all day today -- warm layers, please.",
    "It's nippy enough to notice. Do take a coat.",
    "A chilly one today, so nothing too optimistic, wardrobe-wise.",
    "It stays cold today, so don't go out in just a shirt.",
    "Cold today -- the kind that gets into your bones.",
    "It's a wrap-up-warm day, by the forecast.",
    "Quite cold out there, so a jumper is the least of it.",
    "The day stays cold, so plan your layers now.",
    "It's cold enough that I'd bring a coat, wheels or no wheels.",
]

# --- Array 8: PLEASANT -- dry, between the two temperature bands ---
ADVICE_PLEASANT = [
    "It's looking like a lovely day out there.",
    "Rather a nice day today, by the forecast.",
    "The weather's being agreeable today, for once.",
    "A pleasant one ahead -- good day to be outside.",
    "Nothing to complain about in the forecast today.",
    "It's a comfortable sort of day out there.",
    "Fine weather today, happily.",
    "Looks like a rather good day for it.",
    "A mild one today -- nothing to plan around.",
    "The forecast is being kind to us today.",
    "It's an easy sort of weather day. No fuss needed.",
    "Nice and mild today. Do enjoy it.",
    "A very reasonable day out there, weather-wise.",
    "Today looks pleasant -- make the most of it.",
    "Mild and settled today. Rather nice.",
    "The weather's behaving itself today.",
    "A good day to be out and about, by the look of it.",
    "Perfectly decent weather today.",
    "It's shaping up to be a pleasant one.",
    "Nothing dramatic in today's forecast -- just a nice day.",
    "Comfortable out there today. No coat required.",
    "A gentle sort of day, weather-wise.",
    "Today's forecast is entirely agreeable.",
    "Mild today. The sort of day that asks nothing of you.",
    "Rather lovely out, actually.",
    "A fine day ahead -- enjoy it while it lasts.",
    "Pleasant weather today. Nothing to guard against.",
    "It's a nice one out there today.",
    "The day looks kind, weather-wise.",
    "A lovely middle-of-the-road day. My favourite sort.",
    "Settled and mild today. Very civilised.",
    "Good weather today. No notes.",
    "It's a nice enough day to linger outside.",
    "Today's looking rather pleasant, I'm glad to say.",
    "Mild all day. Nothing to think about.",
    "A pleasant day ahead. Do enjoy it.",
    "The forecast is quietly good today.",
    "Nice weather out there. Take advantage.",
    "A comfortable day, top to bottom.",
    "Today should be a good one, weather and all.",
    "Rather agreeable out there today.",
    "Fine and mild. Hard to ask for more.",
    "A perfectly pleasant day in store.",
    "Nothing to worry about in the sky today.",
    "It's a good day for whatever you had planned.",
    "Mild weather today. Easy going.",
    "A nice day out there, by all accounts.",
    "The weather's on your side today.",
    "Pleasant all day, by the forecast.",
    "A lovely day ahead. I'd be out in it, if I had legs.",
]

# --- Array 9: HOT -- dry, today's max at or above ROBOT_WEATHER_HOT_C ---
ADVICE_HOT = [
    "It's going to be a hot one today, so keep your water bottle handy.",
    "Quite the scorcher today -- sunscreen and shade, I'd say.",
    "It's hot out there, so do drink plenty of water.",
    "A properly warm day ahead. Hat and sunscreen if you're outside.",
    "It's set to be very hot today, so take it easy in the sun.",
    "Hot one on the way -- stay in the shade where you can.",
    "It'll be sweltering later, so plenty of water please.",
    "A real summer day today. Sunscreen before you head out.",
    "It's going to be a warm one, so keep hydrated.",
    "Hot today. Don't underestimate the sun.",
    "A blazing sort of day. Water, hat, shade.",
    "It gets properly hot today, so pace yourself.",
    "Very warm today -- I'd avoid the midday sun.",
    "A hot day ahead. Keep something cold to hand.",
    "It's a sunscreen day, no question.",
    "Heat's building today, so drink more than you think you need.",
    "Sweltering later on -- do find some shade.",
    "It's going to be roasting. Do look after yourself.",
    "A hot one. Light clothes and plenty of water.",
    "Serious heat today, so take regular breaks in the cool.",
    "It's hot enough to matter today. Hydrate.",
    "Very warm out there -- sunscreen if you're going anywhere.",
    "A scorching day ahead. Mind the heat.",
    "It'll be uncomfortably warm, so plan around the midday.",
    "Hot today. Keep the water close.",
    "A properly hot day -- shade is your friend.",
    "It's going to bake out there. A hat, at minimum.",
    "Very hot today, so go easy if you're outside.",
    "A hot day in store. Don't skip the sunscreen.",
    "It's the sort of heat that sneaks up. Drink water.",
    "Warm to hot today -- dress light.",
    "It's a proper summer scorcher. Stay cool.",
    "Hot weather today, so keep out of the direct sun.",
    "A blazing day ahead. Water bottle, please.",
    "It gets very warm today -- take it slowly.",
    "Heat today, and plenty of it. Look after yourself.",
    "A hot one out there. Shade and water.",
    "It'll be hot enough to slow you down today.",
    "Very warm all day. Hydrate early and often.",
    "A scorcher. I'd stay somewhere cool if I could move.",
    "It's hot today, so mind the sun in the middle of the day.",
    "Real heat today. Sunscreen, hat, water -- the full set.",
    "A hot day ahead, so keep something cold nearby.",
    "It's going to be a warm one. Don't overdo it.",
    "Baking today. Shade wherever you can find it.",
    "Hot out there -- light clothing would be sensible.",
    "A very warm day. Do keep drinking water.",
    "It's a hot one, so take the cool route if there is one.",
    "Serious warmth today. Sunscreen before you step out.",
    "Hot today. Your water bottle will earn its keep.",
]

# --- Array 10: SUNNY_RAIN -- the convective sun-shower (WMO 80) ---
ADVICE_SUNNY_RAIN = [
    "Sunshine and showers today -- take the umbrella anyway.",
    "It's a sun-and-rain sort of day, so don't trust the blue sky.",
    "Bright with showers about. Bring a brolly regardless.",
    "Sunny one moment, wet the next -- pack the umbrella.",
    "It's that sunshine-and-showers weather. Be ready for both.",
    "Lovely and bright, but showers are coming. Take cover with you.",
    "A sun-shower day. Don't let the sunshine fool you.",
    "Sun and rain both today, so the umbrella earns its place.",
    "Bright spells with showers -- worth carrying a brolly.",
    "It'll be sunny and wet today. Plan for the wet bit.",
    "Sunshine between the showers today. Take the umbrella.",
    "Deceptive weather today -- sunny, then suddenly not.",
    "Sun-showers on the way. Bring something waterproof.",
    "It's bright out, but rain is coming. Be prepared.",
    "A mix of sun and showers today, so don't get caught out.",
    "Sunny with wet interruptions. An umbrella, I'd say.",
    "Bright but showery today. Pack accordingly.",
    "It's a rainbow sort of day -- sun and rain together.",
    "Sunshine, then a soaking, then sunshine again. Take a brolly.",
    "Showers in the sunshine today. Do bring cover.",
    "It looks lovely and it will rain anyway. Umbrella.",
    "Sun and showers both. Dress for the second one.",
    "Bright day with wet patches. Carry something waterproof.",
    "Sunny spells and sharp showers today. Be ready.",
    "It'll be sunny between the wet bits. Take an umbrella.",
    "Deceptively nice today -- showers are forecast too.",
    "Sunshine with a side of rain. Pack the brolly.",
    "A bright but showery one. Don't leave the umbrella behind.",
    "Sun and rain are taking turns today. Prepare for both.",
    "It's lovely now, but showers are coming. Take cover.",
    "Bright with passing showers -- a brolly wouldn't hurt.",
    "Sunny and wet in equal measure today.",
    "The sun's out, but so is the rain. An umbrella, please.",
    "Showers between the sunshine. Bring something waterproof.",
    "A classic sun-shower day. Be ready for a quick soaking.",
    "It'll look fine right up until it doesn't. Take a brolly.",
    "Sunshine and showers -- the umbrella stays with you.",
    "Bright but unreliable today. Pack for rain.",
    "Sun with scattered showers. Don't be caught out.",
    "It's sunny and showery. Both, genuinely.",
    "Lovely light, occasional soaking. An umbrella is advised.",
    "Sun-shower weather today. Do carry cover.",
    "Bright skies with showers mixed in. Be prepared.",
    "The sunshine is real, and so is the rain. Take a brolly.",
    "Showers punctuating the sunshine today.",
    "It's a both-at-once sort of day. An umbrella, I think.",
    "Sunny with sudden showers -- don't trust it.",
    "Bright and wet by turns today. Pack the waterproof.",
    "Sun and showers today, so keep a brolly to hand.",
    "It'll be lovely, briefly, and then wet. Take an umbrella.",
]

# --- Array 11: SHOWERS -- broken sky, dodgeable rain (WMO 51, 61, 81) ---
ADVICE_SHOWERS = [
    "Passing showers today -- you'll dodge most of them, but take a brolly.",
    "Scattered showers about today. Worth carrying an umbrella.",
    "A few showers coming through, so keep cover to hand.",
    "It'll be showery on and off today. Nothing a brolly won't fix.",
    "Showers in and out today, so don't get caught between them.",
    "Intermittent rain today -- an umbrella would be sensible.",
    "A showery sort of day. You'll want something waterproof.",
    "Showers passing through today. Pack a brolly, just in case.",
    "On-and-off rain today, so keep an umbrella nearby.",
    "A few wet spells today. Nothing dramatic, but take cover.",
    "Showery today -- the sort you can mostly walk between.",
    "Rain comes and goes today. An umbrella will do it.",
    "Scattered wet patches today, so be ready for one.",
    "Showers likely today. A brolly wouldn't go amiss.",
    "It'll rain in bursts today. Carry something waterproof.",
    "A showery day, so keep the umbrella within reach.",
    "Occasional showers today -- easy to dodge, easier with a brolly.",
    "Wet spells on the way. Pack accordingly.",
    "Showers about today, so don't leave without cover.",
    "It's a brolly-in-the-bag sort of day.",
    "A few showers forecast. Worth being prepared.",
    "Showery weather today, though it won't rain all day.",
    "Rain in patches today. An umbrella covers it.",
    "Showers coming and going -- take something waterproof.",
    "It'll be wet at times today. Nothing a brolly can't handle.",
    "Passing rain today, so keep cover close.",
    "A showery one. You may stay dry, but I wouldn't bet on it.",
    "Scattered showers today. An umbrella, to be safe.",
    "On-off rain forecast, so pack a brolly.",
    "Showers likely later. Best to be ready.",
    "It'll rain a few times today, briefly. Take cover with you.",
    "Wet intervals today, so a brolly is worth carrying.",
    "Showery skies today. Prepare for a quick one.",
    "Rain in short bursts today. An umbrella will do.",
    "A few showers about -- nothing to plan around, but take a brolly.",
    "Intermittent showers today. Something waterproof, I'd suggest.",
    "It'll be showery, but between them it's fine.",
    "Passing showers forecast. Keep an umbrella handy.",
    "Wet at intervals today. Carry cover, just in case.",
    "Showers around today, so don't be caught out.",
    "A hit-and-miss rain day. A brolly is recommended.",
    "Occasional wet spells today. Nothing serious.",
    "Showers dotted through the day. Take an umbrella.",
    "It'll rain now and then today, so be ready.",
    "Scattered rain about. An umbrella is the safe call.",
    "Showery today -- worth packing something waterproof.",
    "Rain on and off. Keep the brolly with you.",
    "A few showers due. Take cover, and you'll be fine.",
    "Wet patches today, but not persistently.",
    "Showers likely -- an umbrella will see you through.",
]

# --- Array 12: RAIN -- steady, wet start to finish (WMO 53-67, 82, and the snow family) ---
ADVICE_RAIN = [
    "It's set to rain properly today, so take something genuinely waterproof.",
    "Steady rain all day today. A raincoat, not just a brolly.",
    "It's going to be wet right through -- dress for it.",
    "Persistent rain today. I'd take a proper coat.",
    "A thoroughly wet day ahead. Waterproof everything.",
    "It rains most of today, so plan around it.",
    "Solid rain today -- an umbrella alone won't cut it.",
    "It's a properly wet one. Waterproof shoes, even.",
    "Rain all day today. Do take a raincoat.",
    "A soggy day ahead, so dress seriously for it.",
    "It's raining through today, so nothing you mind getting wet.",
    "Steady wet weather today. A brolly and a coat.",
    "Persistent rain -- it won't clear, I'm afraid.",
    "A wet day, start to finish. Prepare properly.",
    "It's going to pour today, more or less continuously.",
    "Rain settled in for the day. Take the good coat.",
    "Thoroughly wet today -- waterproofs, not optimism.",
    "It's a stay-dry-if-you-can sort of day.",
    "Continuous rain today. Boots wouldn't be silly.",
    "A properly rainy day. Dress like you mean it.",
    "It rains all day today, so no point pretending otherwise.",
    "Heavy and persistent today. Full waterproofs.",
    "A wet one, right through. Take a raincoat.",
    "It's raining and it's staying. Prepare accordingly.",
    "Solid wet weather today -- an umbrella is the minimum.",
    "A drenching sort of day. Do waterproof up.",
    "Rain for most of today. I'd not leave without a coat.",
    "It's going to be properly wet. Dress for a soaking.",
    "Steady rain today, so plan indoor things if you can.",
    "A very wet day ahead. Take the serious coat.",
    "It rains through today. Umbrella, coat, the lot.",
    "Persistent wet today, so nothing that suffers in the rain.",
    "A soaking day. Waterproof everything you can.",
    "Rain all through today -- best be properly equipped.",
    "It's a wet one. A brolly will help; a coat will help more.",
    "Constant rain today. Do stay dry if you're able.",
    "A properly damp day. Waterproofs are recommended.",
    "It won't stop raining today, so dress for that.",
    "Wet weather settled in. Take the good jacket.",
    "Rain right through the day. Prepare thoroughly.",
    "A drizzly, persistent one. Waterproof layers.",
    "It's raining steadily today. Coat and brolly both.",
    "Thoroughly wet out there. Nothing you mind ruining.",
    "A rainy day, properly so. Dress for the worst of it.",
    "It rains and keeps raining today. Be ready.",
    "Solid rain -- an indoor sort of day, if you have the choice.",
    "Wet through today, so waterproof footwear too.",
    "Rain all day. I'd take the umbrella and the coat.",
    "A very wet day in store. Prepare properly, please.",
    "It's going to be a damp one, so bring an umbrella.",
]

# --- Array 13: STORM -- thunderstorms, where the advice is shelter and not an umbrella ---
ADVICE_STORM = [
    "Storms are forecast today, so maybe keep the outdoor plans flexible.",
    "There's thunder about today -- worth staying near shelter.",
    "Thunderstorms on the way, so do be careful out there.",
    "It's a stormy one today. I'd keep an eye on the sky.",
    "Storms are expected later, so plan around them if you can.",
    "Thunder is forecast today -- best not to be caught outside.",
    "Rough weather ahead today, with storms about.",
    "Storms today, so take care if you're heading out.",
    "Thunderstorms are likely -- keep shelter within reach.",
    "It could turn stormy today, so stay alert.",
    "Storms forecast. I'd not plan anything outdoors that can't move.",
    "Thunder and lightning about today. Do be sensible.",
    "A stormy day ahead -- keep an eye on the weather.",
    "Storms expected today, so nothing exposed if you can help it.",
    "Thundery weather today. Shelter beats a brolly.",
    "It's a storm day. Best stay somewhere solid.",
    "Storms about -- worth delaying the outdoor plans.",
    "Thunderstorms forecast, so stay somewhere sheltered.",
    "Rough sky today. I'd keep indoors when it hits.",
    "Storms today, and they can come on fast. Be ready.",
    "Thunder likely today -- keep your plans loose.",
    "A stormy forecast, so mind the weather later on.",
    "Storms coming through. Do take care out there.",
    "It'll be thundery today. Shelter is the sensible call.",
    "Storms about today -- an umbrella won't save you from those.",
    "Thundery and rough today. Best stay close to cover.",
    "Storms forecast, so keep the day flexible.",
    "There's a storm in today's forecast. Plan for it.",
    "Thunderstorms are likely -- I'd stay in when it turns.",
    "A rough one today. Storms about, so be careful.",
    "Storms expected. Nothing outdoors that can't be moved.",
    "Thunder today, so keep somewhere solid within reach.",
    "It's storm weather -- keep an eye on the sky.",
    "Storms on the way today. Do take it seriously.",
    "Thundery today, so be ready to take shelter.",
    "A stormy forecast today. Plans may need shuffling.",
    "Storms are due today, so keep your options open.",
    "Thunder about -- best not to be out in the open.",
    "Rough, stormy weather today. Stay safe out there.",
    "Storms today. I'd keep the day's plans indoors.",
    "Thunderstorms coming. Shelter, not just an umbrella.",
    "It's set to storm today, so be prepared to duck inside.",
    "Storms in the forecast. Take care if you go out.",
    "Thundery weather ahead. Keep near cover.",
    "A storm's forecast today, so mind how you go.",
    "Storms about today -- worth keeping plans adaptable.",
    "Thunder and rain together today. Stay sheltered.",
    "It's a stormy day ahead. Do be careful.",
    "Storms are likely today, so keep an eye out.",
    "Thunderstorms forecast -- shelter beats getting caught.",
]


def _band(hour):
    """5-11 morning, 12-16 afternoon, 17-4 evening."""
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    return "evening"


_OPENERS = {
    ("morning", True):    MORNING_OPENERS,
    ("afternoon", True):  AFTERNOON_OPENERS,
    ("evening", True):    EVENING_OPENERS,
    ("morning", False):   MORNING_OPENERS_UNKNOWN,
    ("afternoon", False): AFTERNOON_OPENERS_UNKNOWN,
    ("evening", False):   EVENING_OPENERS_UNKNOWN,
}

# The WET conditions each get their own voice -- a sun-shower, a dodgeable shower and an all-day soaking are
# three different days and deserve three different sentences. The DRY conditions are absent on purpose: for
# them the useful advice is about TEMPERATURE, so advice() falls through to the bands below.
_ADVICE_BY_CONDITION = {
    "Thunderstorms":    ADVICE_STORM,
    "SunnyRain":        ADVICE_SUNNY_RAIN,
    "PartlyCloudyRain": ADVICE_SHOWERS,
    "CloudyRain":       ADVICE_RAIN,
}


def opener(named, clock=None, tz=None):
    """A random time-of-day opener. `clock` is an injectable callable returning the local hour 0-23 (the
    tests use it; production leaves it None and reads the configured zone)."""
    hour = clock() if callable(clock) else _hour_now(tz)
    try:
        hour = int(hour) % 24
    except (TypeError, ValueError):
        hour = 12
    return random.choice(_OPENERS[(_band(hour), bool(named))])


def advice(snapshot):
    """One advice sentence for TODAY, or "" when there is no usable forecast.

    Selection order is deliberate: a WET condition answers for itself, because being rained on is the thing
    a person would actually have changed their mind about, and it outranks any temperature. Only a DRY day
    falls through to the temperature bands -- there, "it's cloudy" is useless and "it's 8 degrees" is not.
    The band uses today's MAX (the "how should I dress" number) UNROUNDED, so a 15.4 degree day is not
    talked into the chilly band by the display rounding. Never raises: a greeting must not fail because a
    forecast row was odd."""
    try:
        if not snapshot:
            return ""
        day = snapshot["days"][0]
        by_condition = _ADVICE_BY_CONDITION.get(day.get("condition"))
        if by_condition is not None:
            return random.choice(by_condition)
        tmax = float(day["tmax"])
        if tmax <= _envf("ROBOT_WEATHER_CHILLY_C", 15):
            return random.choice(ADVICE_CHILLY)
        if tmax >= _envf("ROBOT_WEATHER_HOT_C", 30):
            return random.choice(ADVICE_HOT)
        return random.choice(ADVICE_PLEASANT)
    except Exception as e:
        log.debug("weather: advice failed (%s)", e)
        return ""


def greeting(name, pronunciation=None, snapshot=None, clock=None):
    """A time-of-day greeting for a RECOGNISED visitor, plus today's advice when a forecast is in hand.

    `snapshot` is passed IN by the caller (weather_svc.get()) because these are module functions with no
    access to the service instance -- the same dependency-injection shape concierge.presence_probe uses. A
    None snapshot yields the opener alone: weather must never delay, break or awkwardly pad a greeting.
    Pronunciation wins over the real name, exactly as concierge.greeting does."""
    spoken = (pronunciation or name or "").strip() or "friend"
    line = opener(named=True, clock=clock).replace("[Name]", spoken)
    tip = advice(snapshot)
    return f"{line} {tip}" if tip else line


def greeting_unknown(snapshot=None, clock=None):
    """The same, for a visitor who is present but wasn't recognised -- warm, and name-less."""
    line = opener(named=False, clock=clock)
    tip = advice(snapshot)
    return f"{line} {tip}" if tip else line
