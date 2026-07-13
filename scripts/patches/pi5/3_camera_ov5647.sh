#!/usr/bin/env bash
# =============================================================================
#   PATCH:   fixed-focus 5MP OV5647 (or any auto-detected Pi camera) on the Pi 5
#   RUN IF:  your Pi camera is an OV5647 / other official auto-detected CSI sensor
#   BOARD:   Raspberry Pi 5 only
#   DOES:    sets ROBOT_CAMERA_AUTOFOCUS=0 (OV5647 has no autofocus)
# =============================================================================
# The BOOT OVERLAY change (drop dtoverlay=imx519, restore camera_auto_detect=1) needs sudo + a reboot, so it
# prints that command rather than doing it here. Revert to the reference IMX519 with
# scripts/patches/pi5/3_camera_imx519.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Switching to a fixed-focus OV5647 (5MP, auto-detected):"
set_kv ROBOT_CAMERA_AUTOFOCUS 0
echo
echo "   Boot overlay (needs sudo + reboot) -- drop the IMX519 overlay so the sensor auto-detects:"
echo "       ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot"
echo "   Verify:  rpicam-hello --list-cameras   ·   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}  PATCH APPLIED (OV5647 fixed-focus) !!!${RST}"
