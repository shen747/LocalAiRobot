#!/usr/bin/env bash
# =============================================================================
#   PATCH:   1080p USB webcam with IR-CUT night vision (fixed focus) on the Pi 5
#   RUN IF:  your camera is a 1080p UVC module with IR LEDs + auto IR-CUT, e.g. the
#            HCVSIGHT / ELP-style 38x38mm boards with a 3.6mm fixed lens
#   BOARD:   Raspberry Pi 5 only
#   DOES:    v4l2 backend at 1920x1080, a 768px square target, autofocus OFF
# =============================================================================
# This is scripts/patches/pi5/3_camera_usb_webcam.sh plus the three things a 1080p night-vision module
# wants. Run THIS one instead of that one -- it sets the same backend/source keys.
#
# WHY EACH KEY:
#   ROBOT_CAMERA_CAPTURE_WIDTH/HEIGHT 1920x1080
#       The v4l2 backend used to take whatever the driver defaulted to, which on nearly every UVC webcam is
#       640x480 -- so face recognition was fed a fraction of the sensor. Ask for the mode you paid for. The
#       camera silently substitutes its nearest mode if it can't do this one, and the startup line reports
#       what it ACTUALLY got, so the setting can't lie to you.
#   ROBOT_CAMERA_SIZE 768
#       face_id only ever sees this letterboxed square, so it CAPS recognition: a visitor's face is (their
#       share of the frame width) x this many pixels, and ArcFace wants >=112px to downscale into rather than
#       stretch up. At 512 a wall-mounted concierge measured 70-97px faces and marginal 0.25-0.40 scores.
#       768 costs 2.25x the bytes per Gemini upload -- worth it on a CONCIERGE, where recognition IS the job.
#   ROBOT_CAMERA_AUTOFOCUS 0
#       These modules have a FIXED 3.6mm lens. There is nothing to focus, and leaving AF on means the driver
#       is asked for a control the camera may not have (harmless but noisy) -- while on cameras that DO have
#       it, AF can hunt mid-burst and hand the matcher a soft frame. Fixed focus is a feature here: a 3.6mm
#       lens on a small sensor has deep depth of field, so 1-3m is sharp without any hunting.
#
# ! NIGHT VISION CHANGES WHAT RECOGNITION CAN DO. After dark the IR-CUT filter swings out and you get
#   greyscale INFRARED frames. Faces are still DETECTED fine, but ArcFace embeddings from IR are a different
#   domain to your daylight enrolment photos, so expect the generic greeting rather than a name at night
#   until you enrol a few IR shots too:
#       sudo systemctl stop robot.service
#       ./scripts/camera_face_probe.sh 0.5        # AT NIGHT, standing where visitors stand
#       cp ~/face-probe/*-face-*.jpg ~/people_named/     # rename <Name>_ir_01.jpg etc
#       ./scripts/enroll_person.sh "Name" "desc" "say-as" ~/people_named/Name*.jpg
#   Enrolling both lighting conditions under one name is exactly what ROBOT_FACE_ID_AGGREGATE=max is for --
#   the best-matching photo wins, so a daylight visitor matches the daylight shots and a night one the IR.
#
# ! CHECK THE MODE IS REAL BEFORE YOU TRUST IT. Some cheap modules only offer 1080p in uncompressed YUYV at
#   5fps, which would make every capture crawl (each one reads several frames):
#       v4l2-ctl --list-formats-ext -d /dev/video0      # want MJPG 1920x1080 @ 30fps
#
# Re-runnable (idempotent).
#   Usage:  ./scripts/patches/pi5/3_camera_usb_ir_1080p.sh [index]   (default 0, or a /dev/videoN path)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RST=$'\033[0m'; else GRN=''; YEL=''; RST=''; fi
IDX="${1:-0}"
case "$IDX" in
  -h|--help) sed -n '2,/^# ===*$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-32s = %s\n' "$key" "$val"
}

echo ">> Selecting a 1080p USB IR night-vision camera on the Pi 5:"
set_kv ROBOT_CAMERA_BACKEND v4l2
set_kv ROBOT_CAMERA_SOURCE "$IDX"
set_kv ROBOT_CAMERA_CAPTURE_WIDTH 1920
set_kv ROBOT_CAMERA_CAPTURE_HEIGHT 1080
set_kv ROBOT_CAMERA_SIZE 768
set_kv ROBOT_CAMERA_AUTOFOCUS 0
echo
echo "   Prove the mode exists:  v4l2-ctl --list-formats-ext -d /dev/video$([ "${IDX}" = "${IDX#/dev/}" ] && echo "$IDX" || echo 0)"
echo "                           (want MJPG 1920x1080 @ 30fps -- YUYV-only at 5fps makes every look crawl)"
echo "   Reload:  ./scripts/run_me.sh --reload"
echo "   Verify:  journalctl -u robot.service -n 30 | grep 'camera:'"
echo "            want ... 768px square JPEG, sensor 1920x1080 ...  (NOT 'asked 1920x1080 -- camera gave')"
echo "   Measure: ./scripts/camera_face_probe.sh    # faces should grow ~30% vs a 720p/90-degree cam"
echo
echo "   ${YEL}UPGRADING A ROBOT THAT ONCE RAN A CSI CAMERA? an old dtoverlay in the boot config can still${RST}"
echo "   ${YEL}claim /dev/video0. Clear it once (this is now the installer's default, so a plain re-run does it):${RST}"
echo "     grep -n 'LocalAiRobot camera' /boot/firmware/config.txt     # nothing? you're already clean"
echo "     ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot"
echo
echo "   ${YEL}AT NIGHT the IR-CUT filter swings out and frames go greyscale INFRARED. Faces still DETECT,${RST}"
echo "   ${YEL}but they won't MATCH daylight enrolment photos -- enrol a few IR shots per person too.${RST}"
echo
echo "${GRN}===============================================${RST}"
echo "${GRN}  PATCH APPLIED (Pi 5, 1080p USB IR camera) !!!${RST}"
echo "${GRN}===============================================${RST}"
