#!/usr/bin/env bash
# run_diagnostics.sh -- pre-first-run HARDWARE WIRING SELF-TEST, on the Pi.
#
# The opposite of dry-run: forces LIVE hardware and checks every wired peripheral (ESC/PWM board,
# motor, servo, 3x ultrasonic, camera, speaker, mic/wake, face), printing a green [v] / red [x] per
# item and a summary that points to the exact doc to fix anything that's wrong. Run it once the car is
# built, wired and powered (LiPo + Pi) and you're SSH'd in.  See docs/diagnostics.md.
#
#   ./scripts/run_diagnostics.sh                # interactive (asks "did it work?" for motor/servo/etc.)
#   ./scripts/run_diagnostics.sh --yes          # non-interactive (motor spin skipped unless --wheels-off)
#   ./scripts/run_diagnostics.sh --wheels-off   # confirm wheels are off the ground -> allow the motor spin
#   ./scripts/run_diagnostics.sh --no-motors    # skip the motor/servo spin tests
#
# SAFETY: the motor test spins the wheels -- keep them OFF the ground / kill switch in hand.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load config (pins, mic, piper paths, face vars); same as run_me.sh / test_sensors.sh.
[ -f config/robot.env ] && { set -a; . config/robot.env; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack.sh on the Pi first." >&2
  exit 1
fi

# Force LIVE hardware: a stray ROBOT_DRY_RUN=1 / ROBOT_MOTORS_OFF=1 in the env must not fake the very
# devices we're trying to test. Flags pass straight through to the Python.
exec env ROBOT_DRY_RUN=0 ROBOT_MOTORS_OFF=0 "$PY" "$REPO/brain/diagnostics.py" "$@"
