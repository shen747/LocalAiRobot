#!/usr/bin/env bash
# scripts/patches/camera_imx519.sh -- select the reference Arducam IMX519 16MP camera AND install its
# autofocus tuning so ROBOT_CAMERA_AUTOFOCUS actually works. No flags to remember: this ONE patch does the
# whole job for the IMX519. Re-runnable. Switch to another camera with camera_ov5647.sh / camera_cm3.sh.
#
# Why the AF install: the Pi's STOCK Bookworm libcamera tuning ships with NO autofocus algorithm, so the
# IMX519 runs fixed-focus and ROBOT_CAMERA_AUTOFOCUS is a no-op (log: "Could not set AF_MODE"). Arducam's
# packages REPLACE the system libcamera with their fork, which carries the IMX519 AF tuning. That is a
# THIRD-PARTY system change that can conflict with later apt libcamera updates -- best-effort here: if it
# fails the camera still works fixed-focus. If it ever breaks, the official Camera Module 3 (IMX708) has
# first-class AF with no fork -> ./scripts/patches/camera_cm3.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
set_kv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$ENV"; then sed -i -E "s|^${k}=.*|${k}=${v}|" "$ENV"; else printf '%s=%s\n' "$k" "$v" >> "$ENV"; fi
  printf '   %-24s = %s\n' "$k" "$v"
}

echo ">> Selecting the reference Arducam IMX519 16MP autofocus camera:"
set_kv ROBOT_CAMERA_AUTOFOCUS 1
echo

# Install Arducam's IMX519 autofocus tuning (replaces the system libcamera with Arducam's fork). Best-effort:
# every branch is guarded so a download/install failure never aborts the patch -- the camera then just stays
# fixed-focus. Needs sudo (the Arducam script calls apt) + network; a reboot afterwards makes it live.
echo ">> Installing Arducam IMX519 autofocus tuning (replaces system libcamera with Arducam's fork)..."
AF_TMP="$(mktemp -d)"
AF_URL="https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh"
if curl -fsSLo "$AF_TMP/install_pivariety_pkgs.sh" "$AF_URL"; then
  chmod +x "$AF_TMP/install_pivariety_pkgs.sh"
  if ( cd "$AF_TMP" && ./install_pivariety_pkgs.sh -p libcamera_dev && ./install_pivariety_pkgs.sh -p libcamera_apps ); then
    echo "   AF tuning installed (after a reboot the 'Could not set AF_MODE' warning should be GONE)."
  else
    echo "   !! Arducam AF install FAILED -- the camera still works fixed-focus. See https://docs.arducam.com/Raspberry-Pi-Camera/"
    echo "      or switch to the official Camera Module 3 (IMX708) for first-class AF: ./scripts/patches/camera_cm3.sh"
  fi
else
  echo "   !! could not download Arducam's install script (network?). Skipping AF tuning -- camera stays fixed-focus."
fi
rm -rf "$AF_TMP"
echo

# When called from install_stack_pi5.sh (LAR_FROM_INSTALL=1) the installer owns the boot overlay, the reboot
# prompt and the final banner -- so stop here and skip the standalone switch/reboot epilogue below. (Use an
# `if`, not `&& exit 0`: under `set -e` a false `[ ... ] && exit 0` would return non-zero and abort.)
if [ -n "${LAR_FROM_INSTALL:-}" ]; then
  exit 0
fi

echo "   SWITCHING to the IMX519 from another camera? also (re)apply the boot overlay (sudo + reboot):"
echo "       ROBOT_CAMERA_OVERLAY=imx519,cam0 ./scripts/install_stack_pi5.sh"
echo "   Then REBOOT and verify:  sudo reboot   ·   rpicam-hello --list-cameras   ·   reload: ./scripts/run_me.sh --reload"
echo
echo "${GRN}  PATCH APPLIED (Arducam IMX519 + autofocus) !!!${RST}"
