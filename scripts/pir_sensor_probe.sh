#!/usr/bin/env bash
# pir_sensor_probe.sh -- live PIR motion-sensor monitor (the Concierge Mode wake trigger).
#
# Reads the PIR's OUT pin directly and prints MOTION / no-motion live (updating in place) until Ctrl-C, with
# a running count of detections. Use it to confirm the sensor + its wiring respond (wave your hand, watch it
# flip), find the right GPIO, and tune the sensor's sensitivity / time-delay pots.
#
#   ./scripts/pir_sensor_probe.sh          # use ROBOT_PIR_PIN from config/robot.env (default GPIO4/pin 7)
#   ./scripts/pir_sensor_probe.sh 17       # probe a specific BCM pin instead (e.g. while testing wiring)
#
# Works with EITHER supported PIR -- it reads the same pin the brain does, and honours ROBOT_PIR_PULL so the
# two can never disagree about how the pin is biased.
#
# Wiring: OUT -> the GPIO shown below, GND -> GND (pin 6/9/...), and the supply depends on YOUR sensor:
#   HC-SR501 / Adafruit  ->  VCC to 5V   (pin 2 or 4)
#   Panasonic PaPIRs     ->  VDD to 3.3V (pin 1)  -- 5V is past its 4.5V absolute maximum and DESTROYS it
# Docs: docs/wiring/pir-sensor-wiring.md, and BENCH_SCRIPTS.md "Swapping the PIR".
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack_pi5.sh on the Pi first." >&2
  exit 1
fi

exec "$PY" - "$@" <<'PYEOF'
import os, sys, time
from gpiozero import DigitalInputDevice

# BCM -> physical header pin (J8), just for the banner (same map as ultrasonic_sensor_probe.sh).
PHYS = {2:3,3:5,4:7,5:29,6:31,7:26,8:24,9:21,10:19,11:23,12:32,13:33,14:8,15:10,
        16:36,17:11,18:12,19:35,20:38,21:40,22:15,23:16,24:18,25:22,26:37,27:13}

argv = sys.argv[1:]
if any(a in ("-h", "--help") for a in argv):
    print("usage: pir_sensor_probe.sh [BCM_PIN]   (no arg = ROBOT_PIR_PIN from config; Ctrl-C to stop)")
    print("  Works with either supported PIR. Pin bias follows ROBOT_PIR_PULL, the same key brain/pir.py uses.")
    sys.exit(0)
nums = [a for a in argv if not a.startswith("-")]
try:
    pin = int(nums[0]) if nums else int(os.environ.get("ROBOT_PIR_PIN", "4") or 4)
except ValueError:
    print("pin must be a BCM GPIO number (e.g. 4)", file=sys.stderr); sys.exit(2)

ph = PHYS.get(pin, "?")
# Bias the pin the SAME way brain/pir.py does, from the same env key -- otherwise the probe and the robot can
# disagree about the sensor and you end up debugging the difference between two tools instead of the hardware.
pull = (os.environ.get("ROBOT_PIR_PULL", "") or "down").strip().lower()
warmup = os.environ.get("ROBOT_PIR_WARMUP_S", "0") or "0"

print(f"Live PIR  OUT=GPIO{pin} (physical pin {ph}), pull={pull}.  Wave at the sensor.  Ctrl-C to stop.")
print("  OUT idles LOW and goes HIGH on motion (both supported PIRs are active-high).")
print("  Never flips?  Check GND, check OUT is on the pin above, and check the SUPPLY is right for YOUR")
print("                sensor: HC-SR501 wants 5V (pin 2), a Panasonic PaPIRs wants 3.3V (pin 1) and is")
print("                DESTROYED by 5V. Getting those two backwards is the usual cause.")
print("  Stuck HIGH?   It is still holding from the last trigger -- both parts latch for ~2.5s. On an")
print("                HC-SR501 you can turn the TIME/delay pot down; a PaPIRs has no pots (~2.6s, fixed).")
try:
    if float(warmup) > 0:
        print(f"\n  !! WARM-UP: this sensor's output is UNDEFINED for up to {float(warmup):.0f}s after it gets POWER")
        print( "     (ROBOT_PIR_WARMUP_S). This probe cannot tell when yours was powered up, so if you have just")
        print( "     booted or re-plugged it, IGNORE everything below until that long has passed. Random flipping")
        print( "     during the warm-up is normal and is not a fault.")
except ValueError:
    pass
print()

# Raw read of the OUT line, biased per ROBOT_PIR_PULL -- identical mapping to brain/pir.py:
#   down (default) = internal pull-DOWN, active-high. Right for BOTH supported PIRs: the HC-SR501 drives
#                    both states anyway, and the PaPIRs SOURCES current only, so it needs the pull-down to
#                    define its LOW state. Also means an UNWIRED pin reads LOW instead of floating.
#   up             = internal pull-UP, active-LOW (open-collector "alarm" outputs).
#   none           = no resistor at all; only for a sensor that actively drives both states.
if pull in ("none", "float", "off"):
    dev = DigitalInputDevice(pin, pull_up=None, active_state=True)
elif pull == "up":
    dev = DigitalInputDevice(pin, pull_up=True)
else:
    dev = DigitalInputDevice(pin, pull_up=False)
events, last, t_last = 0, False, None
try:
    while True:
        motion = bool(dev.value)
        if motion and not last:                 # rising edge = a new detection
            events += 1
            t_last = time.strftime("%H:%M:%S")
        last = motion
        state = "#### MOTION ####" if motion else " ... no motion ..."
        since = f"  (last {t_last})" if t_last else ""
        print(f"  {state}   detections: {events}{since}   ", end="\r", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nstopped.")
finally:
    dev.close()
PYEOF
