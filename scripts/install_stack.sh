#!/usr/bin/env bash
# Installs the fully-offline AI stack + Raspberry Pi 5 hardware deps for the RC crawler (arm64).
# Needs internet for THIS step only. Re-runnable.
set -euo pipefail

MODEL="${ROBOT_MODEL:-gemma3:4b}"
WHISPER_MODEL="base.en"
PIPER_REL="2023.11.14-2"      # check https://github.com/rhasspy/piper/releases
VOICE="en_US-lessac-medium"
H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> [1/6] system packages"
sudo apt update
sudo apt install -y git build-essential cmake alsa-utils curl \
                    python3-venv python3-dev i2c-tools

echo ">> [2/6] enable I2C + hardware group access (PCA9685 over I2C, HC-SR04 on GPIO)"
sudo raspi-config nonint do_i2c 0 || echo "   (raspi-config not found -- enable I2C manually: dtparam=i2c_arm=on)"
sudo usermod -aG i2c,gpio,dialout,audio "$USER" || true

echo ">> [3/6] Ollama + model"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
ollama pull "${MODEL}"

echo ">> [4/6] whisper.cpp"
[ -d "${H}/whisper.cpp" ] || git clone https://github.com/ggerganov/whisper.cpp.git "${H}/whisper.cpp"
cd "${H}/whisper.cpp"
cmake -B build
cmake --build build -j --config Release
bash ./models/download-ggml-model.sh "${WHISPER_MODEL}"

echo ">> [5/6] Piper + voice"
mkdir -p "${H}/piper" && cd "${H}/piper"
if [ ! -x "${H}/piper/piper/piper" ]; then
  curl -fsSLo piper.tgz \
    "https://github.com/rhasspy/piper/releases/download/${PIPER_REL}/piper_linux_aarch64.tar.gz"
  tar -xf piper.tgz
fi
VB="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
[ -f "${VOICE}.onnx" ]      || curl -fsSLo "${VOICE}.onnx"      "${VB}/${VOICE}.onnx"
[ -f "${VOICE}.onnx.json" ] || curl -fsSLo "${VOICE}.onnx.json" "${VB}/${VOICE}.onnx.json"

echo ">> [6/6] Python venv + hardware libraries (gpiozero/lgpio + Adafruit ServoKit)"
cd "${REPO}/brain"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo ">> DONE."
echo "   1. Edit config/robot.env  (YOUR_USER + mic + steering/throttle/pins)."
echo "   2. REBOOT once so the I2C enable and the new groups take effect."
echo "   3. Confirm the PCA9685 is on the bus:  i2cdetect -y 1   (expect 0x40)."
echo "   4. With the wheels OFF the ground, run:"
echo "        set -a; source config/robot.env; set +a; brain/.venv/bin/python brain/main.py"
