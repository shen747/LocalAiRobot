"""Fetch, cache, serve-stale and the midnight re-index -- the offline behaviour, with no network at all.

The transport is injected through the provider's urlopen= constructor argument (the repo's DI convention),
so nothing here monkeypatches urllib and no socket is ever opened. Runs with zero env keys preset: each
scenario sets exactly what it needs, because scripts/run_wake_tests.sh sets only BRAIN_DIR.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.environ["BRAIN_DIR"])
import weather

F = []
TMP = tempfile.mkdtemp(prefix="wtest")


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_transport(days_from, calls=None, boom=None):
    """A fake urlopen serving a 4-day forecast starting at `days_from` (an ISO date string)."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(days_from)

    def urlopen(req, timeout=None):
        if calls is not None:
            calls.append(getattr(req, "full_url", req))
        if boom is not None:
            raise boom
        payload = {"daily": {
            "time": [(d0 + timedelta(days=i)).isoformat() for i in range(4)],
            "weather_code": [2, 61, 0, 95],
            "temperature_2m_max": [17.2, 15.0, 31.4, 22.0],
            "temperature_2m_min": [9.1, 8.3, 18.0, 12.0]}}
        return FakeResp(json.dumps(payload).encode())
    return urlopen


def env(**kw):
    """Set exactly these ROBOT_WEATHER_* keys and clear every other one, so tests can't leak into each other."""
    for k in list(os.environ):
        if k.startswith("ROBOT_WEATHER"):
            del os.environ[k]
    os.environ["ROBOT_WEATHER_ENABLE"] = "1"
    os.environ["ROBOT_WEATHER_LAT"] = "-37.81"
    os.environ["ROBOT_WEATHER_LON"] = "144.96"
    for k, v in kw.items():
        os.environ["ROBOT_WEATHER_" + k] = str(v)


TODAY = weather.today_iso()

print("=== a successful fetch populates the snapshot AND the disk cache ===")
cache = os.path.join(TMP, "c1.json")
env(CACHE=cache)
calls = []
svc = weather.WeatherService(urlopen=make_transport(TODAY, calls))
ck("configured", svc.configured, True)
ck("nothing before the first fetch", svc.get(), None)
svc.refresh()
snap = svc.get()
ck("available after fetch", svc.available, True)
ck("exactly three days served", len(snap["days"]), 3)
ck("day 0 is today", snap["days"][0]["date"], TODAY)
ck("fetch_ok counted", svc.fetch_ok, 1)
ck("cache written", os.path.exists(cache), True)
ck("url carries the timezone", "timezone=" in calls[0], True)
ck("url asks for four days", "forecast_days=4" in calls[0], True)

print("\n=== a network failure keeps serving the last good forecast ===")
svc.provider._urlopen = make_transport(TODAY, boom=ConnectionError("no route to host"))
svc._next_fetch_at = 0.0
svc.refresh()
ck("fetch_fail counted", svc.fetch_fail, 1)
ck("still available", svc.available, True)
ck("still serving three days", len(svc.get()["days"]), 3)

print("\n=== past STALE_S the forecast is DROPPED, not hedged ===")
svc._fetched_at -= 100000            # pretend the last success was long ago
ck("get() returns nothing", svc.get(), None)
svc.set_screen_ready(True)           # even with frames registered...
ck("has_screen() is False without data", svc.has_screen(), False)
svc.refresh()                        # a failing refresh must flip availability now
ck("available drops", svc.available, False)
ck("spoken_summary apologises", "weather service" in svc.spoken_summary(), True)
ck("summary_note is empty", svc.summary_note(), "")

print("\n=== has_screen() needs BOTH data and registered frames ===")
env(CACHE=os.path.join(TMP, "c2.json"))
svc2 = weather.WeatherService(urlopen=make_transport(TODAY))
svc2.refresh()
ck("data but no frames -> False", svc2.has_screen(), False)
svc2.set_screen_ready(True)
ck("data plus frames -> True", svc2.has_screen(), True)
svc2.set_screen_ready(False)
ck("frames withdrawn -> False", svc2.has_screen(), False)

print("\n=== THE MIDNIGHT RE-INDEX: a cache fetched yesterday still says 'today' correctly ===")
from datetime import date, timedelta
yest = (date.fromisoformat(TODAY) - timedelta(days=1)).isoformat()
env(CACHE=os.path.join(TMP, "c3.json"))
svc3 = weather.WeatherService(urlopen=make_transport(yest))    # 4 days starting YESTERDAY
svc3.refresh()
snap = svc3.get()
ck("day 0 is still today, not yesterday", snap["days"][0]["date"], TODAY)
ck("three days remain after the shift", len(snap["days"]), 3)
ck("days are consecutive",
   [d["date"] for d in snap["days"]],
   [(date.fromisoformat(TODAY) + timedelta(days=i)).isoformat() for i in range(3)])

print("\n=== ...and once today has rolled off the end, the data is treated as stale ===")
old = (date.fromisoformat(TODAY) - timedelta(days=5)).isoformat()
env(CACHE=os.path.join(TMP, "c4.json"))
svc4 = weather.WeatherService(urlopen=make_transport(old))
svc4.refresh()
ck("today is not in the data -> None", svc4.get(), None)

print("\n=== blank LAT/LON: off, quiet, no thread, no crash ===")
env(LAT="", LON="")
os.environ["ROBOT_WEATHER_LAT"] = ""
os.environ["ROBOT_WEATHER_LON"] = ""
svc5 = weather.WeatherService(urlopen=make_transport(TODAY))
ck("not configured", svc5.configured, False)
ck("not available", svc5.available, False)
ck("get() is None", svc5.get(), None)
ck("has_screen() is False", svc5.has_screen(), False)
ck("start() declines to spawn a thread", svc5.start(on_change=lambda s: None), False)
ck("greeting surfaces still answer", bool(svc5.spoken_summary()), True)

print("\n=== an unknown provider warns and stays inert ===")
env(PROVIDER="acme-weather")
svc6 = weather.WeatherService()
ck("no provider", svc6.provider, None)
ck("not configured", svc6.configured, False)
ck("start() is a no-op", svc6.start(), False)
ck("summary_note never raises", svc6.summary_note(), "")

print("\n=== a corrupt cache means 'no cache', never an exception ===")
for name, blob in (("not json", "{{{"),
                   ("json, wrong shape", '{"days": "tuesday"}'),
                   ("no fetched_at", '{"days": [{"date": "2026-08-08"}]}'),
                   ("bad condition", '{"days": [{"date": "2026-08-08", "condition": "Snowy", '
                                     '"tmax": 1, "tmin": 0}], "fetched_at": 1}'),
                   ("empty file", "")):
    p = os.path.join(TMP, "bad.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(blob)
    env(CACHE=p)
    try:
        s = weather.WeatherService(urlopen=make_transport(TODAY))
        ck(f"corrupt cache ({name}) -> no data, no raise", (s.get(), s.available), (None, False))
    except Exception as e:
        ck(f"corrupt cache ({name}) -> no raise", f"raised {e!r}", "no raise")

print("\n=== a cache written for OTHER coordinates is not somebody else's weather ===")
p = os.path.join(TMP, "moved.json")
env(CACHE=p)
s = weather.WeatherService(urlopen=make_transport(TODAY))
s.refresh()
ck("cache is usable at the same coordinates", weather.WeatherService(
    urlopen=make_transport(TODAY)).get() is not None, True)
os.environ["ROBOT_WEATHER_LAT"] = "51.50"          # the robot moved to London
os.environ["ROBOT_WEATHER_LON"] = "-0.12"
ck("cache ignored after a coordinate change",
   weather.WeatherService(urlopen=make_transport(TODAY)).get(), None)

print("\n=== the poll loop fires on_change EVERY cycle, including with snapshot=None ===")
# The stale-card fallback in main.py depends on this: without a None call the sleep screen could sit on an
# expired forecast forever, showing yesterday's "Today".
env(CACHE=os.path.join(TMP, "c7.json"))
seen = []
svc7 = weather.WeatherService(urlopen=make_transport(TODAY))
svc7.refresh()
seen.append(svc7.get())
svc7._fetched_at -= 100000
svc7.provider._urlopen = make_transport(TODAY, boom=TimeoutError("timed out"))
svc7._next_fetch_at = 0.0
svc7.refresh()
seen.append(svc7.get())
ck("cycle 1 delivered a snapshot", seen[0] is not None, True)
ck("cycle 2 delivered None", seen[1], None)

print("\n=== REFRESH_S=0 fetches once, but the loop still ticks hourly for the midnight rollover ===")
env(CACHE=os.path.join(TMP, "c8.json"), REFRESH_S=0)
calls8 = []
svc8 = weather.WeatherService(urlopen=make_transport(TODAY, calls8))
svc8.refresh()
svc8.refresh()
svc8.refresh()
ck("exactly one network call", len(calls8), 1)
ck("tick is still an hour", svc8._tick_s(), 3600.0)
env(CACHE=os.path.join(TMP, "c9.json"), REFRESH_S=7200)
ck("a long period is capped at an hour so midnight still rolls",
   weather.WeatherService(urlopen=make_transport(TODAY))._tick_s(), 3600.0)
env(CACHE=os.path.join(TMP, "c10.json"), REFRESH_S=300)
ck("a short period ticks at the period",
   weather.WeatherService(urlopen=make_transport(TODAY))._tick_s(), 300.0)

# REGRESSION: availability is reconciled on EVERY refresh(), not only on cycles where a fetch was due.
# It used to sit inside the "is a fetch due?" branch, so a REFRESH_S=0 robot (whose next-fetch time becomes
# infinity after its single fetch) reported available=True forever -- long after get() had started
# returning None -- and the "no usable forecast" warning could never fire. Data ages on the wall clock,
# not on fetch attempts.
env(CACHE=os.path.join(TMP, "c11.json"), REFRESH_S=0)
svc9 = weather.WeatherService(urlopen=make_transport(TODAY))
svc9.refresh()
ck("REFRESH_S=0 starts available", svc9.available, True)
ck("...and will never fetch again", svc9._next_fetch_at, float("inf"))
svc9._fetched_at -= 100000                       # the wall clock moved on; no fetch will ever run again
svc9.refresh()
ck("availability still drops with no fetch due", svc9.available, False)

print("\n=== blank env values fall back to defaults (set -a exports them as empty strings) ===")
env(CACHE="", REFRESH_S="", TIMEOUT_S="", STALE_S="", CHILLY_C="", HOT_C="", TZ="")
svcb = weather.WeatherService(urlopen=make_transport(TODAY))
ck("blank REFRESH_S -> 1800", svcb.refresh_s, 1800.0)
ck("blank TIMEOUT_S -> 8", svcb.timeout_s, 8.0)
ck("blank STALE_S -> 21600", svcb.stale_s, 21600.0)
ck("blank TZ -> the default zone", svcb.tz, weather.DEFAULT_TZ)
ck("blank CACHE -> the state home", svcb.cache_path.endswith("weather_cache.json"), True)
ck("blank CHILLY_C -> 15", weather._envf("ROBOT_WEATHER_CHILLY_C", 15), 15.0)

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + str(len(F))}")
print("=" * 58)
sys.exit(1 if F else 0)
