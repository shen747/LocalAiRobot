#!/usr/bin/env bash
# scripts/patches/hb_qr_1060.sh -- apply the bench-tested config for a HobbyWing QuicRun WP-1060 brushed
# ESC in F/R ("crawler") mode (Mode jumper cap OFF entirely; LIPO cap ON). Rewrites the ESC keys in
# config/robot.env in place (incl. ROBOT_ESC_CHANNEL=14 for this build). Re-runnable (idempotent).
#
# WHY these values (all learned on the bench -- see docs/wiring/pwm-esc-servo-wiring.md):
#   ROBOT_ESC_NEUTRAL=0.25   this ESC's TRUE neutral is 1625us, not 1500us. At 0.0/1500us it slow-reverses
#                            (creeps) and won't arm; at 0.25 it sits dead still, stops clean, and arms
#                            (cell tones + a long "ready" beep). Holding it is also what arms the ESC.
#   ROBOT_ESC_FWD_FLOOR=0.60 big forward dead-band -- nothing pulls until ~1800us, so speed maps onto 0.60..
#   ROBOT_ESC_REV_CEIL=0.80  reverse only engages in a band (~-0.6..-0.8); deeper (-0.88) overshoots + does nothing.
#   ROBOT_ESC_REVERSE_DOUBLETAP=0   F/R jumper = direct reverse (no F/B/R "double-click").
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
# green success banner (only when writing to a real terminal)
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

set_kv() {   # set_kv KEY VALUE -> replace the whole line (adds a [hb_qr_1060] provenance tag), or append it
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}   # [hb_qr_1060]|" "$ENV"
  else
    printf '%s=%s   # [hb_qr_1060]\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-28s = %s\n' "$key" "$val"
}

echo ">> Patching config/robot.env for a HobbyWing QuicRun WP-1060 (F/R direct reverse):"
set_kv ROBOT_ESC_NEUTRAL           0.25
set_kv ROBOT_ESC_FWD_FLOOR         0.60
set_kv ROBOT_ESC_FWD_CEIL          1.00
set_kv ROBOT_ESC_REV_FLOOR         0.60
set_kv ROBOT_ESC_REV_CEIL          0.80
set_kv ROBOT_ESC_REVERSE_DOUBLETAP 0
set_kv ROBOT_THROTTLE_INVERT       0
set_kv ROBOT_ESC_CHANNEL           14

echo
echo ">> QuicRun WP-1060 defaults applied to config/robot.env."
echo "   HARDWARE: pull the ESC's Mode jumper cap OFF entirely (= F/R direct reverse); leave the LIPO cap ON."
echo "   CHANNEL:  set ROBOT_ESC_CHANNEL=14 -- this build's ESC signal lead is on PCA9685 ch14."
echo "             On a DIFFERENT channel? change it with:"
echo "               sed -i 's|^ROBOT_ESC_CHANNEL=.*|ROBOT_ESC_CHANNEL=<N>|' config/robot.env"
echo "   Next:     ESC ON, wheels OFF the ground, then  ./scripts/run_diagnostics.sh --wheels-off"
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED !!!${RST}"
echo "${GRN}===============================${RST}"
