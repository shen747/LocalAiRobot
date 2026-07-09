#!/usr/bin/env bash
# scripts/patches/imu_bno085.sh -- enable the OPTIONAL BNO085/BNO08x 9-DOF IMU (heading + yaw). Opt-in.
# Sets ROBOT_IMU_TYPE=bno085 in config/robot.env and installs its pip lib into the brain venv. This lands
# only the reader (brain/imu.py) + config -- closed-loop turns / heading-hold are future work. Disable by
# clearing ROBOT_IMU_TYPE (sed -i 's|^ROBOT_IMU_TYPE=.*|ROBOT_IMU_TYPE=|' config/robot.env).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-20s = %s\n' "$k" "$v"
}
echo ">> Enabling the BNO085 9-DOF IMU:"
set_kv ROBOT_IMU_TYPE bno085
PY="$REPO/brain/.venv/bin/python"
if [ -x "$PY" ]; then
  echo "   installing adafruit-circuitpython-bno08x into the brain venv..."
  "$PY" -m pip install --quiet adafruit-circuitpython-bno08x || echo "   (pip install failed -- run it by hand)"
else
  echo "   (brain venv not found -- after install, run: ./brain/.venv/bin/pip install adafruit-circuitpython-bno08x)"
fi
echo "   Self-test:  ROBOT_IMU_TYPE=bno085 ${PY} ${REPO}/brain/imu.py   (prints a live heading)"
echo "${GRN}  PATCH APPLIED (BNO085 IMU) !!!${RST}"
