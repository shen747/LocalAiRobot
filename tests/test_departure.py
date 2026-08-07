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

print("\n" + "=" * 60)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + ', '.join(F)}")
print("=" * 60)
sys.exit(1 if F else 0)
