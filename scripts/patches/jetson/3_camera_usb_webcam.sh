#!/usr/bin/env bash
# scripts/patches/jetson/3_camera_usb_webcam.sh -- use a USB webcam on the Jetson (the DEFAULT / easiest
# camera path). Sets ROBOT_CAMERA_BACKEND=v4l2 + ROBOT_CAMERA_SOURCE=0 in config/robot.env. install_stack_jetson.sh
# auto-applies this when it spots a webcam at install time; run this patch by hand if you plugged the camera
# in later, or it landed on a different index. Re-runnable. (JETSON-ONLY: the Pi 5 always uses picamera2 and
# ignores ROBOT_CAMERA_BACKEND -- on a Pi use the pi5/ camera patches.)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Jetson." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}
echo ">> Selecting a USB webcam (v4l2) as the Jetson camera:"
set_kv ROBOT_CAMERA_BACKEND v4l2
set_kv ROBOT_CAMERA_SOURCE 0
echo
echo "   ROBOT_CAMERA_SOURCE is the webcam's index. It's 0 for a single USB cam; if a CSI sensor also claims"
echo "   /dev/video0 the webcam moves up -- list devices with:  v4l2-ctl --list-devices  (or set a /dev/videoN path)."
echo "   Verify:  ./scripts/run_diagnostics.sh   ·   Reload a running robot:  ./scripts/run_me.sh --reload"
echo
echo "${GRN}  PATCH APPLIED (Jetson USB webcam) !!!${RST}"
