#!/usr/bin/env bash
# =============================================================================
#   PATCH:   3x VL53L1X laser Time-of-Flight obstacle sensors (I2C)
#   RUN IF:  your obstacle sensors are VL53L1X ToF (not HC-SR04P ultrasonic)
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    sets ROBOT_SENSOR_TYPE=vl53l1x (the 6 HC-SR04 TRIG/ECHO pins go unused)
# =============================================================================
# Set the ROBOT_TOF_XSHUT_* pins to YOUR wiring first (or accept the shipped example pins). Re-runnable.
# Revert to ultrasonic with:  ./scripts/patches/common/2_sensor_hcsr04.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-22s = %s\n' "$key" "$val"
}

echo ">> Switching obstacle sensors to VL53L1X laser ToF:"
set_kv ROBOT_SENSOR_TYPE vl53l1x
echo
echo "   XSHUT pins currently in config/robot.env (edit to match your wiring):"
grep -E '^ROBOT_TOF_(XSHUT|ADDR)_' "$ENV" | sed 's/^/     /' || true
echo
echo "   Dependency: if you added ToF AFTER installing, add the driver to the venv:"
echo "       ./brain/.venv/bin/pip install adafruit-circuitpython-vl53l1x"
echo "   Verify:  i2cdetect shows 0x29/0x2A/0x2B  ·  ./scripts/test_sensors.sh tracks each beam"
echo "   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED (VL53L1X ToF) !!!${RST}"
echo "${GRN}===============================${RST}"
