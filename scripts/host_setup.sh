#!/usr/bin/env bash
# host_setup.sh -- install the ASSET-AUTHORING toolkit on YOUR PC (not the Pi), so you can design
# custom face frames + earcons and commit them to the repo. Runs on WSL (Windows), macOS, or Linux --
# all bash. (Native Windows: use WSL.) The robot itself is still set up on the Pi by install_stack.sh.
#
# Installs ONLY what's missing, after a plan + confirm (the cleanup_pi.sh pattern):
#   - ImageMagick + ffmpeg            -> faces: resize/pad, extract frames from a clip
#   - sox                             -> sounds: normalize/convert the Piper WAVs (e.g. 22050 -> 44100)
#   - a small Python venv with Pillow -> faces: scripts/generate_faces.py (procedural templates)
#   - Piper + the en_US-lessac voice  -> sounds: SPOKEN earcons, same voice as the Pi (-> ~/piper)
#
#   ./scripts/host_setup.sh        plan, confirm, then install what's missing
#   ./scripts/host_setup.sh -n     DRY RUN: just print the plan, install nothing
#   ./scripts/host_setup.sh -y     skip the y/N confirmation
#
# After it's done, generate your assets per sounds/README.md (spoken cues) + brain/faces/README.md.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
H="${HOME}"
TOOLS="${H}/.localairobot-tools"
VENV="${TOOLS}/venv"
PIPER_DIR="${H}/piper"
PIPER_REL="2023.11.14-2"            # keep in step with scripts/install_stack.sh
VOICE="en_US-lessac-medium"

DRYRUN=0; YES=0
while [ $# -gt 0 ]; do case "$1" in
  -n|--dry-run) DRYRUN=1 ;;
  -y|--yes)     YES=1 ;;
  -h|--help)    echo "usage: $(basename "$0") [-n dry-run] [-y yes]"; exit 0 ;;
  *) echo "unknown option: $1 (use -n, -y)"; exit 2 ;;
esac; shift; done

# Refuse root: brew refuses to run as root, and pip/venv/Piper would land in the wrong home.
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  echo "Run as your normal user, not root/sudo (it sudo's only the package-manager step)." >&2
  exit 1
fi

# --- detect OS + package manager ---
OS="$(uname -s)"; ARCH="$(uname -m)"
PM=""; PM_INSTALL=""
if command -v brew >/dev/null 2>&1; then PM="brew"; PM_INSTALL="brew install"
elif command -v apt-get >/dev/null 2>&1; then PM="apt"; PM_INSTALL="sudo apt-get install -y"
elif command -v dnf >/dev/null 2>&1; then PM="dnf"; PM_INSTALL="sudo dnf install -y"
elif command -v pacman >/dev/null 2>&1; then PM="pacman"; PM_INSTALL="sudo pacman -S --noconfirm"
fi
if [ "$OS" = "Darwin" ] && [ "$PM" != "brew" ]; then
  echo "macOS without Homebrew. Install brew first ( https://brew.sh ), then re-run this." >&2
  exit 1
fi
if [ -z "$PM" ]; then
  echo "No supported package manager (need apt / dnf / pacman / brew)." >&2
  echo "Install ImageMagick + ffmpeg + sox + python3 venv by hand, then see sounds/README.md + brain/faces/README.md." >&2
  exit 1
fi
imagemagick_pkg() { [ "$PM" = "dnf" ] && echo "ImageMagick" || echo "imagemagick"; }

# --- build the plan: only what's MISSING ---
PLAN=(); PKGS=()
add_pkg() { PKGS+=("$1"); PLAN+=("$PM install $1 -- $2"); }
command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1 || add_pkg "$(imagemagick_pkg)" "faces: resize/pad"
command -v ffmpeg  >/dev/null 2>&1 || add_pkg ffmpeg "faces: clip -> frames"
command -v sox     >/dev/null 2>&1 || add_pkg sox    "sounds: normalize the Piper WAVs"
command -v curl    >/dev/null 2>&1 || add_pkg curl   "download Piper"

PILLOW_NEED=0
if [ ! -x "${VENV}/bin/python" ] || ! "${VENV}/bin/python" -c 'import PIL' 2>/dev/null; then
  PILLOW_NEED=1
  [ "$PM" = "apt" ] && add_pkg python3-venv "create the Pillow venv"
  PLAN+=("create venv ${VENV} + pip install Pillow -- faces: generate_faces.py")
fi

PIPER_NEED=0
[ -x "${PIPER_DIR}/piper/piper" ]   || { PIPER_NEED=1; PLAN+=("download Piper -> ${PIPER_DIR} -- sounds: spoken earcons"); }
[ -f "${PIPER_DIR}/${VOICE}.onnx" ] || { PIPER_NEED=1; PLAN+=("download Piper voice ${VOICE} -> ${PIPER_DIR}"); }

echo "=== host_setup.sh -- authoring toolkit for your PC  (${OS}/${ARCH}, pkg mgr: ${PM}) ==="
if [ ${#PLAN[@]} -eq 0 ]; then
  echo "Everything's already installed. Start customizing: see sounds/README.md + brain/faces/README.md"
  exit 0
fi
echo "Will install ONLY what's missing:"
for l in "${PLAN[@]}"; do echo "   $l"; done
[ "$PM" != "brew" ] && echo "(the package-manager step uses sudo -- you may be asked for your password.)"
[ "$DRYRUN" = 1 ] && { echo; echo "Dry run (-n): nothing was installed."; exit 0; }
if [ "$YES" != 1 ]; then
  echo; read -r -p "Proceed? [y/N] " ans || true
  case "${ans:-}" in y|Y|yes|YES) ;; *) echo "Aborted -- nothing installed."; exit 1 ;; esac
fi

echo; echo "=== installing ==="
# 1) package-manager packages
if [ ${#PKGS[@]} -gt 0 ]; then
  [ "$PM" = "apt" ] && sudo apt-get update
  echo ">> ${PM_INSTALL} ${PKGS[*]}"
  ${PM_INSTALL} "${PKGS[@]}"
fi

# 2) Pillow venv (isolated so we never fight a PEP-668 'externally-managed' system python)
if [ "$PILLOW_NEED" = 1 ]; then
  mkdir -p "$TOOLS"
  [ -x "${VENV}/bin/python" ] || python3 -m venv "$VENV"
  "${VENV}/bin/pip" install --upgrade pip >/dev/null
  "${VENV}/bin/pip" install Pillow
fi

# 3) Piper binary + voice (same layout + voice as the Pi, so ~/piper/piper/piper works on both)
if [ "$PIPER_NEED" = 1 ]; then
  asset=""
  case "$OS" in
    Linux)  case "$ARCH" in x86_64) asset=piper_linux_x86_64.tar.gz;; aarch64|arm64) asset=piper_linux_aarch64.tar.gz;; esac;;
    Darwin) case "$ARCH" in arm64) asset=piper_macos_aarch64.tar.gz;; x86_64) asset=piper_macos_x64.tar.gz;; esac;;
  esac
  if [ -z "$asset" ]; then
    echo "   !! no prebuilt Piper for ${OS}/${ARCH} -- install Piper another way to make the spoken cues."
  else
    mkdir -p "$PIPER_DIR"; cd "$PIPER_DIR"
    if [ ! -x "${PIPER_DIR}/piper/piper" ]; then
      echo ">> download ${asset}"
      curl -fsSLo piper.tgz "https://github.com/rhasspy/piper/releases/download/${PIPER_REL}/${asset}"
      tar -xf piper.tgz && rm -f piper.tgz
    fi
    VB="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
    [ -f "${VOICE}.onnx" ]      || { echo ">> download voice ${VOICE}"; curl -fsSLo "${VOICE}.onnx"      "${VB}/${VOICE}.onnx"; }
    [ -f "${VOICE}.onnx.json" ] ||                                       curl -fsSLo "${VOICE}.onnx.json" "${VB}/${VOICE}.onnx.json"
  fi
fi

echo
echo "=== DONE -- authoring toolkit ready. ==="
echo "   Faces:  magick + ffmpeg  ·  Pillow at  ${VENV}/bin/python  (for scripts/generate_faces.py)"
echo "           -> follow brain/faces/README.md"
echo "   Sounds: Piper at  ${PIPER_DIR}/piper/piper  (+ sox for normalizing)"
echo "           -> follow sounds/README.md  (spoken cues; commit and deploy to the Pi)"
