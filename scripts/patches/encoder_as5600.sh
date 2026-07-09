#!/usr/bin/env bash
# scripts/patches/encoder_as5600.sh -- enable the OPTIONAL AS5600 12-bit magnetic rotary encoder. Opt-in.
# Sets ROBOT_ENCODER_TYPE=as5600 in config/robot.env and installs its pip lib into the brain venv. Lands
# only the reader (brain/encoder.py) + config -- steering-feedback / odometry are future work. Disable by
# clearing ROBOT_ENCODER_TYPE.
# NB: the AS5600 default address 0x36 CLASHES with the UPS-HAT fuel gauge on the Pi build -- if you fit both,
# put the encoder on a separate I2C bus (the AS5600 address is not runtime-remappable).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-22s = %s\n' "$k" "$v"
}
echo ">> Enabling the AS5600 magnetic rotary encoder:"
set_kv ROBOT_ENCODER_TYPE as5600
PY="$REPO/brain/.venv/bin/python"
if [ -x "$PY" ]; then
  echo "   installing adafruit-circuitpython-as5600 into the brain venv..."
  "$PY" -m pip install --quiet adafruit-circuitpython-as5600 || echo "   (pip install failed -- run it by hand)"
else
  echo "   (brain venv not found -- after install, run: ./brain/.venv/bin/pip install adafruit-circuitpython-as5600)"
fi
echo "   Self-test:  ROBOT_ENCODER_TYPE=as5600 ${PY} ${REPO}/brain/encoder.py   (prints a live angle)"
echo "${GRN}  PATCH APPLIED (AS5600 encoder) !!!${RST}"
