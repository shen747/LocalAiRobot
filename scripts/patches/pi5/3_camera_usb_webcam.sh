#!/usr/bin/env bash
# =============================================================================
#   PATCH:   USB webcam instead of the CSI ribbon camera on the Pi 5
#   RUN IF:  your Pi camera is a USB webcam (not the CSI ribbon camera)
#   BOARD:   Raspberry Pi 5 only
#   DOES:    sets ROBOT_CAMERA_BACKEND=v4l2 + ROBOT_CAMERA_SOURCE=<index>, installs OpenCV
# =============================================================================
# A UVC webcam needs no boot overlay, no autofocus fork and no 22->15 ribbon (cv2 is not in requirements.txt,
# so this installs it). Re-runnable. Revert to the CSI camera with scripts/patches/pi5/3_camera_imx519.sh.
#   Usage:  ./scripts/patches/pi5/3_camera_usb_webcam.sh [index]   (index defaults to 0; or a /dev/videoN path)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
IDX="${1:-0}"    # webcam index (ROBOT_CAMERA_SOURCE); 0 for a single USB cam, or a /dev/videoN path.
set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Selecting a USB webcam (v4l2) as the Pi camera:"
set_kv ROBOT_CAMERA_BACKEND v4l2
set_kv ROBOT_CAMERA_SOURCE "$IDX"
PY="$REPO/brain/.venv/bin/python"
if [ -x "$PY" ]; then
  echo "   installing OpenCV (opencv-python) into the brain venv -- the v4l2 path needs cv2..."
  "$PY" -m pip install --quiet opencv-python \
    || echo "   (pip build failed -- try: sudo apt install -y python3-opencv  [visible via the venv's --system-site-packages])"
else
  echo "   (brain venv not found -- after install_stack_pi5.sh, run: ./brain/.venv/bin/pip install opencv-python)"
fi
echo
echo "   ROBOT_CAMERA_SOURCE is the webcam's index (0 for a single USB cam). If a CSI sensor also claims"
echo "   /dev/video0 the webcam moves up -- list devices with:  v4l2-ctl --list-devices  (or set a /dev/videoN path)."
echo "   Coming FROM the reference IMX519 build? free /dev/video0 by dropping the CSI overlay (needs sudo + reboot):"
echo "       ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot"
echo "   Verify:  v4l2-ctl --list-devices  +  ./scripts/run_diagnostics.sh   ·   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}  PATCH APPLIED (Pi USB webcam) !!!${RST}"
