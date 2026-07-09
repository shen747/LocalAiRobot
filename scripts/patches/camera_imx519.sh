#!/usr/bin/env bash
# scripts/patches/camera_imx519.sh -- switch back to the reference Arducam IMX519 16MP autofocus camera.
# Sets ROBOT_CAMERA_AUTOFOCUS=1 in config/robot.env. The BOOT OVERLAY (dtoverlay=imx519) needs sudo + a
# reboot and, for working autofocus, Arducam's tuning (--imx519-af), so it prints those commands.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Switching back to the reference Arducam IMX519 16MP autofocus camera:"
set_kv ROBOT_CAMERA_AUTOFOCUS 1
echo
echo "   Boot overlay + AF tuning (needs sudo + reboot):"
echo "       ROBOT_CAMERA_OVERLAY=imx519,cam0 ./scripts/install_stack_pi5.sh --imx519-af && sudo reboot"
echo "   Verify:  rpicam-hello --list-cameras (the 'Could not set AF_MODE' warning should be gone)"
echo
echo "${GRN}  PATCH APPLIED (Arducam IMX519) !!!${RST}"
