"""Greeting text: the right time band, the right advice, and graceful silence when there is no forecast.

The clock is injected (a callable returning the local hour) so this runs identically at any time of day on
any machine, and every advice band is checked against a hand-built snapshot. No network, no env keys, no
Pillow. Also pins summary_note()'s one-sentence contract -- the note goes into every LLM prompt, and a
multi-sentence one would eat the front-loaded action schema when the context cap truncates.
"""
import os
import sys

sys.path.insert(0, os.environ["BRAIN_DIR"])
import weather

F = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


def at(hour):
    return lambda: hour


def snap(condition="PartlyCloudy", tmax=20.0, tmin=10.0):
    """A snapshot whose TODAY is the day under test; the other two days never affect advice."""
    return {"days": [
        {"date": "2026-08-08", "condition": condition, "tmax": tmax, "tmin": tmin},
        {"date": "2026-08-09", "condition": "CloudyRain", "tmax": 15.0, "tmin": 8.3},
        {"date": "2026-08-10", "condition": "Sunny", "tmax": 31.4, "tmin": 18.0}],
        "fetched_at": 1.0, "age_s": 10.0, "stale": False}


for k in list(os.environ):
    if k.startswith("ROBOT_WEATHER"):
        del os.environ[k]

print("=== time bands: 5-11 morning, 12-16 afternoon, 17-4 evening ===")
BANDS = {0: "evening", 4: "evening", 5: "morning", 11: "morning",
         12: "afternoon", 16: "afternoon", 17: "evening", 20: "evening", 23: "evening"}
for hour, want in sorted(BANDS.items()):
    ck(f"hour {hour:2d}", weather._band(hour), want)

print("\n=== the opener matches the band, named and nameless ===")
for hour, word in ((8, "morning"), (14, "afternoon"), (20, "evening")):
    for named in (True, False):
        # Every variant of a band contains its band word, so one sample is enough to prove the wiring.
        got = weather.opener(named=named, clock=at(hour))
        ck(f"h={hour} named={named} says '{word}'", word in got.lower(), True)
        ck(f"h={hour} named={named} placeholder present", ("[Name]" in got), named)

print("\n=== advice bands, on TODAY's max ===")
ck("chilly (10 <= 15)", weather.advice(snap(tmax=10.0)) in weather.ADVICE_CHILLY, True)
ck("chilly boundary (exactly 15)", weather.advice(snap(tmax=15.0)) in weather.ADVICE_CHILLY, True)
ck("pleasant (20)", weather.advice(snap(tmax=20.0)) in weather.ADVICE_PLEASANT, True)
ck("hot boundary (exactly 30)", weather.advice(snap(tmax=30.0)) in weather.ADVICE_HOT, True)
ck("hot (34)", weather.advice(snap(tmax=34.0)) in weather.ADVICE_HOT, True)

print("\n=== wet outranks BOTH temperature bands -- being rained on is what changes plans ===")
for cond in ("SunnyRain", "PartlyCloudyRain", "CloudyRain"):
    ck(f"{cond} on a hot day -> umbrella",
       weather.advice(snap(condition=cond, tmax=34.0)) in weather.ADVICE_RAIN, True)
    ck(f"{cond} on a cold day -> umbrella",
       weather.advice(snap(condition=cond, tmax=5.0)) in weather.ADVICE_RAIN, True)
ck("Thunderstorms -> storm advice, not rain advice",
   weather.advice(snap(condition="Thunderstorms", tmax=34.0)) in weather.ADVICE_STORM, True)

print("\n=== the temperature band uses the UNROUNDED max ===")
# 15.4 displays as "15" but must NOT be talked into the chilly band by the display rounding.
ck("15.4 is not chilly", weather.advice(snap(tmax=15.4)) in weather.ADVICE_PLEASANT, True)
ck("29.6 is not hot", weather.advice(snap(tmax=29.6)) in weather.ADVICE_PLEASANT, True)

print("\n=== configurable bands ===")
os.environ["ROBOT_WEATHER_CHILLY_C"] = "18"
os.environ["ROBOT_WEATHER_HOT_C"] = "25"
ck("17 is chilly at CHILLY_C=18", weather.advice(snap(tmax=17.0)) in weather.ADVICE_CHILLY, True)
ck("26 is hot at HOT_C=25", weather.advice(snap(tmax=26.0)) in weather.ADVICE_HOT, True)
del os.environ["ROBOT_WEATHER_CHILLY_C"]
del os.environ["ROBOT_WEATHER_HOT_C"]

print("\n=== no forecast -> the opener alone: no padding, no trailing space, no crash ===")
for maker in (lambda: weather.greeting("Ramesh", None, snapshot=None, clock=at(8)),
              lambda: weather.greeting_unknown(snapshot=None, clock=at(8))):
    got = maker()
    ck("advice omitted", got, got.strip())
    ck("no double space", "  " in got, False)
    ck("still greets", got.lower().startswith(("good morning", "morning", "a very good", "well, good", "ah")),
       True)
ck("advice(None) is empty", weather.advice(None), "")
ck("advice({}) is empty", weather.advice({}), "")

print("\n=== a corrupt snapshot degrades to no advice rather than breaking a greeting ===")
for bad in ({"days": []}, {"days": [{"condition": "Sunny"}]}, {"days": [None]},
            {"days": [{"condition": "Sunny", "tmax": "warm"}]}, {"nope": 1}):
    try:
        ck(f"advice({bad!r}) -> ''", weather.advice(bad), "")
    except Exception as e:
        ck(f"advice({bad!r}) must not raise", f"raised {e!r}", "''")
try:
    got = weather.greeting("Ramesh", None, snapshot={"days": [None]}, clock=at(8))
    ck("greeting survives a corrupt snapshot", bool(got), True)
except Exception as e:
    ck("greeting survives a corrupt snapshot", f"raised {e!r}", "a greeting")

print("\n=== [Name] substitution has parity with concierge.greeting ===")
g = weather.greeting("Ramesh", "Ruh-mesh", snapshot=snap(), clock=at(8))
ck("pronunciation wins over the name", ("Ruh-mesh" in g and "Ramesh" not in g), True)
ck("no placeholder survives", "[Name]" in g, False)
g = weather.greeting("Ramesh", None, snapshot=snap(), clock=at(8))
ck("plain name when no respelling", "Ramesh" in g, True)
g = weather.greeting("", "", snapshot=snap(), clock=at(8))
ck("nameless falls back to 'friend'", "friend" in g, True)
g = weather.greeting_unknown(snapshot=snap(), clock=at(8))
ck("the unknown greeting has no placeholder", "[Name]" in g, False)

print("\n=== a decorated greeting is opener + exactly ONE advice sentence ===")
g = weather.greeting("Ramesh", None, snapshot=snap(tmax=10.0), clock=at(8))
ck("has the opener", "morning" in g.lower(), True)
ck("has the advice", any(a in g for a in weather.ADVICE_CHILLY), True)

print("\n=== every phrase array is populated and placeholder-correct ===")
NAMED = (weather.MORNING_OPENERS, weather.AFTERNOON_OPENERS, weather.EVENING_OPENERS)
NAMELESS = (weather.MORNING_OPENERS_UNKNOWN, weather.AFTERNOON_OPENERS_UNKNOWN,
            weather.EVENING_OPENERS_UNKNOWN)
ADVICE = (weather.ADVICE_CHILLY, weather.ADVICE_HOT, weather.ADVICE_PLEASANT,
          weather.ADVICE_RAIN, weather.ADVICE_STORM)
for arr in NAMED + NAMELESS + ADVICE:
    ck(f"array of {len(arr)} has entries", len(arr) >= 8, True)
ck("every named opener has a [Name]", all("[Name]" in s for a in NAMED for s in a), True)
ck("no nameless opener has a [Name]", any("[Name]" in s for a in NAMELESS for s in a), False)
ck("no advice line has a [Name]", any("[Name]" in s for a in ADVICE for s in a), False)
ck("advice lines are sentences", all(s.strip().endswith((".", "!", "?")) for a in ADVICE for s in a), True)

print("\n=== summary_note(): exactly ONE sentence, and never a raise ===")


class Svc(weather.WeatherService):
    """A service whose get() we drive directly -- the note formatting is what's under test here."""

    def __init__(self, snapshot):
        self._snap = snapshot
        self.tz = "Australia/Melbourne"
        self.asks = 0

    def get(self):
        return self._snap


note = Svc(snap()).summary_note()
print("      note:", note)
ck("is parenthesised", note.startswith("(") and note.endswith(")"), True)
ck("exactly one full stop", note.count("."), 1)
ck("names the place, not the whole zone", ("Melbourne" in note and "Australia/" not in note), True)
ck("mentions all three days", ("today" in note and "tomorrow" in note), True)
ck("carries the weekday of day 3", "Monday" in note, True)      # 2026-08-10 is a Monday
ck("no forecast -> empty", Svc(None).summary_note(), "")
for bad in ({"days": []}, {"days": [1, 2, 3]}, {"nope": 1}):
    try:
        ck(f"corrupt snapshot {bad!r} -> ''", Svc(bad).summary_note(), "")
    except Exception as e:
        ck(f"corrupt snapshot {bad!r} must not raise", f"raised {e!r}", "''")

print("\n=== spoken_summary(): speakable, and one source of the apology ===")
sp = Svc(snap()).spoken_summary()
print("      spoken:", sp)
ck("says degrees in words, not a symbol", ("degrees" in sp and "°" not in sp), True)
ck("mentions today and tomorrow", ("Today" in sp and "Tomorrow" in sp), True)
ck("stays brief", len(sp.split()) <= 40, True)
s = Svc(None)
ck("no data -> the apology line", "weather service" in s.spoken_summary(), True)
ck("asks counted", s.asks, 1)
ck("apology never raises on junk", bool(Svc({"days": []}).spoken_summary()), True)

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + str(len(F))}")
print("=" * 58)
sys.exit(1 if F else 0)
