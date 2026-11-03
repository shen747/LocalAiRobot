"""The four defects that made brain/zigbee_wake.py unsafe to enable.

The module is dormant (ROBOT_ZIGBEE_WAKE=0) and these were all deferred, so nothing here has ever run in
the field -- which is exactly why it needs a harness before anyone flips the flag. No MQTT broker and no
paho: the tests drive _on_message / _note_availability directly, which is the whole surface the broker
touches.

The four:
  1. a RETAINED message returned BEFORE updating state -- so a retained CLEAR left _occupied True forever,
     and since a wake needs a false->true edge, the sensor could never wake the robot again;
  2. presence_age() was binary 0.0/inf, and CompositeWake takes the MIN across sources, so a Zigbee sensor
     that saw somebody 80s ago drowned out the wired PIR's accurate age;
  3. going OFFLINE (flat battery) left _occupied set, with no possible way to ever clear it;
  4. the departure was stamped when the CLEAR arrived -- 60-90s after the person actually left.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.environ["BRAIN_DIR"])
os.environ["ROBOT_ZIGBEE_TOPIC"] = ""          # unconfigured: no MQTT client is ever built
import zigbee_wake as Z

FAILS = []
FIRED = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        FAILS.append(n)


class Msg:
    def __init__(self, topic, obj, retain=False):
        self.topic = topic
        self.payload = json.dumps(obj).encode()
        self.retain = retain


def mk():
    """A ZigbeeWake wired to a fake callback, with the broker parts bypassed."""
    z = Z.ZigbeeWake.__new__(Z.ZigbeeWake)
    Z.ZigbeeWake.__init__(z, lambda: FIRED.append(1))
    z.available = True                          # pretend the broker connected
    z._topic, z._avail_topic = "z/pir", "z/pir/availability"
    return z


def send(z, occupied, retain=False):
    z._on_message(None, None, Msg("z/pir", {"occupancy": occupied}, retain))


print("=== 1. a RETAINED clear updates state -- the permanent-deafness bug ===")
FIRED.clear()
z = mk()
send(z, True)
ck("a live arrival wakes", len(FIRED), 1)
ck("occupied", z._occupied, True)
send(z, False, retain=True)                     # the broker replays the clear after a reconnect
ck("RETAINED clear still clears the state", z._occupied, False)
ck("...but is not itself a wake", len(FIRED), 1)
ck("counted as retained", z.rejected_retained, 1)
z._last_accepted = 0.0                          # step past the 2s per-source debounce
send(z, True)
ck("the NEXT real arrival can wake again", len(FIRED), 2)

print("\n=== 2. a RETAINED true updates state but never triggers ===")
FIRED.clear()
z = mk()
send(z, True, retain=True)
ck("state follows the broker", z._occupied, True)
ck("no wake from a replay", len(FIRED), 0)
# ...and because the state is now correct, the eventual real clear is a real transition.
send(z, False)
ck("the clear lands as a transition", z._occupied, False)

print("\n=== 3. presence_age AGES -- it must not pin at 0.0 for the occupancy timeout ===")
FIRED.clear()
z = mk()
send(z, True)
ck("fresh presence is ~0", z.presence_age() < 0.5, True)
z._last_occupied_at -= 80.0                     # 80s later, still inside the sensor's occupancy_timeout
aged = z.presence_age()
ck("it has aged to ~80s (old code said 0.0)", 79.0 < aged < 81.0, True)
ck("still reports occupied", z.is_active, True)
# This is what CompositeWake's min() consumes: an honest age lets the wired PIR win when it is fresher.
ck("a fresher wired age would now win the min", min(aged, 0.2), 0.2)
send(z, False)
ck("cleared -> inf", z.presence_age(), float("inf"))

print("\n=== 4. OFFLINE clears occupancy (a flat battery cannot send the clear itself) ===")
FIRED.clear()
z = mk()
send(z, True)
z._online = True
z._note_availability(b"offline")
ck("occupancy dropped", z._occupied, False)
ck("is_active false", z.is_active, False)
ck("presence_age inf", z.presence_age(), float("inf"))
ck("a departure was stamped", z._last_release > 0, True)

print("\n=== 5. the departure is back-dated by the occupancy timeout ===")
FIRED.clear()
z = mk()
t0 = time.monotonic()
send(z, True)
send(z, False)
back_dated = t0 - z._last_release
ck("release sits ~one timeout in the past", 89.0 < back_dated < 91.0, True)
ck("...which is the configured value", z._occupancy_timeout_s, 90.0)
# Why it matters: _fire()'s departure check compares against ROBOT_PIR_PRESENCE_HOLD_S (5s). Taking the
# clear at face value would mean a new arrival looked like "the same person still here" for a full minute.
z._suppress_until = time.monotonic() + 60.0     # a greet cooldown is running
z._suppress_reason = "greeted"
z._last_accepted = 0.0
FIRED.clear()
send(z, True)
ck("a new arrival clears the stale cooldown", z.cleared_on_departure, 1)
ck("...and is woken for", len(FIRED), 1)

print("\n=== degradation: an unconfigured source stays inert through all of this ===")
os.environ["ROBOT_ZIGBEE_TOPIC"] = ""
z2 = Z.ZigbeeWake(lambda: FIRED.append(1))
ck("not available", z2.available, False)
ck("is_active false", z2.is_active, False)
ck("presence_age inf", z2.presence_age(), float("inf"))
ck("stats_line empty", z2.stats_line(), "")
z2.close()

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + str(len(FAILS))}")
print("=" * 58)
sys.exit(1 if FAILS else 0)
