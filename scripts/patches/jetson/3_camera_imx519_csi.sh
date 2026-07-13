#!/usr/bin/env bash
# =============================================================================
#   PATCH:   Arducam IMX519 CSI-ribbon camera on the Jetson (GStreamer/Argus)
#   RUN IF:  Jetson build using the IMX519 CSI camera (not a USB webcam)
#   BOARD:   Jetson only
#   DOES:    sets ROBOT_CAMERA_BACKEND=gstreamer and CLEARS ROBOT_CAMERA_SOURCE
# =============================================================================
# (gstreamer reads a non-empty SOURCE as a full pipeline string, so a leftover '0' from the USB path would
# silently kill the camera.) Re-runnable. On the Pi 5 use pi5/3_camera_imx519.sh.
#
# IMPORTANT: the CSI driver itself is a SEPARATE, JetPack-version-specific Arducam install that this patch
# does NOT run for you (installing the wrong one for your L4T kernel breaks the camera). The command is
# printed below -- match it to YOUR exact JetPack, run it, then reboot.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Jetson." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Selecting the Arducam IMX519 CSI camera (GStreamer/Argus) on the Jetson:"
set_kv ROBOT_CAMERA_BACKEND gstreamer
set_kv ROBOT_CAMERA_SOURCE ""
echo
echo "   STILL REQUIRED -- install Arducam's Jetson driver for YOUR EXACT JetPack (this patch can't: the deb"
echo "   pins to your L4T kernel, and the wrong one breaks the camera):"
echo "       wget https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh"
echo "       chmod +x install_full.sh && ./install_full.sh -m imx519"
echo "   Then:  sudo apt-mark hold nvidia-l4t-kernel   (so an apt upgrade can't silently revert the driver),"
echo "          sudo reboot, and verify with:  ./scripts/run_diagnostics.sh"
echo
echo "${GRN}  PATCH APPLIED (Jetson IMX519 CSI) !!!${RST}"
