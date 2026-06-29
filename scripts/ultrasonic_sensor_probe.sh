#!/usr/bin/env bash
# ultrasonic_sensor_probe.sh -- live HC-SR04 monitor for ONE sensor at a time (bypasses gpiozero's
# DistanceSensor).
#
# Drives a raw ~10 us TRIG pulse and reads ECHO directly, printing the distance live (updating in place)
# until Ctrl-C. Use it to confirm a sensor + its wiring respond, and to reseat jumpers while watching the
# number track your hand.
#
#   ./scripts/ultrasonic_sensor_probe.sh --front     # live-read the front sensor (Ctrl-C to stop)
#   ./scripts/ultrasonic_sensor_probe.sh --left
#   ./scripts/ultrasonic_sensor_probe.sh --right
#
# Pins come from config/robot.env (ROBOT_TRIG_PIN/ECHO_PIN, ROBOT_LEFT_*, ROBOT_RIGHT_*).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack.sh on the Pi first." >&2
  exit 1
fi

exec "$PY" - "$@" <<'PYEOF'
import os, sys, time
from gpiozero import DigitalOutputDevice, DigitalInputDevice

g = lambda n, d: int(os.environ.get(n, d) or d)
SENSORS = {
    "front": (g("ROBOT_TRIG_PIN", 23),       g("ROBOT_ECHO_PIN", 24)),
    "left":  (g("ROBOT_LEFT_TRIG_PIN", 5),   g("ROBOT_LEFT_ECHO_PIN", 6)),
    "right": (g("ROBOT_RIGHT_TRIG_PIN", 20), g("ROBOT_RIGHT_ECHO_PIN", 21)),
}
# BCM -> physical header pin (J8), just for the banner.
PHYS = {2:3,3:5,4:7,5:29,6:31,7:26,8:24,9:21,10:19,11:23,12:32,13:33,14:8,15:10,
        16:36,17:11,18:12,19:35,20:38,21:40,22:15,23:16,24:18,25:22,26:37,27:13}
ph = lambda n: PHYS.get(n, "?")


def ping(trig, echo):
    """One raw trigger + echo -> distance in cm, or None if no echo came back."""
    trig.off(); time.sleep(0.003); trig.on(); time.sleep(11e-6); trig.off()
    t0 = time.time()
    while echo.value == 0 and time.time() - t0 < 0.04:      # wait up to 40 ms for ECHO to rise
        pass
    if echo.value == 0:
        return None
    rise = time.time()
    while echo.value == 1 and time.time() - rise < 0.04:
        pass
    return (time.time() - rise) * 34300.0 / 2.0


def monitor(name, tp, ep):
    print(f"Live {name.upper()}  TRIG GPIO{tp}(pin{ph(tp)}) / ECHO GPIO{ep}(pin{ph(ep)})  --  Ctrl-C to stop.\n")
    trig = DigitalOutputDevice(tp); echo = DigitalInputDevice(ep)
    try:
        while True:
            d = ping(trig, echo)
            print("  " + (f"{d:6.1f} cm   " if d is not None else "no echo    "), end="\r", flush=True)
            time.sleep(0.15)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        trig.close(); echo.close()


pick = next((n for n in SENSORS if f"--{n}" in sys.argv[1:]), None)
if pick:
    monitor(pick, *SENSORS[pick])
else:
    print("usage: ultrasonic_sensor_probe.sh --front | --left | --right   (live read one sensor; Ctrl-C to stop)")
    sys.exit(2)
PYEOF
