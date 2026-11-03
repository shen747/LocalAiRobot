"""Sticky presence must survive the LOW gaps of a real PIR pulse train."""
import os
import sys
import types

sys.path.insert(0, os.environ["BRAIN_DIR"])
import pir

F = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


class FakePin:
    def __init__(self):
        self.value = False


NOW = [1000.0]
pir.time = types.SimpleNamespace(monotonic=lambda: NOW[0], sleep=lambda s: None)

m = pir.MotionWake.__new__(pir.MotionWake)
m._sensor = FakePin()
m._last_high = 0.0
m._presence_hold_s = 5.0

print("\n=== SEN0171 pulse train: HIGH ~2.5s, LOW ~1.5s, repeat ===")
m._sensor.value = True
NOW[0] = 1000.0
ck("pulse high -> present", m.is_active, True)
NOW[0] = 1002.5
m._sensor.value = False
ck("gap starts, still present (sticky)", m.is_active, True)
NOW[0] = 1003.9
ck("1.4s into the gap -- STILL present", m.is_active, True)

print("\n=== the bug this fixes: a bare pin read fails right here ===")
ck("live pin is LOW mid-gap", bool(m._sensor.value), False)
ck("...but presence correctly says True", m.is_active, True)

print("\n=== next pulse re-stamps ===")
m._sensor.value = True
NOW[0] = 1004.0
ck("re-triggered", m.is_active, True)

print("\n=== person LEAVES -> expires one hold window after the LAST pulse ===")
m._sensor.value = True
NOW[0] = 1010.0
m.is_active                      # last confirmed pulse, stamps 1010.0
m._sensor.value = False
NOW[0] = 1014.9
ck("4.9s after last pulse -- still present", m.is_active, True)
NOW[0] = 1015.1
ck("5.1s after last pulse -- GONE", m.is_active, False)

print("\n=== staying put extends indefinitely (each pulse re-stamps) ===")
for t in range(0, 120, 4):
    NOW[0] = 2000.0 + t
    m._sensor.value = True
    m.is_active
    NOW[0] = 2000.0 + t + 2.5
    m._sensor.value = False
ck("still present after 2 minutes of pulses", m.is_active, True)

print("\n=== HOLD_S=0 restores the bare pin read (documented off switch) ===")
m._presence_hold_s = 0.0
m._sensor.value = False
NOW[0] = 3000.0
ck("no stickiness", m.is_active, False)

print("\n=== a caller can never hang on it ===")
m2 = pir.MotionWake.__new__(pir.MotionWake)
m2._sensor = None
m2._last_high = 0.0
m2._presence_hold_s = 5.0
ck("no sensor -> False", m2.is_active, False)


class Boom:
    @property
    def value(self):
        raise RuntimeError("read failed")


m3 = pir.MotionWake.__new__(pir.MotionWake)
m3._sensor = Boom()
m3._last_high = 0.0
m3._presence_hold_s = 5.0
ck("broken read -> False, no raise", m3.is_active, False)

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + ', '.join(F)}")
print("=" * 58)
sys.exit(1 if F else 0)
