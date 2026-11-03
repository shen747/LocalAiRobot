"""The IDENTIFY gate: a "who" question must reach the face pipeline, not the vision model.

Also pins the ORDER of the three promotion gates, which is load-bearing: weather -> identify -> look.
Get it wrong and "who do you see" is answered by a model that has never seen an enrolment.
"""
import os
import sys

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())
from main import (compose_identity_reply, identify_triggers, is_identify_command,
                  is_look_command, is_weather_command)

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


print("\n=== THE COMPLAINT: the phrasings that used to get a description ===")
for phrase in ("who is this?",
               "do you know these people that you see through the camera?",
               "hey jarvis, who's that?",
               "do you recognise anyone?",
               "who can you see right now",
               "do you know who this is",
               "who am i?",
               "can you tell who that is",
               "identify these people for me"):
    check(f"{phrase!r} -> IDENTIFY", is_identify_command(phrase), True)

print("\n=== ...without hijacking questions that are not about who is in the room ===")
for phrase in ("who made you",
               "who is the prime minister",
               "what do you see",
               "what is this thing i am holding",
               "what's the weather like today",
               "do you know how to drive",
               "tell me a joke"):
    check(f"{phrase!r} stays put", is_identify_command(phrase), False)

print("\n=== the gates OVERLAP on purpose -- order is what resolves them ===")
# A real utterance can hit both lists at once -- this one carries 'do you know these' AND 'what do you see'.
both = "what do you see, do you know these people?"
check("this hits the identify list", is_identify_command(both), True)
check("...and the look list too", is_look_command(both), True)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "brain", "main.py"), encoding="utf-8").read()
# Source order IS the behaviour here: these three gates run top to bottom on the same `action`.
check("weather gate runs first (a forecast is never about people)",
      src.index("weather gate: promoting") < src.index("identify gate: promoting"), True)
check("identify gate beats the look gate (WHO cannot be answered by a description)",
      src.index("identify gate: promoting") < src.index("look gate: promoting"), True)
check("a weather question is still a weather question", is_weather_command("what's the weather like"), True)

print("\n=== IDENTIFY is camera work, so the HTTP block must cover it ===")
blocked = src[src.index('if source == "http" and action in ('):][:120]
check("listed in the http camera block", "IDENTIFY" in blocked, True)

print("\n=== ROBOT_IDENTIFY_TRIGGERS *ADDS TO* the defaults (never replaces) ===")
base = len(identify_triggers())
os.environ["ROBOT_IDENTIFY_TRIGGERS"] = "whos in the hall, spot anyone"
try:
    now = identify_triggers()
    check("both extras added", len(now), base + 2)
    check("the built-ins survived", "who is this" in now, True)
    check("and the extra works", is_identify_command("hey, spot anyone out there?"), True)
finally:
    del os.environ["ROBOT_IDENTIFY_TRIGGERS"]
check("removing it restores the defaults", len(identify_triggers()), base)

print("\n=== what it actually SAYS ===")
check("no face at all -> None, so the caller falls back to a description",
      compose_identity_reply([], 0), None)
check("one known", compose_identity_reply(["Ramesh"], 1), "That's Ramesh.")
check("one known + a social note",
      compose_identity_reply(["Ramesh"], 1, note="He said he's working from home today."),
      "That's Ramesh. He said he's working from home today.")
check("known beside a stranger names one and counts the other",
      compose_identity_reply(["Ramesh"], 2, unknown=1),
      "That's Ramesh. There's someone with them I haven't met.")
check("two known", compose_identity_reply(["Ramesh", "Shanika"], 2),
      "I can see Ramesh and Shanika.")
check("three known use an Oxford comma", compose_identity_reply(["A", "B", "C"], 3),
      "I can see A, B, and C.")
check("a group note is NOT attached (it speaks about one person)",
      compose_identity_reply(["A", "B"], 2, note="She likes tea."), "I can see A and B.")
check("faces but nobody enrolled", compose_identity_reply([], 2),
      "I can see two people, but I don't recognise any of them.")
check("a single unrecognised face reads naturally", compose_identity_reply([], 1),
      "I can see someone, but I don't recognise them.")
check("over the cap, it says so out loud rather than pretending",
      compose_identity_reply(["A"], 9, unknown=0, truncated=True),
      "That's A. There may be one or two more further back that I couldn't make out.")
check("counts beyond the word list fall back to digits",
      compose_identity_reply([], 14), "I can see 14 people, but I don't recognise any of them.")
check("face_count can never undercount the names it just said",
      compose_identity_reply(["A", "B"], 0), "I can see A and B.")

print("\n" + "=" * 62)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
print("=" * 62)
sys.exit(1 if FAILS else 0)
