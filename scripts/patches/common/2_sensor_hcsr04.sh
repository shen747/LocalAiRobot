#!/usr/bin/env bash
# =============================================================================
#   PATCH:   3x HC-SR04P ultrasonic obstacle sensors  (the reference / default)
#   RUN IF:  your obstacle sensors are HC-SR04P ultrasonic (not VL53L1X ToF)
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    sets ROBOT_SENSOR_TYPE=hcsr04 (the TRIG/ECHO pins take over; ROBOT_TOF_* ignored)
# =============================================================================
# Re-runnable (idempotent). Opposite of common/2_sensor_vl53l1x.sh.
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

echo ">> Switching obstacle sensors back to HC-SR04P ultrasonics:"
set_kv ROBOT_SENSOR_TYPE hcsr04
echo
echo "   The TRIG/ECHO pins in config/robot.env are used again:"
grep -E '^ROBOT_(LEFT_|RIGHT_)?(TRIG|ECHO)_PIN=' "$ENV" | sed 's/^/     /' || true
echo "   Verify:  ./scripts/test_sensors.sh   ·   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED (HC-SR04P) !!!${RST}"
echo "${GRN}===============================${RST}"
