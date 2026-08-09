"""A cooldown must suppress the person it was armed for -- and NOBODY ELSE."""
import os
import sys
import types

sys.path.insert(0, os.environ["BRAIN_DIR"])
import pir

F = []
NOW = [1000.0]
pir.time = types.SimpleNamespace(monotonic=lambda: NOW[0], sleep=lambda s: None)


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


def fresh(hold=5.0, verbose=False):
    m = pir.MotionWake.__new__(pir.MotionWake)
    m._sensor = None
    m._on_motion = lambda: fired.append(NOW[0])
    m._verbose = verbose
    m._lock = __import__("threading").Lock()
    m._hold_s = 0.0
    m._cooldown_s = 0.0
    m._chatter_n = 0
    m._chatter_window_s = 0
    m._last_accepted = 0.0
    m._suppress_until = 0.0
    m._suppress_reason = ""
    m.last_wake_at = 0.0
    m._window_start = 0.0
    m._window_count = 0
    m._chatter_warned = False
    m.accepted = 0
    m.rejected_spike = 0
    m.rejected_cooldown = 0
    m.rejected_outcome = 0
    m.rejected_warmup = 0
    m.arrivals_after_gap = 0
    m._warmup_s = 0.0
    m._ready_at = 0.0
    m._last_high = 0.0
    m._presence_hold_s = hold
    m._last_release = 0.0
    m.cleared_on_departure = 0
    m._last_rise_was_spike = False
    m._pin = "4"
    return m


print("\n=== SAME PERSON still there: the cooldown MUST hold (no double greeting) ===")
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")            # greeted at t=1000, deaf until 1060
NOW[0] = 1003.0
m._note_release()                        # their PIR pulse ends
NOW[0] = 1004.2                          # ...and they re-trigger 1.2s later, still standing there
m._fire()
ck("suppressed -- same person", len(fired), 0)
ck("counted as an outcome rejection", m.rejected_outcome, 1)
ck("cooldown NOT cleared", m.cleared_on_departure, 0)

print("\n=== THEY LEFT, someone new arrives: cooldown must be CUT SHORT ===")
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
NOW[0] = 1005.0
m._note_release()                        # they walk off; sensor goes quiet
NOW[0] = 1020.0                          # 15s of silence, then a NEW arrival
m._fire()
ck("woke for the new arrival", len(fired), 1)
ck("cooldown cut short", m.cleared_on_departure, 1)
ck("NOT counted as a rejection", m.rejected_outcome, 0)

print("\n=== the boundary: gap just under vs just over the presence window ===")
for gap, want_wake in ((4.9, False), (5.1, True)):
    fired = []
    m = fresh(hold=5.0)
    NOW[0] = 1000.0
    m.arm_cooldown(60, "greeted")
    NOW[0] = 1010.0
    m._note_release()
    NOW[0] = 1010.0 + gap
    m._fire()
    ck(f"gap {gap}s -> wake", bool(fired), want_wake)

print("\n=== a LONG cooldown still ends normally when nobody comes back ===")
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
NOW[0] = 1061.0                          # past the deadline, no release ever recorded
m._fire()
ck("expired cooldown lets it through", len(fired), 1)
ck("not counted as a departure clear", m.cleared_on_departure, 0)

print("\n=== no falling edge yet (first ever wake) must not falsely clear ===")
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
NOW[0] = 1002.0                          # _last_release still 0
m._fire()
ck("suppressed, since we never saw them leave", len(fired), 0)
ck("no bogus departure clear", m.cleared_on_departure, 0)

print("\n=== PRESENCE_HOLD_S=0 disables the feature entirely ===")
fired = []
m = fresh(hold=0.0)
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
NOW[0] = 1005.0
m._note_release()
NOW[0] = 1050.0                          # a 45s gap -- would clear if enabled
m._fire()
ck("still suppressed (feature off)", len(fired), 0)
ck("no clear", m.cleared_on_departure, 0)

print("\n=== the field scenario: 60s greet cooldown, visitor leaves after 10s ===")
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")            # would have been deaf until 1060
NOW[0] = 1010.0
m._note_release()                        # they leave
NOW[0] = 1025.0                          # a DIFFERENT person arrives 25s in
m._fire()
ck("new visitor served instead of ignored", len(fired), 1)
print(f"   -> deaf time cut from 60s to {1025.0 - 1000.0:.0f}s for this arrival")

print("\n=== P5: arm_cooldown EXTENDS, never shortens ===")
# Suppression means "we have already dealt with whoever is standing there". A second, cheaper look running
# afterwards cannot make that less true, so a short cooldown must not cut a long one short -- otherwise a
# 4s 'silent' armed on the heels of a 60s 'greeted' re-greets the person mid-conversation.
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
ck("long cooldown armed", round(m._suppress_until), 1060)
m.arm_cooldown(4, "silent")              # a cheaper look lands right behind it
ck("short cooldown does NOT shorten it", round(m._suppress_until), 1060)
ck("...and the reason still names the deadline that won", m._suppress_reason, "greeted")
NOW[0] = 1005.0
m._fire()                                # inside what the SHORT one would have allowed
ck("still suppressed at t+5s", len(fired), 0)

print("\n=== ...but a LONGER one still pushes the deadline out ===")
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(4, "silent")
ck("short armed first", round(m._suppress_until), 1004)
m.arm_cooldown(60, "greeted")
ck("longer one wins", round(m._suppress_until), 1060)
ck("reason follows the winner", m._suppress_reason, "greeted")

print("\n=== ...and 0 still CLEARS outright (the documented off-switch) ===")
# 0 is "stop suppressing", not "suppress for zero seconds", so it must beat the extend rule or setting
# ROBOT_CONCIERGE_*_COOLDOWN_S=0 would silently stop working.
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "greeted")
m.arm_cooldown(0, "")
ck("cleared", m._suppress_until, 0.0)
ck("reason cleared too", m._suppress_reason, "")
NOW[0] = 1001.0
m._fire()
ck("every edge served again", len(fired), 1)

print("\n=== extend-only cannot creep the deadline forever ===")
# Each call is max(existing, now + secs), so repeated arming tracks the clock rather than accumulating.
m = fresh()
NOW[0] = 1000.0
for _ in range(20):
    m.arm_cooldown(60, "greeted")
ck("deadline is now+60, not now+1200", round(m._suppress_until), 1060)
NOW[0] = 1030.0
m.arm_cooldown(60, "greeted")
ck("a later arm does push it out", round(m._suppress_until), 1090)

print("\n=== arrivals_after_gap: counts ARRIVALS, not pulses ===")
# This is what lets main.py tell "the person I am talking to kept pulsing the sensor" from "somebody new
# walked up mid-conversation". The field log had the sensor correctly announcing a new arrival and the
# robot throwing that wake away because a chat happened to be running.
fired = []
m = fresh()
NOW[0] = 1000.0
m._fire()                                # first edge of a visit: nothing to have departed from yet
ck("no release seen yet -> not an arrival-after-gap", m.arrivals_after_gap, 0)
NOW[0] = 1001.0
m._note_release()
NOW[0] = 1003.0                          # a 2s gap -- shorter than the 5s hold: the same person, pulsing
m._fire()
ck("a short pulse gap is not an arrival", m.arrivals_after_gap, 0)
NOW[0] = 1004.0
m._note_release()
NOW[0] = 1020.0                          # a 16s gap -- they left
m._fire()
ck("a long quiet gap IS an arrival", m.arrivals_after_gap, 1)
NOW[0] = 1021.0
m._note_release()
NOW[0] = 1040.0
m._fire()
ck("...and it keeps counting", m.arrivals_after_gap, 2)

print("\n=== it counts even when the edge is otherwise REJECTED ===")
# The whole point: a wake suppressed by a cooldown, or debounced, still tells us somebody arrived. If the
# counter only advanced on accepted wakes it would miss exactly the case main.py needs.
fired = []
m = fresh()
NOW[0] = 1000.0
m.arm_cooldown(60, "just-talked")
NOW[0] = 1002.0
m._note_release()
NOW[0] = 1030.0                          # long gap, then an edge while the cooldown is still running
m._fire()
ck("counted despite the cooldown", m.arrivals_after_gap, 1)
ck("...and the cooldown was cleared for them", m.cleared_on_departure, 1)
ck("...so they were served", len(fired), 1)

fired = []
m = fresh()
NOW[0] = 1000.0
m._fire()                                # accepted; starts the 2s debounce
NOW[0] = 1000.5
m._note_release()
NOW[0] = 1010.0                          # long gap, but INSIDE nothing -- debounce long expired
m._fire()
ck("a debounced edge would still count", m.arrivals_after_gap, 1)

print("\n=== PRESENCE_HOLD_S=0 disables arrival counting too (the feature's off-switch) ===")
m = fresh(hold=0.0)
NOW[0] = 1000.0
m._note_release()
NOW[0] = 1100.0
m._fire()
ck("no arrivals counted", m.arrivals_after_gap, 0)

print("\n=== the main.py decision, replicated ===")
# At conversation end: sample the counter, compare with the baseline taken when the chat began.
def chat(gap_during_chat):
    """Returns True when main.py would KEEP the queued wake (somebody new turned up)."""
    mm = fresh()
    NOW[0] = 2000.0
    mm._fire()                                       # the visitor we are about to talk to
    start = mm.arrivals_after_gap                    # <- baseline at conversation start
    NOW[0] = 2001.0
    mm._note_release()
    NOW[0] = 2001.0 + gap_during_chat                # what happens DURING the chat
    mm._fire()
    return mm.arrivals_after_gap > start

ck("same person pulsing (2s gaps) -> discard", chat(2.0), False)
ck("they left and someone arrived (20s gap) -> KEEP", chat(20.0), True)
ck("boundary just under the hold -> discard", chat(4.9), False)
ck("boundary just over the hold -> KEEP", chat(5.2), True)

print("\n" + "=" * 60)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + ', '.join(F)}")
print("=" * 60)
sys.exit(1 if F else 0)
