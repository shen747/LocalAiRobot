#!/usr/bin/env bash
# calibrate_steering.sh -- interactive STEERING SERVO calibrator, on the Pi.
#
# Moves ONLY the steering servo (never the ESC/motor), so it's SAFE with the wheels on the ground.
# Nudge the servo live, mark CENTER / LEFT / RIGHT while watching the wheels, then save straight into
# config/robot.env (it backs the file up to robot.env.bak first). See the on-screen key legend.
#
#   ./scripts/calibrate_steering.sh              # calibrate the servo on ROBOT_STEER_CHANNEL
#   ./scripts/calibrate_steering.sh -c 1         # override the channel
#
# Angles map 1:1 to ROBOT_STEER_CENTER/LEFT/RIGHT -- the driver uses the same 500-2500us / 180deg range.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load config (channel + current steer angles); same as run_me.sh / run_diagnostics.sh.
[ -f config/robot.env ] && { set -a; . config/robot.env; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack.sh on the Pi first." >&2
  exit 1
fi

exec "$PY" "$REPO/brain/calibrate_steering.py" "$@"
