#!/usr/bin/env bash
# test_sensors.sh -- read all three HC-SR04P distance sensors LIVE, on the Pi. Cover each beam in turn
# and watch ITS number drop -- that's how you confirm front/left/right aren't swapped before trusting
# the obstacle gate. Uses the GPIO pins from config/robot.env. Ctrl-C to stop.
#
#   ./scripts/test_sensors.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Load the pin numbers from config (run_me.sh does the same on launch).
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack.sh on the Pi first." >&2
  exit 1
fi

# gpiozero reads the sensors; Python is fed on stdin so you only ever run this .sh.
exec "$PY" - <<'PYEOF'
import os, time
from gpiozero import DistanceSensor
p = lambda n: int(os.environ[n])
s = {
    "front": DistanceSensor(echo=p("ROBOT_ECHO_PIN"),       trigger=p("ROBOT_TRIG_PIN"),       max_distance=4.0),
    "left":  DistanceSensor(echo=p("ROBOT_LEFT_ECHO_PIN"),  trigger=p("ROBOT_LEFT_TRIG_PIN"),  max_distance=4.0),
    "right": DistanceSensor(echo=p("ROBOT_RIGHT_ECHO_PIN"), trigger=p("ROBOT_RIGHT_TRIG_PIN"), max_distance=4.0),
}
print("Reading front/left/right every 0.3 s -- cover each beam to see its number drop. Ctrl-C to stop.")
while True:
    print("  ".join(f"{k}={v.distance*100:5.1f}cm" for k, v in s.items()))
    time.sleep(0.3)
PYEOF
