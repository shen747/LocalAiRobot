"""Prove the OR wake works in all three hardware modes, with no hardware.

Fakes stand in for MotionWake / ZigbeeWake -- what is under test is CompositeWake's contract with
brain/main.py, which is the thing that breaks silently.
"""
import os
import sys
import time

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


class FakeSource:
    """Stands in for a wake source; mirrors MotionWake's surface."""

    def __init__(self, name, available=True, has_spike=True):
        self.source_name = name
        self.available = available
        self.accepted = 0
        self.rejected_outcome = 0
        self.rejected_cooldown = 0
        if has_spike:                       # ZigbeeWake has no spike filter -> attribute absent on purpose
            self.rejected_spike = 0
        self.last_wake_at = 0.0
        self.cooldowns = []
        self.closed = False
        self._cb = None
        self._suppress_until = 0.0

    def bind(self, cb):
        self._cb = cb

    def fire(self, edge_ago=0.0, stamp=True):
        """Simulate a detection, honouring the outcome cooldown like the real sources do.

        `edge_ago` models MotionWake's hold sleep: the real source stamps last_wake_at at the moment the
        PIN went high and only THEN sleeps ROBOT_PIR_HOLD_S before calling back, so by callback time the
        stamp is already `edge_ago` seconds old. `stamp=False` models a source that never sets it at all."""
        if self._suppress_until and time.monotonic() < self._suppress_until:
            self.rejected_outcome += 1
            return
        self.accepted += 1
        if stamp:
            self.last_wake_at = time.monotonic() - edge_ago
        self._cb()

    def arm_cooldown(self, seconds, reason=""):
        self.cooldowns.append((round(float(seconds), 3), reason))
        self._suppress_until = time.monotonic() + float(seconds) if seconds > 0 else 0.0

    def stats_line(self):
        return f"{self.source_name}: woke {self.accepted}x" if self.available else ""

    def close(self):
        self.closed = True


def build(sources, dedupe_s=2.0):
    from wake_sources import CompositeWake
    fired = []
    c = CompositeWake(lambda: fired.append(time.monotonic()), dedupe_s=dedupe_s)
    for s in sources:
        s.bind(c.make_callback(s.source_name))
        c.add(s)
    return c, fired


print("\n=== MODE 1: GPIO PIR only (no dongle) -- must behave exactly as before ===")
gpio = FakeSource("gpio-pir")
c, fired = build([gpio, FakeSource("zigbee-pir", available=False, has_spike=False)])
check("available with one live source", c.available, True)
gpio.fire()
check("wired PIR wakes the robot", len(fired), 1)
check("winner", c.winner, "gpio-pir")
check("dead source contributes no stats", "zigbee-pir" in c.stats_line(), False)

print("\n=== MODE 2: BOTH present -- OR, with duplicates collapsed ===")
gpio = FakeSource("gpio-pir")
zig = FakeSource("zigbee-pir", has_spike=False)
c, fired = build([gpio, zig])
check("available", c.available, True)
gpio.fire()                                   # wired wins the race
zig.fire()                                    # same person, 0.0s later
check("two sensors, ONE wake", len(fired), 1)
check("winner is the one that got there first", c.winner, "gpio-pir")
check("duplicate collapsed", c.deduped, 1)
check("accepted is robot wakes, not the sum", c.accepted, 1)
check("dedupe counts as debounce for the summary", c.rejected_cooldown, 1)
time.sleep(2.1)                               # outside the dedupe window = a genuine new arrival
zig.fire()
check("later Zigbee hit is a NEW wake", len(fired), 2)
check("winner switched", c.winner, "zigbee-pir")
check("wins tallied per source", dict(c.wins), {"gpio-pir": 1, "zigbee-pir": 1})

print("\n=== MODE 3: Zigbee ONLY (ROBOT_PIR_PIN empty / PIR unplugged) ===")
gpio = FakeSource("gpio-pir", available=False)
zig = FakeSource("zigbee-pir", has_spike=False)
c, fired = build([gpio, zig])
check("available on the wireless source alone", c.available, True)
zig.fire()
check("Zigbee alone wakes the robot", len(fired), 1)
check("winner", c.winner, "zigbee-pir")

print("\n=== MODE 4: NEITHER available -- wake-word-only, no crash ===")
c, fired = build([FakeSource("gpio-pir", available=False),
                  FakeSource("zigbee-pir", available=False, has_spike=False)])
check("available", c.available, False)
check("stats_line empty", c.stats_line(), "")
check("counters still readable", (c.accepted, c.rejected_spike), (0, 0))

print("\n=== THE LOAD-BEARING ONE: arm_cooldown must reach EVERY source ===")
gpio = FakeSource("gpio-pir")
zig = FakeSource("zigbee-pir", has_spike=False)
c, fired = build([gpio, zig])
c.arm_cooldown(5, "silent")
check("wired PIR got the cooldown", gpio.cooldowns, [(5.0, "silent")])
check("Zigbee got the SAME cooldown", zig.cooldowns, [(5.0, "silent")])
zig.fire()                                    # would fire straight through a PIR-only cooldown
check("Zigbee suppressed by the outcome cooldown", len(fired), 0)
check("attributed to outcome, not debounce", c.rejected_outcome, 1)

print("\n=== main.py's hourly summary reads four counters straight off the object ===")
gpio = FakeSource("gpio-pir")
zig = FakeSource("zigbee-pir", has_spike=False)   # deliberately LACKS rejected_spike
c, fired = build([gpio, zig])
gpio.rejected_spike = 7
zig.rejected_outcome = 3
for attr in ("accepted", "rejected_outcome", "rejected_cooldown", "rejected_spike"):
    v = getattr(c, attr)
    print(f"  PASS  {attr} readable and int: {v}")
    if not isinstance(v, int):
        FAILS.append(attr)
check("spike summed despite a source lacking it", c.rejected_spike, 7)
check("outcome summed across sources", c.rejected_outcome, 3)

print("\n=== close() reaches every source, even a dead one ===")
gpio = FakeSource("gpio-pir")
zig = FakeSource("zigbee-pir", available=False, has_spike=False)
c, _ = build([gpio, zig])
c.close()
check("live source closed", gpio.closed, True)
check("dead source closed too", zig.closed, True)

print("\n=== ZigbeeWake with no topic = inert, importable, no paho needed ===")
os.environ.pop("ROBOT_ZIGBEE_TOPIC", None)
from zigbee_wake import ZigbeeWake
z = ZigbeeWake(lambda: None)
check("unconfigured -> available False", z.available, False)
check("stats_line empty", z.stats_line(), "")
check("counters present for the composite", (z.rejected_spike, z.rejected_warmup), (0, 0))
z.close()
check("close() on an unconfigured source is safe", True, True)

print("\n=== P6: the composite reports the SOURCE's edge time, not the callback time ===")
# MotionWake stamps last_wake_at when the PIN went high, BEFORE sleeping ROBOT_PIR_HOLD_S to filter spikes.
# The composite used to re-stamp at callback time, i.e. AFTER that sleep, silently shaving hold_s off every
# "[N.NNs from PIR to decision]" line -- the one number used to judge whether the greeter feels quick.
gpio = FakeSource("gpio-pir")
c, fired = build([gpio])
t0 = time.monotonic()
gpio.fire(edge_ago=0.25)                  # pin went high 250ms ago; the hold sleep has since elapsed
check("wake accepted", len(fired), 1)
check("composite adopted the source's edge time", abs(c.last_wake_at - gpio.last_wake_at) < 0.01, True)
check("...which is EARLIER than the callback time, by the hold", c.last_wake_at < t0 - 0.2, True)

print("\n=== ...and a source with no edge clock still works (falls back to now) ===")
zig = FakeSource("zigbee-pir", has_spike=False)
c2, fired2 = build([zig])
t0 = time.monotonic()
zig.fire(stamp=False)                     # never stamped -- e.g. an event timed by the broker
check("wake accepted", len(fired2), 1)
check("stamped at callback time instead", c2.last_wake_at >= t0, True)

print("\n=== ...and a deduped second source does NOT move the stamp ===")
a = FakeSource("gpio-pir")
b = FakeSource("zigbee-pir", has_spike=False)
c3, fired3 = build([a, b], dedupe_s=5.0)
a.fire(edge_ago=0.30)
first_stamp = c3.last_wake_at
b.fire()                                   # same arrival, inside the dedupe window
check("one wake only", len(fired3), 1)
check("deduped", c3.deduped, 1)
check("stamp unchanged by the dedupe", c3.last_wake_at, first_stamp)

print("\n" + ("=" * 62))
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
print("=" * 62)
sys.exit(1 if FAILS else 0)
