#!/usr/bin/env bash
# Installs the fully-offline AI stack + Raspberry Pi 5 hardware deps for the RC crawler (arm64):
# Ollama + gemma3:4b (text) + moondream (vision), whisper.cpp, Piper, picamera2, the face-panel library,
# and the Python venv. Needs internet for THIS step only. Re-runnable.
set -euo pipefail

MODEL="${ROBOT_MODEL:-gemma3:4b}"
VISION_MODEL="${ROBOT_VISION_MODEL:-moondream}"
WHISPER_MODEL="base.en"
PIPER_REL="2023.11.14-2"      # check https://github.com/rhasspy/piper/releases
VOICE="en_US-lessac-medium"
TURING_REPO="https://github.com/mathoudebine/turing-smart-screen-python.git"
H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> [1/8] system packages"
sudo apt update
sudo apt install -y git build-essential cmake alsa-utils curl \
                    python3-venv python3-dev i2c-tools \
                    portaudio19-dev libasound2-dev   # PortAudio: continuous mic for the optional wake word

echo ">> [2/8] camera stack (picamera2 + libcamera, from apt -- NOT pip)"
sudo apt install -y --no-install-recommends python3-picamera2 || \
  echo "   (python3-picamera2 not found -- update Raspberry Pi OS, or the camera feature stays off)"

echo ">> [3/8] enable I2C + hardware group access (PCA9685 I2C, 3x HC-SR04 GPIO, USB-C panel, framebuffer)"
sudo raspi-config nonint do_i2c 0 || echo "   (raspi-config not found -- enable I2C manually: dtparam=i2c_arm=on)"
# i2c/gpio = motion + sensors;  dialout = USB-C face panel (/dev/ttyACM0);  audio = mic/speaker;  video = /dev/fb0 fallback
sudo usermod -aG i2c,gpio,dialout,audio,video "$USER" || true

echo ">> [4/8] Ollama + models (text intent + vision)"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
ollama pull "${MODEL}"
ollama pull "${VISION_MODEL}"            # dedicated vision VLM (ROBOT_VISION_MODEL); default moondream
echo "   note: gemma3 vision needs Ollama >= 0.6 -- 'ollama --version' is $(ollama --version 2>/dev/null || echo '?')"

echo ">> [5/8] whisper.cpp"
[ -d "${H}/whisper.cpp" ] || git clone https://github.com/ggerganov/whisper.cpp.git "${H}/whisper.cpp"
cd "${H}/whisper.cpp"
cmake -B build
cmake --build build -j --config Release
bash ./models/download-ggml-model.sh "${WHISPER_MODEL}"

echo ">> [6/8] Piper + voice"
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

echo ">> [8/8] Python venv (--system-site-packages, so it sees apt's picamera2/libcamera) + libraries"
cd "${REPO}/brain"
# The venv MUST be created with --system-site-packages or 'import picamera2' fails (libcamera has
# no pip package). That flag cannot be added to an existing venv, so recreate it if picamera2
# isn't importable (i.e. it's an old venv made without the flag, or there is no venv yet).
if [ ! -d .venv ] || ! ./.venv/bin/python -c "import picamera2" >/dev/null 2>&1; then
  rm -rf .venv
  python3 -m venv --system-site-packages .venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# openWakeWord feature models (melspectrogram + embedding) -- REQUIRED, since the "Richard" wake word
# is ON by default. NOT silenced any more (so a failure is visible), retried for flaky installs, and it
# tells you how to re-run. They land inside brain/.venv (so cleanup_pi.sh removes them with the venv).
echo "   fetching openWakeWord wake-word feature models (needs internet)..."
owok=0
for owtry in 1 2 3; do
  if ./.venv/bin/python -c "import openwakeword; openwakeword.utils.download_models()"; then
    owok=1; break
  fi
  echo "   (attempt $owtry failed -- retrying in 3s)"; sleep 3
done
if [ "$owok" = 1 ]; then
  echo "   openWakeWord feature models ready."
else
  echo "   !! NOT fetched -- the wake word falls back to ENTER push-to-talk until you re-run, online:"
  echo "        brain/.venv/bin/python -c 'import openwakeword; openwakeword.utils.download_models()'"
fi

# Earcon directories for the ack/greeting/thinking cues. Drop your own short .wav files in here;
# an empty directory is a silent no-op, so the feature is harmless until you add sounds.
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/error_sounds"

# Auto-load config/robot.env in every NEW interactive shell, so you never have to
# 'set -a; source config/robot.env; set +a' by hand again. Idempotent (appended once); run_me.sh and
# shutdown.sh also source it themselves. cleanup_pi.sh leaves ~/.bashrc alone -- remove the block by hand.
BASHRC="${H}/.bashrc"
if [ -f "$BASHRC" ] && grep -qF "LocalAiRobot: auto-load robot.env" "$BASHRC"; then
  echo "   ~/.bashrc already auto-loads config/robot.env"
else
  {
    echo ""
    echo "# LocalAiRobot: auto-load robot.env into every interactive shell"
    echo "[ -f \"${REPO}/config/robot.env\" ] && { set -a; . \"${REPO}/config/robot.env\"; set +a; }"
  } >> "$BASHRC"
  echo "   added config/robot.env auto-load to ~/.bashrc (new SSH sessions load it automatically)"
fi

echo ">> DONE -- the offline AI stack is installed:"
echo "   Ollama + the gemma3:4b (text) and moondream (vision) models, whisper.cpp (speech-to-text),"
echo "   Piper (text-to-speech), the camera + face-screen libraries, and the Python environment."
echo
echo "What to do next  (full, explained walkthrough: docs/assembly.md -> Phase 1, from step 5):"
echo
echo "   1. Reboot now, so the settings this installer switched on and your new device permissions"
echo "      take effect:        sudo reboot"
echo "      ...then SSH back in and:   cd ~/LocalAiRobot"
echo "      (new SSH sessions now auto-load config/robot.env -- no more 'source' by hand.)"
echo
echo "   2. Tell the robot who you are and which microphone to use:   nano config/robot.env"
echo "        - set your username in the whisper/piper paths:   sed -i \"s/YOUR_USER/\$(whoami)/g\" config/robot.env"
echo "        - set ROBOT_MIC to your USB microphone (the doc shows how to find it)"
echo "        - for the bench, set ROBOT_DRY_RUN=1   (nothing drives)"
echo
echo "   3. Check the AI brain answers, entirely on the Pi (no internet):"
echo "        ollama run gemma3:4b \"reply with one word: ready\""
echo
echo "   4. Dry-run the brain -- nothing moves, it just prints what it WOULD do:"
echo "        ./scripts/run_me.sh --dry"
echo
echo "   The 'Richard' wake word is ON by default -- just say 'Richard' (or press ENTER) to talk."
