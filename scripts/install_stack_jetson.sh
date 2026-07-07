#!/usr/bin/env bash
# Installs the offline AI stack + NVIDIA Jetson (Orin Nano, JetPack 6 / Ubuntu 22.04, arm64) hardware deps
# for the RC crawler: Ollama + gemma4/moondream (GPU/CUDA), whisper.cpp (CUDA), Piper, OpenCV camera, the
# face-panel library, and the Python venv. Needs internet for THIS step only. Re-runnable.
#
# This is the Jetson counterpart of scripts/install_stack.sh. Differences vs the Pi:
#   - GPIO: Jetson.GPIO (via pip) instead of python3-lgpio/gpiozero.
#   - Camera: system OpenCV (apt python3-opencv) for a USB webcam; the Arducam IMX519 CSI driver is a
#             SEPARATE, JetPack-version-specific install (printed at the end), not automated here.
#   - I2C: no raspi-config; the 40-pin header I2C is enabled by default (usually bus 7). Pins are freed via
#          jetson-io. whisper.cpp is built with -DGGML_CUDA=1; Ollama uses its Jetson CUDA runtime.
#   - No /boot/firmware/config.txt camera overlay (that's a Raspberry Pi mechanism).
set -euo pipefail

MODEL="${ROBOT_MODEL:-gemma4:e2b-it-qat}"   # e2b by default -- lighter on the Orin's 8GB shared memory
VISION_MODEL="${ROBOT_VISION_MODEL:-moondream}"
WHISPER_MODEL="base.en"
PIPER_REL="2023.11.14-2"      # check https://github.com/rhasspy/piper/releases
VOICE="en_US-lessac-medium"
TURING_REPO="https://github.com/mathoudebine/turing-smart-screen-python.git"
H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

BOOTSTRAP=0
for arg in "$@"; do
  case "$arg" in
    --bootstrap) BOOTSTRAP=1 ;;
    -h|--help)   echo "usage: $(basename "$0") [--bootstrap]"; \
                 echo "  --bootstrap  also install the systemd boot service (brain auto-starts on boot)"; exit 0 ;;
    *) echo "unknown option: $arg (use --bootstrap)" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

# Sanity: warn (don't fail) if this doesn't look like a Jetson -- the script still installs, but the
# hardware factories will only work on an actual Jetson.
if [ -r /proc/device-tree/model ] && ! grep -qiE 'jetson|orin|tegra' /proc/device-tree/model; then
  echo "!! /proc/device-tree/model does not look like a Jetson ($(tr -d '\0' </proc/device-tree/model)) --"
  echo "   this script targets the Jetson; on a Pi use scripts/install_stack.sh instead."
fi

echo ">> [1/8] system packages"
sudo apt update
sudo apt install -y git build-essential cmake alsa-utils curl \
                    python3-venv python3-dev i2c-tools sqlite3 avahi-daemon \
                    portaudio19-dev libasound2-dev python3-opencv
                    # python3-opencv: USB-webcam capture via cv2 (CSI uses the same cv2 + GStreamer/Argus)
                    # NOTE: no python3-lgpio -- the Jetson uses Jetson.GPIO (pip, see requirements-jetson.txt)

echo ">> [2/8] I2C + GPIO group access (PCA9685 I2C, 3x HC-SR04 GPIO, USB-C panel, framebuffer)"
# Jetson: no raspi-config. The 40-pin header I2C is on by default (bus 7 for pins 3/5). Group membership is
# what lets a non-root user reach /dev/i2c-*, /dev/gpiochip*, the serial panel and the framebuffer.
sudo usermod -aG i2c,gpio,dialout,audio,video "$USER" || true
echo "   NOTE: some header pins default to SFIO (not GPIO). Free the ultrasonic TRIG/ECHO pins with:"
echo "         sudo /opt/nvidia/jetson-io/jetson-io.py   (then reboot). Verify I2C: i2cdetect -y 7 (expect 0x40)."

echo ">> [3/8] performance mode (MAXN + clocks) for lowest inference latency"
sudo nvpmodel -m 0 2>/dev/null || echo "   (nvpmodel not found -- set the max power mode from the desktop if needed)"
sudo jetson_clocks 2>/dev/null || echo "   (jetson_clocks not found -- skipping)"

echo ">> [4/8] Ollama (Jetson CUDA runtime) + models (text intent + vision)"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
ollama pull "${MODEL}"
ollama pull "${VISION_MODEL}"
echo "   verify the GPU is used:  ollama run ${MODEL} 'hi'  while watching  tegrastats  (GR3D_FREQ should rise)."

echo ">> [5/8] whisper.cpp (CUDA)"
[ -d "${H}/whisper.cpp" ] || git clone https://github.com/ggerganov/whisper.cpp.git "${H}/whisper.cpp"
cd "${H}/whisper.cpp"
cmake -B build -DGGML_CUDA=1
cmake --build build -j --config Release
bash ./models/download-ggml-model.sh "${WHISPER_MODEL}"

echo ">> [6/8] Piper + voice (same aarch64 binary as the Pi)"
mkdir -p "${H}/piper" && cd "${H}/piper"
if [ ! -x "${H}/piper/piper/piper" ]; then
  curl -fsSLo piper.tgz \
    "https://github.com/rhasspy/piper/releases/download/${PIPER_REL}/piper_linux_aarch64.tar.gz"
  tar -xf piper.tgz
fi
VB="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
[ -f "${VOICE}.onnx" ]      || curl -fsSLo "${VOICE}.onnx"      "${VB}/${VOICE}.onnx"
[ -f "${VOICE}.onnx.json" ] || curl -fsSLo "${VOICE}.onnx.json" "${VB}/${VOICE}.onnx.json"

echo ">> [7/8] USB-C face-panel library (turing-smart-screen-python; used via ROBOT_FACE_LIB)"
[ -d "${H}/turing-smart-screen-python" ] || git clone "${TURING_REPO}" "${H}/turing-smart-screen-python"

echo ">> [8/8] Python venv (--system-site-packages, so it sees apt's OpenCV/GStreamer) + libraries"
cd "${REPO}/brain"
# The venv MUST be --system-site-packages so `import cv2` finds JetPack's system OpenCV (there is no
# suitable pip wheel for the Jetson's GStreamer-enabled build). Recreate it if cv2 isn't importable.
if [ ! -d .venv ] || ! ./.venv/bin/python -c "import cv2" >/dev/null 2>&1; then
  rm -rf .venv
  python3 -m venv --system-site-packages .venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements-jetson.txt

# openWakeWord feature models (version-tolerant; same as the Pi installer).
echo "   checking openWakeWord wake-word models..."
./.venv/bin/python - <<'PY' || echo "   (model check failed -- if the wake word won't load, LIVE has no trigger; use --dry)"
import glob, os, openwakeword
ver = getattr(openwakeword, "__version__", "?")
dl = getattr(getattr(openwakeword, "utils", None), "download_models", None)
if callable(dl):
    dl()
    print(f"   openWakeWord {ver}: feature models downloaded.")
else:
    res = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    n_onnx = len(glob.glob(os.path.join(res, "*.onnx")))
    n_tfl = len(glob.glob(os.path.join(res, "*.tflite")))
    print(f"   openWakeWord {ver}: {n_onnx} .onnx + {n_tfl} .tflite models bundled in the wheel.")
    assert n_onnx or n_tfl, f"no bundled models found under {res}"
PY

# Earcons + face templates (synthesized/rendered with the venv, same as the Pi installer; committed WAVs kept).
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/wake_word_ack" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/thinking_still_sounds" \
         "${REPO}/sounds/remote_ack_sounds" "${REPO}/sounds/sleep_sounds" "${REPO}/sounds/error_sounds"
if [ -x "${H}/piper/piper/piper" ] && [ -f "${H}/piper/${VOICE}.onnx" ]; then
  echo "   synthesizing any MISSING earcons with Piper (committed ones are kept)..."
  bash "${REPO}/scripts/generate_sounds.sh" || echo "   (earcon generation skipped/failed -- committed WAVs still ship)"
fi
echo "   rendering any MISSING face templates..."
bash "${REPO}/scripts/generate_faces.sh" || echo "   (face template render skipped/failed -- the face still renders procedurally)"

# Fill YOUR_USER in config/robot.env with the current Linux user (whisper/piper paths).
if [ -f "${REPO}/config/robot.env" ]; then
  sed -i "s/YOUR_USER/$(whoami)/g" "${REPO}/config/robot.env"
  echo ">> config: whisper/piper paths set to /home/$(whoami)/...  (YOUR_USER -> $(whoami))"
fi

# Auto-load config/robot.env in every NEW interactive shell (idempotent).
BASHRC="${H}/.bashrc"
if [ -f "$BASHRC" ] && grep -qF "LocalAiRobot: auto-load robot.env" "$BASHRC"; then
  echo "   ~/.bashrc already auto-loads config/robot.env"
else
  {
    echo ""
    echo "# LocalAiRobot: auto-load robot.env into every interactive shell"
    echo "[ -f \"${REPO}/config/robot.env\" ] && { set -a; . \"${REPO}/config/robot.env\"; set +a; }"
  } >> "$BASHRC"
  echo "   added config/robot.env auto-load to ~/.bashrc"
fi

# Point ALSA's default output at the USB speaker + write device names into config/robot.env (best-effort).
echo ">> audio: pointing ALSA's default output at your USB speaker + writing the device names into config/robot.env"
bash "${REPO}/scripts/setup_audio.sh" || true

if [ "$BOOTSTRAP" = 1 ]; then
  echo
  echo ">> [bootstrap] enabling boot autostart (systemd)..."
  bash "${REPO}/scripts/run_me.sh" --install-boot || echo "   (bootstrap: boot-service install failed -- run it by hand.)"
fi

echo ">> DONE -- Jetson AI stack installed (Ollama+CUDA, whisper.cpp+CUDA, Piper, OpenCV camera, face libs, venv)."
echo
echo "Next -- Jetson-specific bring-up (see JETSON_NANO_SUPPORT.md):"
echo "   * Reboot so the group membership + any jetson-io pin changes take effect:   sudo reboot"
echo "   * Set the board + I2C bus in config/robot.env:  ROBOT_BOARD=jetson (or auto), ROBOT_I2C_BUS=7"
echo "     and swap the ultrasonic pins to the Jetson header profile (BOARD SELECT section)."
echo "   * PWM board:  i2cdetect -y 7   (expect 0x40).   Camera (USB):  ROBOT_CAMERA_BACKEND=v4l2 ROBOT_CAMERA_SOURCE=0"
echo "   * Arducam IMX519 (CSI) is a SEPARATE, JetPack-version-specific driver:"
echo "       wget https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh"
echo "       chmod +x install_full.sh && ./install_full.sh -m imx519   (match your exact JetPack; then ROBOT_CAMERA_BACKEND=gstreamer)"
echo "   * Drive it:   ./scripts/run_me.sh        (bench: ./scripts/run_me.sh --dry)"
echo
echo "${GRN}==============================================================${RST}"
echo "${GRN}   ✓  LocalAiRobot Brain (Jetson) Installed !!!${RST}"
echo "${GRN}==============================================================${RST}"
