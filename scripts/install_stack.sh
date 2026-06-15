#!/usr/bin/env bash
# Installs the fully-offline AI stack + Raspberry Pi 5 hardware deps for the RC crawler (arm64):
# Ollama + gemma4:e2b-it-qat (text) + moondream (vision), whisper.cpp, Piper, picamera2, the face-panel library,
# and the Python venv. Needs internet for THIS step only. Re-runnable.
set -euo pipefail

MODEL="${ROBOT_MODEL:-gemma4:e2b-it-qat}"
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
                    python3-venv python3-dev i2c-tools sqlite3 \
                    portaudio19-dev libasound2-dev   # PortAudio: continuous mic for the optional wake word
                    # sqlite3: raw CLI for the memory store (scripts/rag.sh wraps it; venv python also has it)

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
echo "   note: ${MODEL} (Gemma 4) needs a RECENT Ollama -- if the pull 404s, upgrade Ollama and retry. 'ollama --version' is $(ollama --version 2>/dev/null || echo '?')"

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

# openWakeWord wake-word models. Behaviour differs by version: 0.5+ DOWNLOAD the feature models via
# utils.download_models(); 0.4.x (what installs on Python 3.13 / aarch64) BUNDLES them in the wheel.
# So this is version-tolerant: download if we can, else confirm the bundled models exist. Non-fatal --
# the wake word just won't load -- LIVE then has no trigger (ENTER works only in --dry).
echo "   checking openWakeWord wake-word models..."
./.venv/bin/python - <<'PY' || echo "   (model check failed -- if the wake word won't load, LIVE has no trigger; use --dry, see docs/assembly.md step 8)"
import glob, os, openwakeword
ver = getattr(openwakeword, "__version__", "?")
dl = getattr(getattr(openwakeword, "utils", None), "download_models", None)
if callable(dl):
    dl()                                   # 0.5+: fetch the feature models (needs internet)
    print(f"   openWakeWord {ver}: feature models downloaded.")
else:
    res = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    n_onnx = len(glob.glob(os.path.join(res, "*.onnx")))
    n_tfl = len(glob.glob(os.path.join(res, "*.tflite")))
    print(f"   openWakeWord {ver}: {n_onnx} .onnx + {n_tfl} .tflite models bundled in the wheel.")
    assert n_onnx or n_tfl, f"no bundled models found under {res}"
PY

# Earcon folders for the spoken ack/greeting/thinking/error cues. The WAVs are generated on your PC with
# Piper and committed (see sounds/README.md), so a fresh clone already has them; this just makes sure the
# folders exist. An empty folder is a silent no-op.
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/wake_word_ack" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/error_sounds"

# Auto-load config/robot.env in every NEW interactive shell, so you never have to
# 'set -a; source config/robot.env; set +a' by hand again. Idempotent (appended once); run_me.sh and
# shutdown.sh also source it themselves. cleanup_pi.sh removes this block again on a reset.
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

# Make the USB speaker ALSA's DEFAULT output (writes ~/.asoundrc) so the robot's VOICE has somewhere to
# play -- otherwise `aplay` hits the wrong card and errors ("audio open error: Unknown error 524"). This
# is best-effort + non-interactive: if no USB speaker is plugged in yet it just prints how to run it
# later. cleanup_pi.sh removes the ~/.asoundrc it writes.
echo ">> audio: pointing ALSA's default output at your USB speaker (if one is plugged in)"
bash "${REPO}/scripts/setup_audio.sh" || true

echo ">> DONE -- the offline AI stack is installed:"
echo "   Ollama + the gemma4:e2b-it-qat (text) and moondream (vision) models, whisper.cpp (speech-to-text),"
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
echo "        - point the VOICE at your USB speaker:   ./scripts/setup_audio.sh   (writes ~/.asoundrc)"
echo "        - for the bench, set ROBOT_DRY_RUN=1   (nothing drives)"
echo
echo "   3. Check the AI brain answers, entirely on the Pi (no internet):"
echo "        ollama run gemma4:e2b-it-qat \"reply with one word: ready\""
echo
echo "   4. Dry-run the brain -- nothing moves, it just prints what it WOULD do:"
echo "        ./scripts/run_me.sh --dry"
echo
echo "   The wake word is ON by default -- in live, say 'hey jarvis' to talk (ENTER works in --dry)."
echo
echo "   Inspect the robot's memory (taught people/things, recent sightings) any time:"
echo "        ./scripts/rag.sh        (also: --seen | --all | --forget <name> | --query \"<SQL>\")"
