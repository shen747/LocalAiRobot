"""Every WMO code maps to one of the seven canonical conditions, and no snow name can ever escape.

The gif basenames ARE the condition names, so a mapping that returns something unmapped is an icon that
silently never renders. This pins the whole table, including the defensive default for codes that do not
exist yet. Pure dict lookups + one canned JSON response -- no network, no env keys, no Pillow.
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


P = weather._OpenMeteoProvider

print("=== the published WMO table, code by code ===")
EXPECTED = {
    0: "Sunny", 1: "Sunny", 2: "PartlyCloudy", 3: "Cloudy",
    45: "Cloudy", 48: "Cloudy",
    51: "PartlyCloudyRain", 53: "CloudyRain", 55: "CloudyRain",
    56: "CloudyRain", 57: "CloudyRain",
    61: "PartlyCloudyRain", 63: "CloudyRain", 65: "CloudyRain",
    66: "CloudyRain", 67: "CloudyRain",
    71: "CloudyRain", 73: "CloudyRain", 75: "CloudyRain", 77: "Cloudy",
    80: "SunnyRain", 81: "PartlyCloudyRain", 82: "CloudyRain",
    85: "CloudyRain", 86: "CloudyRain",
    95: "Thunderstorms", 96: "Thunderstorms", 99: "Thunderstorms",
}
for code, want in sorted(EXPECTED.items()):
    ck(f"WMO {code}", P.condition_for(code), want)

print("\n=== nothing can escape the seven canonical names ===")
# The point of the snow-exclusion rule: 71..77/85/86 CAN appear in a real forecast, and mapping them --
# rather than filtering them -- is what guarantees the card always has an icon to draw.
ck("mapped set is a subset of CONDITIONS",
   set(P._WMO_TO_CONDITION.values()) <= set(weather.CONDITIONS), True)
ck("every canonical name is reachable",
   set(P._WMO_TO_CONDITION.values()) == set(weather.CONDITIONS), True)
ck("every condition has a spoken wording",
   all(c in weather._CONDITION_WORDS for c in weather.CONDITIONS), True)

print("\n=== unknown / hostile codes fall back, never raise ===")
for bad in (42, 103, -1, 9999):
    ck(f"unknown code {bad} -> Cloudy", P.condition_for(bad), "Cloudy")
for junk in (None, "", "abc", 3.7):
    got = P.condition_for(junk)
    ck(f"junk {junk!r} -> a canonical name", got in weather.CONDITIONS, True)
ck("3.7 truncates to code 3", P.condition_for(3.7), "Cloudy")

print("\n=== a canned Open-Meteo response normalizes to the day contract ===")
payload = {"daily": {
    "time": ["2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11"],
    "weather_code": [2, 61, 0, 95],
    "temperature_2m_max": [17.2, 15.0, 31.4, 22.0],
    "temperature_2m_min": [9.1, 8.3, 18.0, 12.0]}}
days = P()._normalize(payload)
ck("four days parsed", len(days), 4)
ck("day 0", days[0], {"date": "2026-08-08", "condition": "PartlyCloudy", "tmax": 17.2, "tmin": 9.1})
ck("day 3 condition", days[3]["condition"], "Thunderstorms")
ck("temps are floats", all(isinstance(d["tmax"], float) for d in days), True)

print("\n=== a response we cannot trust raises instead of half-rendering ===")
for name, bad in (
        ("mismatched array lengths", {"daily": {"time": ["a", "b"], "weather_code": [0],
                                                "temperature_2m_max": [1, 2], "temperature_2m_min": [0, 1]}}),
        ("empty daily", {"daily": {}}),
        ("no daily key", {"hourly": {}}),
        ("None payload", None)):
    try:
        P()._normalize(bad)
        ck(name, "no raise", "ValueError")
    except ValueError:
        ck(name, "ValueError", "ValueError")

print("\n=== a single null day is skipped, not fatal ===")
holey = {"daily": {"time": ["2026-08-08", "2026-08-09", "2026-08-10"],
                   "weather_code": [2, 61, 0],
                   "temperature_2m_max": [17.2, None, 31.4],
                   "temperature_2m_min": [9.1, 8.3, 18.0]}}
days = P()._normalize(holey)
ck("the null day is dropped", [d["date"] for d in days], ["2026-08-08", "2026-08-10"])

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + str(len(F))}")
print("=" * 58)
sys.exit(1 if F else 0)
