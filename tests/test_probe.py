"""presence_probe: keep looking while the sensor asserts, without ever running away."""
import os
import sys

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())
from concierge import presence_probe

FAILS = []
NOW = [0.0]


def clock():
    return NOW[0]


def sleep(s):
    NOW[0] += s


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def run(detect_on=None, held_until=999, tries=4, gap=0.3, cap=6.0, frames=200):
    """detect_on = attempt number that finds a face (None = never). held_until = sensor stays HIGH
    until this many attempts have happened."""
    NOW[0] = 0.0
    n = [0]

    def capture():
        n[0] += 1
        return b"jpeg" if n[0] <= frames else None

    def detect(_):
        return detect_on is not None and n[0] >= detect_on

    return presence_probe(capture, detect, attempts=tries, interval_s=gap, sleep=sleep,
                          still_present=lambda: n[0] < held_until, max_s=cap, clock=clock), NOW[0]


print("\n=== THE POINT: sensor still HIGH -> keep looking past the floor ===")
r, t = run(detect_on=9, held_until=999)
check("found on attempt 9, well past the 4-try floor", r["attempts"], 9)
check("present", r["present"], True)

print("\n=== sensor goes LOW after the floor -> stop, don't burn frames ===")
r, t = run(detect_on=None, held_until=5)
check("stopped when the sensor released", r["attempts"], 5)
check("not present", r["present"], False)

print("\n=== STUCK-HIGH SENSOR MUST NOT RUN AWAY (the ceiling) ===")
r, t = run(detect_on=None, held_until=10**9, cap=6.0, gap=0.3)
check("bounded by max_s, did not loop forever", r["attempts"] <= 25, True)
check("stopped at/after the 6s ceiling", round(t, 1) >= 6.0, True)
print(f"   (took {r['attempts']} frames over {t:.1f}s simulated)")

print("\n=== floor is always honoured even if the sensor already released ===")
r, t = run(detect_on=None, held_until=1, tries=4)
check("still took the full 4-try floor", r["attempts"], 4)

print("\n=== BACKWARD COMPAT: no still_present -> old fixed budget, exactly ===")
NOW[0] = 0.0
n = [0]
r = presence_probe(lambda: (n.__setitem__(0, n[0] + 1), b"j")[1], lambda _: False,
                   attempts=4, interval_s=0.3, sleep=sleep)
check("exactly the floor, no more", r["attempts"], 4)

print("\n=== max_s=0 also disables it (the documented off switch) ===")
r, t = run(detect_on=None, held_until=10**9, cap=0.0)
check("fixed budget when cap is 0", r["attempts"], 4)

print("\n=== early return still short-circuits: someone already at the door ===")
r, t = run(detect_on=1, held_until=999)
check("one frame, no extra cost", r["attempts"], 1)
check("present", r["present"], True)

print("\n=== a broken still_present must stop, not spin ===")
NOW[0] = 0.0
n = [0]


def boom():
    raise RuntimeError("sensor read failed")


r = presence_probe(lambda: (n.__setitem__(0, n[0] + 1), b"j")[1], lambda _: False,
                   attempts=4, interval_s=0.3, sleep=sleep, still_present=boom, max_s=6.0, clock=clock)
check("bailed at the floor instead of spinning", r["attempts"], 4)

print("\n=== a dead camera cannot spin the CPU (100ms floor on failed captures) ===")
NOW[0] = 0.0
r = presence_probe(lambda: None, lambda _: False, attempts=4, interval_s=0.0, sleep=sleep,
                   still_present=lambda: True, max_s=1.0, clock=clock)
check("backed off despite interval_s=0", NOW[0] >= 0.3, True)

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
print("=" * 58)
sys.exit(1 if FAILS else 0)
