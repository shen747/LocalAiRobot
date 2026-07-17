#!/usr/bin/env bash
# test_sensors.sh -- read all three HC-SR04P distance sensors LIVE (Pi 5 OR Jetson). Cover each beam in turn
# and watch ITS number drop -- that's how you confirm front/left/right aren't swapped before trusting
# the obstacle gate. Uses the SAME board HAL backend the robot drives with, and the pins from config/robot.env
# (or the board defaults). Ctrl-C to stop.
#
#   ./scripts/test_sensors.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Load the pin numbers from config (run_me.sh does the same on launch).
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack_pi5.sh (Pi) or install_stack_jetson.sh (Jetson) first." >&2
  exit 1
fi

# Reads the sensors for the configured ROBOT_SENSOR_TYPE (hcsr04 via the board HAL's raw-ping backend, or
# vl53l1x via the I2C ToF backend). Python is fed on stdin so you only ever run this .sh; LAR_REPO lets it
# import brain/ for the HAL.
LAR_REPO="$REPO" exec "$PY" - <<'PYEOF'
import os, sys, time
sys.path.insert(0, os.path.join(os.environ["LAR_REPO"], "brain"))
sensor_type = (os.environ.get("ROBOT_SENSOR_TYPE", "hcsr04").strip().lower() or "hcsr04")
maxm = float(os.environ.get("ROBOT_SENSOR_MAX_DISTANCE_M", "1.0") or 1.0)

if sensor_type == "vl53l1x":
    import hal
    import vl53l1x_sensor as tof
    def _addr(k, d):
        v = os.environ.get(k, "").strip()
        return int(v, 0) if v else d
    p = lambda n: int(os.environ[n])
    plan = [("front", p("ROBOT_TOF_XSHUT_FRONT"), _addr("ROBOT_TOF_ADDR_FRONT", 0x2A)),
            ("left",  p("ROBOT_TOF_XSHUT_LEFT"),  _addr("ROBOT_TOF_ADDR_LEFT", 0x2B)),
            ("right", p("ROBOT_TOF_XSHUT_RIGHT"), _addr("ROBOT_TOF_ADDR_RIGHT", 0x29))]
    i2c = tof.make_i2c()
    tof.assign_addresses(i2c, [(pin, a) for (_n, pin, a) in plan], hal.current().make_digital_output)
    s = {n: tof.VL53L1XSensor(i2c, a, maxm) for (n, _p, a) in plan}
    print("VL53L1X ToF: reading front/left/right every 0.3 s -- cover each beam. Ctrl-C to stop.")
else:
    # Step 26: build via the board HAL (Pi5 / Jetson raw-ping backends), NOT gpiozero -- so this runs on
    # BOTH boards and exercises the EXACT code path the driver drives with. Pins: env > board default_pins()
    # > Pi hard default, resolved once in hal.resolve_hcsr04_pins() (shared with driver.py + diagnostics.py).
    import hal
    board = hal.current()
    pins = hal.resolve_hcsr04_pins()
    s = {name: board.make_distance_sensor(trig, echo, 4.0) for name, (trig, echo) in pins.items()}
    print("Reading front/left/right every 0.3 s -- cover each beam to see its number drop. Ctrl-C to stop.")

while True:
    print("  ".join(f"{k}={v.distance*100:5.1f}cm" for k, v in s.items()))
    time.sleep(0.3)
PYEOF
