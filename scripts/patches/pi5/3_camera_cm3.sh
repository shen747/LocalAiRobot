#!/usr/bin/env bash
# =============================================================================
#   PATCH:   official Raspberry Pi Camera Module 3 (IMX708) on the Pi 5
#   RUN IF:  your Pi camera is a Camera Module 3 / IMX708 (CSI)
#   BOARD:   Raspberry Pi 5 only
#   DOES:    sets ROBOT_CAMERA_AUTOFOCUS=1 (CM3 auto-detects + has first-class AF, no fork)
# =============================================================================
# The BOOT OVERLAY change (drop dtoverlay=imx519, restore camera_auto_detect=1) needs sudo + a reboot, so it
# prints that command rather than doing it here. Revert with scripts/patches/pi5/3_camera_imx519.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Switching to the official Camera Module 3 (IMX708, first-class autofocus):"
set_kv ROBOT_CAMERA_AUTOFOCUS 1
echo
echo "   Boot overlay (needs sudo + reboot) -- drop the IMX519 overlay so CM3 auto-detects:"
echo "       ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot"
echo "   Verify:  rpicam-hello --list-cameras   ·   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}  PATCH APPLIED (Camera Module 3) !!!${RST}"
