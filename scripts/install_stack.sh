#!/usr/bin/env bash
# Installs the fully-offline AI stack on a Raspberry Pi 5 (arm64).
# Needs internet for THIS step only. Re-runnable.
set -euo pipefail

GO_VERSION="1.23.4"           # check https://go.dev/dl for the current stable
MODEL="${ROBOT_MODEL:-gemma3:4b}"
WHISPER_MODEL="base.en"
PIPER_REL="2023.11.14-2"      # check https://github.com/rhasspy/piper/releases
VOICE="en_US-lessac-medium"
H="${HOME}"

echo ">> [1/5] system packages"
sudo apt update
sudo apt install -y git build-essential cmake alsa-utils curl

echo ">> [2/5] Ollama + model"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
ollama pull "${MODEL}"

echo ">> [3/5] Go ${GO_VERSION}"
if ! /usr/local/go/bin/go version 2>/dev/null | grep -q "go${GO_VERSION}"; then
  curl -fsSLo /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-arm64.tar.gz"
  sudo rm -rf /usr/local/go
  sudo tar -C /usr/local -xzf /tmp/go.tgz
fi
export PATH="$PATH:/usr/local/go/bin"
grep -q '/usr/local/go/bin' "${H}/.profile" 2>/dev/null || \
  echo 'export PATH="$PATH:/usr/local/go/bin"' >> "${H}/.profile"

echo ">> [4/5] whisper.cpp"
[ -d "${H}/whisper.cpp" ] || git clone https://github.com/ggerganov/whisper.cpp.git "${H}/whisper.cpp"
cd "${H}/whisper.cpp"
cmake -B build
cmake --build build -j --config Release
bash ./models/download-ggml-model.sh "${WHISPER_MODEL}"

echo ">> [5/5] Piper + voice"
mkdir -p "${H}/piper" && cd "${H}/piper"
if [ ! -x "${H}/piper/piper/piper" ]; then
  curl -fsSLo piper.tgz \
    "https://github.com/rhasspy/piper/releases/download/${PIPER_REL}/piper_linux_aarch64.tar.gz"
  tar -xf piper.tgz
fi
VB="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
[ -f "${VOICE}.onnx" ]      || curl -fsSLo "${VOICE}.onnx"      "${VB}/${VOICE}.onnx"
[ -f "${VOICE}.onnx.json" ] || curl -fsSLo "${VOICE}.onnx.json" "${VB}/${VOICE}.onnx.json"

echo ">> building the brain"
cd "$(dirname "$0")/../brain"
/usr/local/go/bin/go mod tidy
/usr/local/go/bin/go build -o brain .

echo ">> DONE. Edit config/robot.env (YOUR_USER + mic), then run: ./brain/brain"
