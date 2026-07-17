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
# Wiring: OUT -> the GPIO shown below, VCC -> 5V (pin 2/4), GND -> GND (pin 6/9/...). Docs:
# docs/wiring/pir-sensor-wiring.md.
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
    sys.exit(0)
nums = [a for a in argv if not a.startswith("-")]
try:
    pin = int(nums[0]) if nums else int(os.environ.get("ROBOT_PIR_PIN", "4") or 4)
except ValueError:
    print("pin must be a BCM GPIO number (e.g. 4)", file=sys.stderr); sys.exit(2)

ph = PHYS.get(pin, "?")
print(f"Live PIR  OUT=GPIO{pin} (physical pin {ph}).  Wave your hand in front of the sensor.  Ctrl-C to stop.")
print("  PIR OUT idles LOW and goes HIGH on motion. Stuck HIGH = still 'holding' from the last trigger")
print("  (turn the TIME/delay pot down). Never flips = check VCC->5V, GND->GND, OUT->the pin above.\n")

# Raw read of the OUT line. pull_up=False = internal pull-DOWN (PIRs are active-high), so an UNWIRED pin
# reads LOW instead of floating -- matches how the brain constructs the sensor.
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
