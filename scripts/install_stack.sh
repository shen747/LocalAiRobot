#!/usr/bin/env bash
# install_stack.sh -- board-detecting DISPATCHER for the per-board installers (one script per board):
#   scripts/install_stack_pi5.sh      Raspberry Pi 5 (the reference build)
#   scripts/install_stack_jetson.sh   NVIDIA Jetson (Orin Nano, JetPack 6)
#
# Reads /proc/device-tree/model (the same signal brain/hal.py auto-detects the board from) and exec's the
# matching installer, forwarding every flag AND env var (ROBOT_CAMERA_OVERLAY=... --bootstrap etc.) verbatim
# -- so './scripts/install_stack.sh' in the README, docs and the "run install_stack.sh" rescue messages the
# other scripts print keeps working on ANY board. Adding a board? drop in scripts/install_stack_<board>.sh
# and add one case line below. Re-runnable (it just delegates to the target installer, which is idempotent).
#
# `exec bash` (never source): each installer derives its REPO root from "$0", so it must run as its own $0.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL=""
[ -r /proc/device-tree/model ] && MODEL="$(tr -d '\0' </proc/device-tree/model)"

case "$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')" in
  *"raspberry pi"*)         exec bash "$DIR/install_stack_pi5.sh" "$@" ;;
  *jetson*|*orin*|*tegra*)  exec bash "$DIR/install_stack_jetson.sh" "$@" ;;   # same match as install_stack_jetson.sh's own guard
  *)
    echo "install_stack.sh: could not detect a supported board (/proc/device-tree/model: '${MODEL:-<missing>}')." >&2
    echo "Run the installer for your board directly:" >&2
    echo "    ./scripts/install_stack_pi5.sh       # Raspberry Pi 5" >&2
    echo "    ./scripts/install_stack_jetson.sh    # NVIDIA Jetson (Orin Nano, JetPack 6)" >&2
    exit 2 ;;
esac
