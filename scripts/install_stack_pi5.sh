#!/usr/bin/env bash
# Installs the fully-offline AI stack + Raspberry Pi 5 hardware deps for the RC crawler (arm64):
# Ollama + gemma4:e4b-it-qat (text) + moondream (vision), whisper.cpp, Piper, picamera2, the face-panel library,
# and the Python venv. Needs internet for THIS step only. Re-runnable.
set -euo pipefail

MODEL="${ROBOT_MODEL:-gemma4:e2b-it-qat}"   # e2b = the faster default (e4b is smarter but ~2x slower on the Pi 5 CPU)
VISION_MODEL="${ROBOT_VISION_MODEL:-moondream}"
WHISPER_MODEL="base.en"
PIPER_REL="2023.11.14-2"      # check https://github.com/rhasspy/piper/releases
VOICE="en_US-lessac-medium"
TURING_REPO="https://github.com/mathoudebine/turing-smart-screen-python.git"
H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# --bootstrap: after installing the stack, also enable the systemd boot service so the brain auto-starts
# on every boot (see scripts/robot.service + run_me.sh --install-boot). Off by default -- a plain install
# never touches boot behaviour.
BOOTSTRAP=0
for arg in "$@"; do
  case "$arg" in
    --bootstrap)  BOOTSTRAP=1 ;;
    --imx519-af)  echo "note: IMX519 autofocus setup moved to a patch -- just run ./scripts/patches/pi5/3_camera_imx519.sh (no install flag needed)." >&2; exit 2 ;;
    -h|--help)    echo "usage: $(basename "$0") [--bootstrap]"; \
                  echo "  --bootstrap  also install the systemd boot service (brain auto-starts on boot)"; \
                  echo "  (camera autofocus? no flag -- run ./scripts/patches/pi5/3_camera_imx519.sh for your camera)"; exit 0 ;;
    *) echo "unknown option: $arg (use --bootstrap)" >&2; exit 2 ;;
  esac
done

# Green colour for the final success banner (only when writing to a real terminal).
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

# Sanity: warn (don't fail) if this looks like a Jetson -- mirrors the guard in scripts/install_stack_jetson.sh.
# The script still installs, but the Pi-only steps (raspi-config, the camera boot overlay, lgpio/gpiozero)
# and brain/requirements.txt only make sense on a Raspberry Pi -- on a Jetson use scripts/install_stack_jetson.sh.
if [ -r /proc/device-tree/model ] && grep -qiE 'jetson|orin|tegra' /proc/device-tree/model; then
  echo "!! /proc/device-tree/model looks like a Jetson ($(tr -d '\0' </proc/device-tree/model)) --"
  echo "   this script targets the Raspberry Pi 5; on a Jetson use scripts/install_stack_jetson.sh instead."
fi

echo ">> [1/8] system packages"
sudo apt update
sudo apt install -y git build-essential cmake alsa-utils curl \
                    python3-venv python3-dev i2c-tools sqlite3 avahi-daemon python3-lgpio \
                    portaudio19-dev libasound2-dev   # PortAudio: continuous mic for the optional wake word
                    # sqlite3: raw CLI for the memory store (scripts/rag.sh wraps it; venv python also has it)
                    # avahi-daemon: mDNS so <hostname>.local resolves -- SSH by name AND the phone command endpoint
                    # python3-lgpio: the GPIO backend gpiozero uses on the Pi 5 (pigpio does NOT support the Pi 5)

echo ">> [2/8] camera stack (picamera2 + libcamera, from apt -- NOT pip)"
sudo apt install -y --no-install-recommends python3-picamera2 || \
  echo "   (python3-picamera2 not found -- update Raspberry Pi OS, or the camera feature stays off)"
# Camera boot overlay. The build uses an Arducam IMX519 16MP AUTOFOCUS module, which is THIRD-PARTY and so
# is NOT auto-detected -- it needs an explicit dtoverlay in the boot config. We write it here (idempotent;
# config.txt backed up once) so the reboot at the end of install brings the camera up with no hand-editing.
#   Using an OFFICIAL Pi camera (OV5647/IMX219/IMX708)? those auto-detect -- run:  ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh
#   Camera on the other CSI connector? re-run with:  ROBOT_CAMERA_OVERLAY=imx519,cam1 ./scripts/install_stack_pi5.sh
CAMERA_OVERLAY="${ROBOT_CAMERA_OVERLAY:-imx519,cam0}"
CAMCFG=/boot/firmware/config.txt; [ -f "$CAMCFG" ] || CAMCFG=/boot/config.txt
case "$CAMERA_OVERLAY" in
  none|off|auto)
    # Auto-detected sensors (OV5647/IMX219/IMX708/Camera Module 3) need camera_auto_detect=1 and NO
    # dtoverlay=imx519. Actively UNDO a prior IMX519 install so a SWITCH from the reference build is clean
    # (not just a fresh install): restore camera_auto_detect=1 and delete the LocalAiRobot dtoverlay=imx519
    # line. A hand-added dtoverlay is left alone (only our tagged line is removed).
    if [ -f "$CAMCFG" ]; then
      if grep -qE '^[[:space:]]*camera_auto_detect=' "$CAMCFG"; then
        sudo sed -i -E 's|^[[:space:]]*camera_auto_detect=.*|camera_auto_detect=1   # [LocalAiRobot camera]|' "$CAMCFG"
      fi
      sudo sed -i -E '/^[[:space:]]*dtoverlay=imx519.*\[LocalAiRobot camera\]/d' "$CAMCFG"
      echo "   camera overlay: none -- camera_auto_detect=1 restored + LocalAiRobot dtoverlay=imx519 removed (official/auto-detected sensors)."
    else
      echo "   camera overlay: skipped (ROBOT_CAMERA_OVERLAY=$CAMERA_OVERLAY; boot config not found -- not a Pi?)."
    fi ;;
  *)
    if [ -f "$CAMCFG" ]; then
      [ -f "$CAMCFG.localairobot.bak" ] || sudo cp "$CAMCFG" "$CAMCFG.localairobot.bak"   # one-time backup
      if grep -qE '^[[:space:]]*camera_auto_detect=' "$CAMCFG"; then
        sudo sed -i -E 's|^[[:space:]]*camera_auto_detect=.*|camera_auto_detect=0   # [LocalAiRobot camera]|' "$CAMCFG"
      else
        echo 'camera_auto_detect=0   # [LocalAiRobot camera]' | sudo tee -a "$CAMCFG" >/dev/null
      fi
      if grep -qE '^[[:space:]]*dtoverlay=imx519' "$CAMCFG"; then
        sudo sed -i -E "s|^[[:space:]]*dtoverlay=imx519.*|dtoverlay=${CAMERA_OVERLAY}   # [LocalAiRobot camera]|" "$CAMCFG"
      else
        echo "dtoverlay=${CAMERA_OVERLAY}   # [LocalAiRobot camera]" | sudo tee -a "$CAMCFG" >/dev/null
      fi
      echo "   camera overlay set: camera_auto_detect=0 + dtoverlay=${CAMERA_OVERLAY} (IMX519; live after the reboot)"
      echo "   nothing listed by 'rpicam-hello --list-cameras' after reboot? camera's on the other port -- re-run with ROBOT_CAMERA_OVERLAY=imx519,cam1"
      # IMX519 default: auto-run the camera patch so its AUTOFOCUS tuning (Arducam's libcamera fork) is set up
      # for you -- the stock Bookworm tuning has NO AF algorithm, so without this AF is a no-op ("Could not set
      # AF_MODE"). Runs ONLY for the IMX519; other cameras (OV5647/CM3) don't need it. Best-effort: a failure
      # leaves the camera fixed-focus and the install continues. Set ROBOT_CAMERA_AF_INSTALL=0 to skip the fork.
      case "$CAMERA_OVERLAY" in imx519*)
        if [ "${ROBOT_CAMERA_AF_INSTALL:-1}" != 0 ]; then
          echo "   [camera AF] auto-installing IMX519 autofocus tuning (./scripts/patches/pi5/3_camera_imx519.sh)..."
          LAR_FROM_INSTALL=1 bash "${REPO}/scripts/patches/pi5/3_camera_imx519.sh" \
            || echo "   (AF tuning step failed -- camera still works fixed-focus; re-run ./scripts/patches/pi5/3_camera_imx519.sh later)"
        else
          echo "   [camera AF] skipped (ROBOT_CAMERA_AF_INSTALL=0). Enable later:  ./scripts/patches/pi5/3_camera_imx519.sh"
        fi ;;
      esac
    else
      echo "   (boot config not found -- skipping camera overlay; not a Pi?)"
    fi ;;
esac

# Camera AUTOFOCUS (IMX519) is set up by a PATCH now, not an install flag: ./scripts/patches/pi5/3_camera_imx519.sh
# installs Arducam's AF tuning (their libcamera fork). It's kept out of the plain install because it's a
# THIRD-PARTY system change that can conflict with later apt libcamera updates -- so it stays an explicit,
# discoverable per-camera step (no flag to remember). Camera Module 3 (IMX708) has first-class AF with no fork.

# USB power headroom on BATTERY. The robot runs off a UBEC (LiPo -> 5V) into the UPS HAT / USB-C, and a bare
# 5V feed CANNOT do USB-PD negotiation -- so the Pi 5 firmware assumes a weak supply and CLAMPS all USB ports
# to 600mA total. The robot's USB load (speaker + mic + face panel) blows past that, and devices BROWN OUT and
# drop off the bus mid-run (aplay 'No such device' mid-word, /dev/ttyACM0 vanishing, the mic disappearing from
# arecord -l). usb_max_current_enable=1 lifts the clamp to the full 1.6A -- the UBEC must actually supply ~5A
# (it does; that's why it was sized for the Pi 5). Harmless on the bench PSU (PD already negotiates 5A).
# Idempotent + tagged; cleanup_pi.sh --deep removes it. Set ROBOT_USB_FULL_CURRENT=0 to skip.
if [ "${ROBOT_USB_FULL_CURRENT:-1}" != 0 ] && [ -f "$CAMCFG" ]; then
  if grep -qE '^[[:space:]]*usb_max_current_enable=' "$CAMCFG"; then
    sudo sed -i -E 's|^[[:space:]]*usb_max_current_enable=.*|usb_max_current_enable=1   # [LocalAiRobot power]|' "$CAMCFG"
  else
    echo 'usb_max_current_enable=1   # [LocalAiRobot power]' | sudo tee -a "$CAMCFG" >/dev/null
  fi
  echo "   USB full current enabled (usb_max_current_enable=1): battery/UBEC power can't PD-negotiate, so"
  echo "   without this the Pi clamps USB to 600mA and the speaker/mic/face brown out. Live after the reboot."
fi

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
# USB webcam instead of the CSI camera? that path needs OpenCV (the CSI/picamera2 default does not), so it is
# NOT in requirements.txt -- run scripts/patches/pi5/3_camera_usb_webcam.sh, which sets the keys + installs cv2.

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
# Piper and committed (see sounds/README.md), so a fresh clone already has them; this makes sure the folders
# exist AND -- since Piper is installed above -- synthesizes any MISSING cues as a safety net (it never
# overwrites a committed WAV; only --force would). An empty folder is a silent no-op.
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/wake_word_ack" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/thinking_still_sounds" \
         "${REPO}/sounds/remote_ack_sounds" "${REPO}/sounds/sleep_sounds" "${REPO}/sounds/error_sounds"
if [ -x "${H}/piper/piper/piper" ] && [ -f "${H}/piper/${VOICE}.onnx" ]; then
  echo "   synthesizing any MISSING earcons with Piper (committed ones are kept)..."
  bash "${REPO}/scripts/generate_sounds.sh" || echo "   (earcon generation skipped/failed -- committed WAVs still ship)"
else
  echo "   (Piper not found at ~/piper -- skipping earcon generation; committed WAVs still ship)"
fi

# Face templates: render any MISSING procedural face PNGs (brain/faces/<state>/01.png) with the brain venv's
# Pillow -- committed art is kept, and new states (e.g. sleeping, inquisitive) get a starting template. The
# face ALSO renders procedurally live with no PNGs, so this is a convenience/consistency step, never required.
echo "   rendering any MISSING face templates (brain/faces/<state>/01.png)..."
bash "${REPO}/scripts/generate_faces.sh" || echo "   (face template render skipped/failed -- the face still renders procedurally)"

# Fill YOUR_USER in config/robot.env with the current Linux user -- whisper (STT) + piper (TTS) run from
# these exact /home/<user>/... paths. Idempotent: once replaced there's no YOUR_USER left (re-run = no-op).
if [ -f "${REPO}/config/robot.env" ]; then
  sed -i "s/YOUR_USER/$(whoami)/g" "${REPO}/config/robot.env"
  echo ">> config: whisper/piper paths set to /home/$(whoami)/...  (YOUR_USER -> $(whoami))"
fi

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
echo ">> audio: pointing ALSA's default output at your USB speaker + writing the device names into config/robot.env"
bash "${REPO}/scripts/setup_audio.sh" || true

# --bootstrap: enable boot autostart. Reuses run_me.sh --install-boot (single source of truth for the
# systemd unit), then makes network-online.target a REAL wait so the brain starts AFTER Wi-Fi/SSH (and
# Comitup) are up. The brain is offline-capable, so that wait is convenience, not a requirement.
if [ "$BOOTSTRAP" = 1 ]; then
  echo
  echo ">> [bootstrap] enabling boot autostart (systemd) so the brain launches on every boot..."
  if bash "${REPO}/scripts/run_me.sh" --install-boot; then
    if sudo systemctl enable NetworkManager-wait-online.service 2>/dev/null; then
      echo "   network wait enabled -- brain starts after the network is up."
      echo "   (offline robot: if booting away from known Wi-Fi is slow, 'sudo systemctl disable NetworkManager-wait-online.service')"
    else
      echo "   (NetworkManager-wait-online not enabled -- NetworkManager may be inactive; the After= ordering still applies.)"
    fi
  else
    echo "   (bootstrap: boot-service install failed -- run './scripts/run_me.sh --install-boot' by hand.)"
  fi
fi

echo ">> DONE -- offline AI stack installed (Ollama + gemma4/moondream, whisper.cpp, Piper, camera + face"
echo "   libs, the Python venv); your username + USB audio devices are set in config/robot.env."
echo
echo "Next -- finish the 5-step setup at the top of the README (full walkthrough: docs/assembly.md):"
echo
echo "   * Reboot so I2C + your new device groups take effect:   sudo reboot"
echo "     ...then SSH back in:   cd ~/LocalAiRobot        (new shells auto-load config/robot.env)"
echo
echo "   3. Patch in your ESC's tested defaults:   ./scripts/patches/common/<your-esc>.sh   (e.g. 1_esc_hb_qr_1060.sh)"
echo "   4. Prove the wiring (ESC ON, wheels OFF):  ./scripts/run_diagnostics.sh --wheels-off"
echo "   5. Drive it:   ./scripts/run_me.sh         (live: say 'hey jarvis' | bench: ./scripts/run_me.sh --dry)"
echo
if [ "$BOOTSTRAP" = 1 ]; then
  echo "   Boot autostart is ENABLED (--bootstrap): the brain starts on the next boot. Watch it with"
  echo "   'journalctl -u robot.service -f'; undo with './scripts/run_me.sh --disable-boot'."
else
  echo "   Auto-start the brain on every boot?   ./scripts/install_stack_pi5.sh --bootstrap   (or run_me.sh --install-boot)"
fi
echo "   Diagnostics (step 4) flags anything wrong -- audio not right? just re-run ./scripts/setup_audio.sh."
echo "   Inspect the robot's memory any time:   ./scripts/rag.sh   (--seen | --all | --forget <name>)"
echo
echo ">> Quick checks -- verify, don't fret (fix only if something's actually off):"
echo "     audio:      speaker-test -D default -t wav -l 1     (hear it now = the VOICE is ready)"
echo "     PWM board:  i2cdetect -y 1                          (after reboot + PCA9685 wired: expect 0x40;"
echo "                 0x70 is the same board's all-call alias).  run_diagnostics.sh (step 4) checks it all."
echo "   Audio silent/wrong? devices are auto-set -- if your hardware differs, re-run ./scripts/setup_audio.sh."
echo
echo "${GRN}==============================================================${RST}"
echo "${GRN}   ✓  LocalAiRobot Brain Successfully Installed !!!${RST}"
echo "${GRN}==============================================================${RST}"

# Offer to reboot now -- I2C, the new device-group membership, and the camera boot overlay only take effect
# after a reboot. Interactive only: skip cleanly when piped / non-interactive (curl | bash, CI) so it can't hang.
echo
if [ -t 0 ]; then
  printf ">> A reboot is needed for I2C, your new device groups, and the camera overlay to take effect.\n"
  printf "   Reboot now? [y/N] "
  read -r REBOOT_ANS || REBOOT_ANS=""
  case "$REBOOT_ANS" in
    [Yy]*) echo "   rebooting..."; sudo reboot ;;
    *)     echo "   Skipped -- reboot later with:  sudo reboot" ;;
  esac
else
  echo ">> Reboot required for I2C, device groups, and the camera overlay to take effect:  sudo reboot"
fi
