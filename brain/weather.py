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
_WET = ("SunnyRain", "PartlyCloudyRain", "CloudyRain")

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


def _tzinfo(tz=None):
    """ZoneInfo for the configured zone, or None to mean 'use naive local time'. Warns ONCE."""
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
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
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
        """Open-Meteo JSON -> [{date, condition, tmax, tmin}, ...]. Raises on a response we can't trust."""
        daily = (payload or {}).get("daily") or {}
        times = daily.get("time") or []
        codes = daily.get("weather_code") or []
        tmaxs = daily.get("temperature_2m_max") or []
        tmins = daily.get("temperature_2m_min") or []
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
                out.append({"date": str(times[i])[:10],
                            "condition": self.condition_for(codes[i]),
                            "tmax": float(tmaxs[i]),
                            "tmin": float(tmins[i])})
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
# WORDING NOTE: everything spoken passes through main._fix_pronunciation, a regex over
# ROBOT_TTS_PRONUNCIATIONS whose keys are people's NAMES. These lines avoid name-like words for that reason.

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
]

# --- Array 7: Chilly-day advice (today's max <= ROBOT_WEATHER_CHILLY_C) ---
ADVICE_CHILLY = [
    "It's a very chilly day today, isn't it? Make sure to pack your jumper.",
    "It's a cold one out there -- worth grabbing a jacket before you go.",
    "Rather nippy today, so I'd wear something warm.",
    "It's staying cold all day, so a proper coat wouldn't hurt.",
    "Bit of a shivery one today -- don't forget your jumper.",
    "It's chilly out, so wrap up warm before you head off.",
    "Cold air today. A scarf would not be an overreaction.",
    "It's a jumper sort of day, by the look of the forecast.",
]

# --- Array 8: Hot-day advice (today's max >= ROBOT_WEATHER_HOT_C) ---
ADVICE_HOT = [
    "It's going to be a hot one today, so keep your water bottle handy.",
    "Quite the scorcher today -- sunscreen and shade, I'd say.",
    "It's hot out there, so do drink plenty of water.",
    "A properly warm day ahead. Hat and sunscreen if you're outside.",
    "It's set to be very hot today, so take it easy in the sun.",
    "Hot one on the way -- stay in the shade where you can.",
    "It'll be sweltering later, so plenty of water please.",
    "A real summer day today. Sunscreen before you head out.",
]

# --- Array 9: Pleasant-day advice (between the two bands, and dry) ---
ADVICE_PLEASANT = [
    "It's looking like a lovely day out there.",
    "Rather a nice day today, by the forecast.",
    "The weather's being agreeable today, for once.",
    "A pleasant one ahead -- good day to be outside.",
    "Nothing to complain about in the forecast today.",
    "It's a comfortable sort of day out there.",
    "Fine weather today, happily.",
    "Looks like a rather good day for it.",
]

# --- Array 10: Rain advice (today is a wet condition) ---
ADVICE_RAIN = [
    "There's rain in the forecast today, so do take an umbrella.",
    "It's meant to be wet today -- I'd take a brolly if I were you.",
    "Showers on the way today, so grab an umbrella.",
    "Looks like rain today. A raincoat wouldn't go amiss.",
    "Wet weather ahead, so pack something waterproof.",
    "Rain is forecast today -- best take cover with you.",
    "It's going to be a damp one, so bring an umbrella.",
    "Showers expected today. Don't get caught out.",
]

# --- Array 11: Storm advice (today is Thunderstorms) ---
ADVICE_STORM = [
    "Storms are forecast today, so maybe keep the outdoor plans flexible.",
    "There's thunder about today -- worth staying near shelter.",
    "Thunderstorms on the way, so do be careful out there.",
    "It's a stormy one today. I'd keep an eye on the sky.",
    "Storms are expected later, so plan around them if you can.",
    "Thunder is forecast today -- best not to be caught outside.",
    "Rough weather ahead today, with storms about.",
    "Storms today, so take care if you're heading out.",
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

    Selection order is deliberate: wet beats hot or cold, because being rained on is the thing a person
    would actually have changed their mind about. The temperature band uses today's MAX -- the "how should I
    dress" number -- and uses it UNROUNDED, so a 15.4 degree day is not talked into the chilly band by the
    display rounding. Never raises: a greeting must not fail because a forecast row was odd."""
    try:
        if not snapshot:
            return ""
        day = snapshot["days"][0]
        cond = day.get("condition")
        if cond == "Thunderstorms":
            return random.choice(ADVICE_STORM)
        if cond in _WET:
            return random.choice(ADVICE_RAIN)
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
