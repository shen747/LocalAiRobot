"""Social Memory: sighting buckets, greeting composition, notes retention, and the flag-off guarantee.

The governing requirement is ZERO REGRESSION, so the most important assertions here are the ones that
prove the feature is invisible when off and degrades to today's behaviour at every seam. Everything runs
against a real SQLite Memory in a temp dir -- the schema is part of what's under test -- but no camera,
no network and no LLM.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.environ["BRAIN_DIR"])

TMP = tempfile.mkdtemp(prefix="social")
os.environ["ROBOT_MEMORY_DB"] = os.path.join(TMP, "mem.db")
for k in list(os.environ):
    if k.startswith("ROBOT_SOCIAL") or k.startswith("ROBOT_WEATHER"):
        del os.environ[k]

import social
import weather
from memory import Memory

F = []
NOW = time.time()


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


def days_ago(d, h=0):
    return NOW - d * 86400 - h * 3600


SNAP = {"days": [{"date": "2026-08-08", "condition": "CloudyRain", "tmax": 14.0, "tmin": 8.0},
                 {"date": "2026-08-09", "condition": "Sunny", "tmax": 20.0, "tmin": 9.0},
                 {"date": "2026-08-10", "condition": "Sunny", "tmax": 21.0, "tmin": 10.0}]}

print("=== the master switch is ON by default, and 0 still turns it off ===")
# The CODE default matches the shipped template rather than being the safe-side 0: "on by default" that a
# missing key silently disables is not on by default. A robot whose robot.env predates these keys gets the
# feature, which is only defensible because every seam degrades (see the end of this file).
ck("social.enabled() with no env at all", social.enabled(), True)
ck("curiosity too", social.curiosity_enabled(), True)
for off in ("0", "false", "no", "off"):
    os.environ["ROBOT_SOCIAL_MEMORY"] = off
    ck(f"ROBOT_SOCIAL_MEMORY={off!r} turns it off", social.enabled(), False)
os.environ["ROBOT_SOCIAL_CURIOSITY"] = "0"
ck("curiosity is separately switchable", social.curiosity_enabled(), False)
del os.environ["ROBOT_SOCIAL_CURIOSITY"]
os.environ["ROBOT_SOCIAL_MEMORY"] = "1"
ck("...and back on", social.enabled(), True)

print("\n=== sighting buckets are CALENDAR days, not elapsed hours ===")
# 23:00 -> 00:30 is 90 minutes but a NEW day; 08:00 -> 20:00 is 12 hours and the SAME day. Bucketing on
# elapsed time gets both backwards, which is why this is done on dates in the configured zone.
zone = weather._tzinfo(None)
today_8am = datetime.fromtimestamp(NOW, zone).replace(hour=8, minute=0, second=0).timestamp()
today_8pm = datetime.fromtimestamp(NOW, zone).replace(hour=20, minute=0, second=0).timestamp()
ck("never seen -> first", social.bucket(None), social.FIRST)
ck("8am seen from 8pm -> today (12h apart)", social.bucket(today_8am, now_ts=today_8pm), social.TODAY)
ck("yesterday", social.bucket(days_ago(1)), social.YESTERDAY)
ck("4 days -> this week", social.bucket(days_ago(4)), social.THIS_WEEK)
ck("40 days -> long gap", social.bucket(days_ago(40)), social.LONG_GAP)
late = datetime.fromtimestamp(NOW, zone).replace(hour=23, minute=0, second=0)
early = late + timedelta(hours=1, minutes=30)
ck("23:00 -> 00:30 is a NEW day despite 90 min",
   social.bucket(late.timestamp(), now_ts=early.timestamp()), social.YESTERDAY)
os.environ["ROBOT_SOCIAL_LONG_GAP_D"] = "3"
ck("the long-gap threshold is configurable", social.bucket(days_ago(4)), social.LONG_GAP)
del os.environ["ROBOT_SOCIAL_LONG_GAP_D"]

print("\n=== every bucket produces a greeting, with the name substituted ===")
for b, last in ((social.FIRST, None), (social.TODAY, (days_ago(0, 6), True)),
                (social.YESTERDAY, (days_ago(1), True)), (social.THIS_WEEK, (days_ago(4), True)),
                (social.LONG_GAP, (days_ago(40), True))):
    g = social.greeting("Ramesh", "Ruh-mesh", snapshot=SNAP, last_seen=last)
    ck(f"{b}: says the name", "Ruh-mesh" in g, True)
    ck(f"{b}: no placeholder left", "[Name]" in g, False)
    ck(f"{b}: real name not spoken over the respelling", "Ramesh" in g, False)

print("\n=== composition rule: AT MOST one follow-on clause ===")
# The 'seen earlier today' opener already ends in a question, so it must not also get a plan callback or
# weather advice -- two questions in one hello is an interrogation.
notes = [("plan", "school", days_ago(0, 6)), ("job", "drives a bus", days_ago(3))]
g = social.greeting("Ramesh", None, snapshot=SNAP, last_seen=(days_ago(0, 6), True), notes=notes)
ck("today-bucket adds nothing", g in [o.replace("[Name]", "Ramesh") for o in social.OPENERS_TODAY], True)
ck("...so no weather advice is appended", any(a in g for a in weather.ADVICE_RAIN), False)

print("\n=== follow-on precedence: plan > job-weather > plain weather ===")
plan_only = [("plan", "school", days_ago(0, 6))]
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=plan_only)
ck("a plan from earlier today wins", "school" in g, True)
job_only = [("job", "drives a bus", days_ago(3))]
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=job_only)
ck("job + wet forecast -> job-aware line", "driving" in g or "wheel" in g, True)
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=[])
ck("no notes -> plain weather advice", any(a in g for a in weather.ADVICE_RAIN), True)
g = social.greeting("R", None, snapshot=None, last_seen=(days_ago(1), True), notes=[])
ck("no weather either -> the opener alone, no dangling space", g, g.strip())
ck("...and no double space", "  " in g, False)
ck("...and it IS just an opener", g.replace("R", "[Name]") in social.OPENERS_YESTERDAY, True)

print("\n=== a plan is only asked about once it has HAD time to happen ===")
fresh = [("plan", "school", NOW - 90)]         # said ninety seconds ago -- they haven't been yet
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=fresh)
ck("a just-stated plan is not asked about", "How was school" in g, False)
old = [("plan", "school", days_ago(0, 6))]
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=old)
ck("a morning plan is", "school" in g, True)

print("\n=== a rambling plan degrades to the generic callback, never mangled grammar ===")
rambling = [("plan", "I am going to the dentist and then the shops", days_ago(0, 6))]
g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=rambling)
ck("no 'How was I am going to...'", "How was I am" in g, False)
ck("a generic callback instead", any(c in g for c in social.PLAN_CALLBACKS_GENERIC), True)

print("\n=== never raises, whatever it is handed ===")
for bad in (None, [], [("plan", None, "x")], [("job", 42, None)], "notanote"):
    try:
        g = social.greeting("R", None, snapshot=SNAP, last_seen=(days_ago(1), True), notes=bad)
        ck(f"notes={bad!r} still greets", bool(g), True)
    except Exception as e:
        ck(f"notes={bad!r} must not raise", f"raised {e!r}", "a greeting")
try:
    g = social.greeting("R", None, snapshot={"days": []}, last_seen=("notatime", True))
    ck("corrupt last_seen still greets", bool(g), True)
except Exception as e:
    ck("corrupt last_seen must not raise", f"raised {e!r}", "a greeting")

print("\n=== the context note: short, informative, never raises ===")
n = social.context_note("Ramesh", last_seen=(days_ago(0, 6), True),
                        notes=[("job", "drives a bus", days_ago(3)),
                               ("plan", "school", days_ago(0, 6))])
print("      note:", n)
ck("parenthesised", n.startswith("(") and n.endswith(")"), True)
ck("names the person", "Ramesh" in n, True)
ck("carries the job", "drives a bus" in n, True)
ck("carries the plan", "school" in n, True)
ck("stays short", len(n.split()) <= 45, True)
ck("no name -> empty", social.context_note(""), "")
ck("no data -> still just the name", social.context_note("R"), "(You are talking to R.)")

print("\n=== curiosity: ask once, then not again for the cooldown ===")
ck("unknown job -> due", social.curiosity_due([]), True)
ck("known job -> not due", social.curiosity_due([("job", "drives a bus", NOW)]), False)
ck("asked recently -> not due",
   social.curiosity_due([("curiosity", "asked-job", NOW - 86400)]), False)
ck("asked long ago -> due again",
   social.curiosity_due([("curiosity", "asked-job", days_ago(60))]), True)
ck("the directive names the person", "Ramesh" in social.curiosity_directive("Ramesh"), True)

print("\n=== memory: sightings round-trip ===")
mem = Memory()
ck("no history yet", mem.last_sighting("Ramesh"), None)
mem.record_sighting("Ramesh", 0.42, greeted=True)
got = mem.last_sighting("Ramesh")
ck("recorded", got is not None and abs(got[0] - time.time()) < 5, True)
ck("greeted flag", got[1], True)
ck("a different person is unaffected", mem.last_sighting("Nobody"), None)
ck("blank name is ignored", (mem.record_sighting("", 1.0), mem.last_sighting(""))[1], None)
mem.record_sighting("Ramesh", 0.5, greeted=False)
ck("count since", mem.sightings_since("Ramesh", NOW - 60), 2)

print("\n=== memory: notes, expiry and per-kind replace ===")
mem.remember_note("Ramesh", "job", "drives a bus")
mem.remember_note("Ramesh", "job", "drives a tram")          # they changed jobs
jobs = mem.notes_for("Ramesh", kinds=("job",))
ck("a job REPLACES, never stacks", len(jobs), 1)
ck("...and the new one wins", jobs[0][1], "drives a tram")
mem.remember_note("Ramesh", "plan", "school", expires_ts=time.time() + 60)
ck("an unexpired plan is visible", len(mem.notes_for("Ramesh", kinds=("plan",))), 1)
mem.remember_note("Ramesh", "plan", "dentist", expires_ts=time.time() - 60)
ck("an EXPIRED plan is hidden", [t for _k, t, _ts in mem.notes_for("Ramesh", kinds=("plan",))],
   ["dentist"] if False else [])
ck("...but visible when asked for", len(mem.notes_for("Ramesh", kinds=("plan",), include_expired=True)), 1)
ck("blank name -> no notes", mem.notes_for(""), [])
ck("blank kind is ignored", (mem.remember_note("Ramesh", "", "x"), True)[1], True)

print("\n=== plans expire at MIDNIGHT in the configured zone ===")
eod = social.end_of_day_ts()
ck("expiry is in the future", eod > time.time(), True)
ck("...and within 24h", eod - time.time() <= 86400 + 1, True)
midnight = datetime.fromtimestamp(eod, zone)
ck("it IS midnight", (midnight.hour, midnight.minute), (0, 0))

print("\n=== retention caps both tables ===")
os.environ["ROBOT_MEMORY_RETAIN_SIGHTINGS"] = "5"
os.environ["ROBOT_MEMORY_RETAIN_NOTES"] = "3"
for i in range(12):
    mem.record_sighting("Bulk", 0.1, greeted=False)
for i in range(8):
    mem.remember_note("Bulk", f"note{i}", f"thing {i}", replace=False)
mem._prune_social()
with mem._lock:
    n_sight = mem._db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    n_notes = mem._db.execute(
        "SELECT COUNT(*) FROM person_notes WHERE name='Bulk' AND expires_ts IS NULL").fetchone()[0]
ck("sightings capped", n_sight, 5)
ck("stable notes capped per person", n_notes, 3)
ck("expired plans swept",
   len(mem.notes_for("Ramesh", kinds=("plan",), include_expired=True)) <= 1, True)
del os.environ["ROBOT_MEMORY_RETAIN_SIGHTINGS"]
del os.environ["ROBOT_MEMORY_RETAIN_NOTES"]

print("\n=== the extractor: only usable rows survive, and generated text never becomes state blindly ===")
# This is the ONE place where model output becomes stored state, and every row it accepts is later spoken
# aloud to the person it is about. The rules are deliberately mean.
good = {"notes": [{"kind": "job", "text": "drives a bus", "transient": False},
                  {"kind": "plan", "text": "school", "transient": True}]}
rows = social.parse_extraction(good)
ck("two usable rows", len(rows), 2)
ck("job parsed", rows[0], ("job", "drives a bus", False))
ck("plan parsed as transient", rows[1], ("plan", "school", True))

for label, payload in (
        ("unknown kind", {"notes": [{"kind": "horoscope", "text": "a libra"}]}),
        ("empty text", {"notes": [{"kind": "job", "text": "   "}]}),
        ("a whole sentence", {"notes": [{"kind": "job", "text":
                                         "he told me that he drives the number 57 bus every weekday"}]}),
        ("a question", {"notes": [{"kind": "job", "text": "what do you do?"}]}),
        ("not a dict row", {"notes": ["drives a bus"]}),
        ("notes not a list", {"notes": "drives a bus"}),
        ("no notes key", {"result": "ok"}),
        ("empty object", {}),
        ("None", None),
        ("a bare list", ["job"]),
        ("a string", "notes")):
    try:
        ck(f"{label} -> nothing stored", social.parse_extraction(payload), [])
    except Exception as e:
        ck(f"{label} must not raise", f"raised {e!r}", "[]")

ck("a nonsense flood is capped", len(social.parse_extraction(
    {"notes": [{"kind": "note", "text": f"thing {i}"} for i in range(50)]})) <= 6, True)
ck("quotes and trailing stops are cleaned",
   social.parse_extraction({"notes": [{"kind": "job", "text": '"drives a bus".'}]})[0][1], "drives a bus")

print("\n=== a model call is only spent on a conversation that could teach something ===")
# Cost gate: every extraction is a model call, billable under a remote text backend. A greeting followed
# by "hello" cannot have taught us anything.
ck("nothing said at all", social.worth_extracting([]), False)
ck("None", social.worth_extracting(None), False)
ck("one 'hello' only", social.worth_extracting([("user", "hello"), ("robot", "Hi!")]), False)
ck("robot chatter alone doesn't count",
   social.worth_extracting([("robot", "a"), ("robot", "b"), ("robot", "c")]), False)
ck("blank user turns don't count",
   social.worth_extracting([("user", "  "), ("user", ""), ("user", "\n")]), False)
ck("a real exchange does",
   social.worth_extracting([("user", "hello"), ("robot", "Hi!"), ("user", "i drive a bus")]), True)
ck("junk turns don't raise", social.worth_extracting([(None, None), 42]), False)

print("\n=== the transcript handed to the extractor is bounded at both ends ===")
turns = [("user" if i % 2 == 0 else "robot", f"line {i}") for i in range(40)]
t = social.format_transcript(turns)
ck("only the tail is kept", t.count("\n") + 1 <= 12, True)
ck("the LAST turn survives (where people say what they're doing)", "line 39" in t, True)
ck("the earliest turns are dropped", "line 0" in t, False)
ck("roles are labelled", "Person:" in t and "Robot:" in t, True)
ck("empty buffer -> empty string", social.format_transcript([]), "")
ck("junk turns don't raise", isinstance(social.format_transcript([(None, None), ("user", "")]), str), True)
long_turns = [("user", "x" * 3000)]
ck("a huge turn is truncated", len(social.format_transcript(long_turns)) <= 1200, True)

print("\n=== ROBOT_WEATHER_GREETING=0 still suppresses weather, now that social sits in front of it ===")
# REGRESSION. That key used to be checked at the call site, on the branch social now takes first -- so
# turning weather greetings off left the weather line showing up inside social greetings instead. The call
# site fixes it by WITHHOLDING the snapshot, which is why this is testable here: social composes its
# follow-on from what it is given, so no snapshot means no weather clause and nothing else changes.
FIRST_LEN = len(social.OPENERS_FIRST)
with_snap = [social.greeting(f"P{i}", None, snapshot=SNAP, last_seen=None, notes=[])
             for i in range(FIRST_LEN)]
without = [social.greeting(f"P{i}", None, snapshot=None, last_seen=None, notes=[])
           for i in range(FIRST_LEN)]
ck("a snapshot yields weather advice",
   all(any(a in g for a in weather.ADVICE_RAIN) for g in with_snap), True)
ck("no snapshot yields none at all",
   any(any(a in g for a in weather.ADVICE_RAIN) for g in without), False)
ck("...and the opener still comes through",
   all(g.replace(f"P{i}", "[Name]") in social.OPENERS_FIRST for i, g in enumerate(without)), True)
# The plan callback must survive the withheld snapshot -- it is not weather, and turning weather greetings
# off should not cost you "how was school?".
plan = [("plan", "school", NOW - 6 * 3600)]
ck("the plan callback is unaffected",
   "school" in social.greeting("P", None, snapshot=None, last_seen=(days_ago(1), True), notes=plan), True)

print("\n=== the extractor's generation guard: keep this chat's result, drop a superseded one ===")
# REGRESSION. `gen` used to be bumped when a conversation ENDED, which is the same moment the extractor
# was spawned -- so its own guard rejected its own result every single time. The feature ran, made its
# model call, and threw the answer away, silently and forever. `gen` marks a conversation STARTING.
import threading


class Flow:
    """main.py's present_person lifecycle, reduced to the parts the guard depends on."""

    def __init__(self):
        self.pp = {"name": "", "gen": 0, "turns": []}
        self.stored = []

    def greet(self, name):                       # a by-name greeting starts a conversation
        self.pp.update(name=name, turns=[], gen=self.pp["gen"] + 1)

    def finish(self):                            # conversation over: spawn, then forget the person
        gen, name = self.pp["gen"], self.pp["name"]
        self.pp.update(name="", turns=[])        # NOTE: does not touch gen
        return gen, name

    def extract(self, gen, name):                # what the daemon thread does when the model returns
        if self.pp["gen"] != gen:
            return False
        self.stored.append(name)
        return True


f = Flow()
f.greet("Ramesh")
f.pp["turns"].append(("user", "hello"))
g, n = f.finish()
ck("a normal conversation's result IS stored", f.extract(g, n), True)
ck("...under the right name", f.stored, ["Ramesh"])

f2 = Flow()
f2.greet("Ramesh")
g, n = f2.finish()
f2.greet("Shanika")                              # a new visitor arrives while the model is still thinking
ck("a superseded extraction is dropped", f2.extract(g, n), False)
ck("...and nothing was filed under the wrong person", f2.stored, [])

f3 = Flow()
f3.greet("Ramesh")
g1, n1 = f3.finish()
f3.greet("Ramesh")                               # the SAME person, back for a second chat
ck("even the same person's later chat supersedes", f3.extract(g1, n1), False)
g2, n2 = f3.finish()
ck("...but that second chat stores normally", f3.extract(g2, n2), True)

print("\n=== ZERO REGRESSION: with the flag off, the greeting is the weather one ===")
# The call site branches on social.enabled(); with it off nothing here is reached, and the text must come
# from today's pools. Proven by construction: every line weather.greeting can produce is in those arrays.
os.environ["ROBOT_SOCIAL_MEMORY"] = "0"
ck("flag off", social.enabled(), False)
ALL_WEATHER = set(weather.MORNING_OPENERS + weather.AFTERNOON_OPENERS + weather.EVENING_OPENERS)
for _ in range(40):
    g = weather.greeting("Ramesh", "Ruh-mesh", snapshot=None, clock=lambda: 8)
    if g.replace("Ruh-mesh", "[Name]") not in ALL_WEATHER:
        F.append("flag-off greeting escaped the weather pools")
        break
ck("40 samples all come from the weather pools", "flag-off greeting escaped the weather pools" in F, False)
ck("...and none of them is a social opener",
   any(g.replace("Ruh-mesh", "[Name]") in social.OPENERS_TODAY for g in
       [weather.greeting("R", "Ruh-mesh", snapshot=None, clock=lambda: 8) for _ in range(20)]), False)

mem.close()
print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + str(len(F))}")
print("=" * 58)
sys.exit(1 if F else 0)
