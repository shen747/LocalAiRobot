"""The face must follow the SENSOR, while the retry loop runs on its own longer window."""
import os
import sys

sys.path.insert(0, os.environ["BRAIN_DIR"])
from concierge import presence_probe

F = []
NOW = [0.0]


def clock():
    return NOW[0]


def sleep(s):
    NOW[0] += s


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


SMILE_HOLD = 1.5
RETRY_HOLD = 5.0


def run(last_pulse_at):
    """Sensor pulses until last_pulse_at, then goes quiet. Track the face across the whole look."""
    NOW[0] = 0.0
    faces = []

    def presence_age():
        return 0.0 if NOW[0] <= last_pulse_at else NOW[0] - last_pulse_at

    def on_tick(_last=[None]):
        want = "happy" if presence_age() < SMILE_HOLD else "sleeping"
        if want != _last[0]:
            _last[0] = want
            faces.append((round(NOW[0], 1), want))

    r = presence_probe(lambda: b"j", lambda _: False, attempts=4, interval_s=0.3, sleep=sleep,
                       still_present=lambda: presence_age() < RETRY_HOLD, max_s=20.0,
                       clock=clock, on_tick=on_tick)
    return r, faces, NOW[0]


print("\n=== visitor leaves at 3s: face must drop LONG before the look ends ===")
r, faces, total = run(last_pulse_at=3.0)
print(f"   face changes: {faces}")
print(f"   look ran {total:.1f}s, {r['attempts']} frames")
drop = next((t for t, f in faces if f == "sleeping"), None)
ck("face started happy", faces[0][1], "happy")
ck("face dropped", drop is not None, True)
ck("dropped within ~1.5s of the last pulse", drop is not None and drop <= 3.0 + SMILE_HOLD + 0.4, True)
ck("retry loop kept going AFTER the face dropped", total > drop + 1.0, True)
print(f"   -> smiled {drop:.1f}s, kept looking to {total:.1f}s  ({total - drop:.1f}s of silent hunting)")

print("\n=== BEFORE this fix the face would have stayed happy the whole look ===")
ck("old behaviour would have smiled for", round(total, 1) > 6.0, True)
print(f"   (i.e. {total:.1f}s of smiling; now it is {drop:.1f}s)")

print("\n=== visitor STAYS: face never drops, no flicker ===")
r, faces, total = run(last_pulse_at=10 ** 9)
ck("exactly one face change (happy), never flickers", len(faces), 1)
ck("and it is happy", faces[0][1], "happy")

print("\n=== short pulse GAP must not flicker the face ===")
NOW[0] = 0.0
faces = []
gaps = [(0.0, 2.5), (3.4, 6.0)]      # HIGH 0-2.5s, gap 0.9s, HIGH 3.4-6.0s


def age_gapped():
    t = NOW[0]
    for lo, hi in gaps:
        if lo <= t <= hi:
            return 0.0
    last = max((hi for lo, hi in gaps if hi < t), default=None)
    return (t - last) if last is not None else float("inf")


def tick(_last=[None]):
    want = "happy" if age_gapped() < SMILE_HOLD else "sleeping"
    if want != _last[0]:
        _last[0] = want
        faces.append((round(NOW[0], 1), want))


presence_probe(lambda: b"j", lambda _: False, attempts=4, interval_s=0.3, sleep=sleep,
               still_present=lambda: age_gapped() < RETRY_HOLD, max_s=20.0, clock=clock, on_tick=tick)
ck("0.9s pulse gap did NOT flicker the face", len([f for _, f in faces if f == "sleeping"]) <= 1, True)
print(f"   face changes: {faces}")

print("\n=== a throwing on_tick must never break the look ===")
NOW[0] = 0.0


def boom():
    raise RuntimeError("face panel offline")


r = presence_probe(lambda: b"j", lambda _: False, attempts=4, interval_s=0.3, sleep=sleep,
                   still_present=lambda: False, max_s=20.0, clock=clock, on_tick=boom)
ck("look completed despite a broken face", r["attempts"], 4)

print("\n" + "=" * 60)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + ', '.join(F)}")
print("=" * 60)
sys.exit(1 if F else 0)
