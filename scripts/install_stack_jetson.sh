#!/usr/bin/env bash
# Installs the offline AI stack + NVIDIA Jetson (Orin Nano, JetPack 6 / Ubuntu 22.04, arm64) hardware deps
# for the RC crawler: Ollama + gemma4/moondream (GPU/CUDA), whisper.cpp (CUDA), Piper, OpenCV camera, the
# face-panel library, and the Python venv. Needs internet for THIS step only. Re-runnable.
#
# This is the Jetson counterpart of scripts/install_stack_pi5.sh. Differences vs the Pi:
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

# Install-time flags -- precedence: inline env var > config/robot.env value > default (1 = ON). Read config
# SURGICALLY (grep, not source) so it can NEVER clobber the OTHER inline overrides this installer documents.
_cfg_flag() {   # _cfg_flag VARNAME DEFAULT
  local name="$1" def="$2" cur="${!1:-}" val
  if [ -z "$cur" ] && [ -f "${REPO}/config/robot.env" ]; then
    val="$(sed -n "s/^${name}=\([0-9]\+\).*/\1/p" "${REPO}/config/robot.env" | head -1)"
    [ -n "$val" ] && cur="$val"
  fi
  printf -v "$name" '%s' "${cur:-$def}"
}
_cfg_flag ROBOT_INSTALL_COMITUP 1     # Comitup captive-portal Wi-Fi fallback (docs/change-pi-wifi.md Tier 2)
_cfg_flag ROBOT_PERSIST_JOURNAL 1     # persistent systemd journal (/var/log/journal), boot logs survive reboots
_cfg_flag ROBOT_BOOT_TO_CONSOLE 1     # boot to console (multi-user.target), NOT the desktop -- headless robot + the DSI/HDMI face needs /dev/fb0

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
  echo "   this script targets the Jetson; on a Pi use scripts/install_stack_pi5.sh instead."
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

# Boot to the CONSOLE (multi-user.target), NOT the graphical desktop. The robot is a HEADLESS appliance and
# its DSI/HDMI face is drawn straight to /dev/fb0 -- under the desktop the compositor OWNS the framebuffer, so
# the face never shows (and the desktop just wastes the Orin's shared RAM). Idempotent. Opt out with
# ROBOT_BOOT_TO_CONSOLE=0 (keep the desktop). Takes effect on the reboot at the end of install.
if [ "${ROBOT_BOOT_TO_CONSOLE}" = 1 ] && command -v systemctl >/dev/null 2>&1; then
  if sudo systemctl set-default multi-user.target >/dev/null 2>&1; then
    echo "   boot target -> multi-user.target (console; the DSI/HDMI face renders on /dev/fb0)."
    echo "   keep a desktop instead: ROBOT_BOOT_TO_CONSOLE=0, or undo with 'sudo systemctl set-default graphical.target'."
  else
    echo "   (could not set multi-user.target -- set it by hand: sudo systemctl set-default multi-user.target)"
  fi
fi

echo ">> [3/8] performance mode (MAXN + clocks) for lowest inference latency"
sudo nvpmodel -m 0 2>/dev/null || echo "   (nvpmodel not found -- set the max power mode from the desktop if needed)"
sudo jetson_clocks 2>/dev/null || echo "   (jetson_clocks not found -- skipping)"

echo ">> [4/8] Ollama (Jetson CUDA runtime) + models (text intent + vision)"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
# Step 20: guard each pull so a 404 / network drop prints its own recovery hint before exiting.
if ! ollama pull "${MODEL}"; then
  echo "!! 'ollama pull ${MODEL}' failed -- Gemma 4 needs a RECENT Ollama. Upgrade it" >&2
  echo "   (curl -fsSL https://ollama.com/install.sh | sh) and re-run. Version now: $(ollama --version 2>/dev/null || echo '?')" >&2
  exit 1
fi
if ! ollama pull "${VISION_MODEL}"; then
  echo "!! 'ollama pull ${VISION_MODEL}' failed -- check the model name / network and re-run." >&2
  exit 1
fi
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
# Step 23: tmp+mv so an interrupted download can't leave a TRUNCATED voice the [ -f ] guard then skips.
[ -f "${VOICE}.onnx" ]      || { curl -fsSLo "${VOICE}.onnx.part"      "${VB}/${VOICE}.onnx"      && mv "${VOICE}.onnx.part"      "${VOICE}.onnx"; }
[ -f "${VOICE}.onnx.json" ] || { curl -fsSLo "${VOICE}.onnx.json.part" "${VB}/${VOICE}.onnx.json" && mv "${VOICE}.onnx.json.part" "${VOICE}.onnx.json"; }

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

# Re-verify cv2 AFTER pip: a pip package can shadow a system lib JetPack's cv2 was compiled against (classic:
# a pip numpy 2.x over apt numpy 1.x -> "import cv2" dies with "_ARRAY_API not found"). The pre-pip check
# above cannot see that, and a silent cv2 break means the camera is dead at runtime -- so fail HERE, named.
./.venv/bin/python -c "import numpy, cv2; print('   cv2', cv2.__version__, 'OK with numpy', numpy.__version__)" || {
  echo "!! import cv2 BROKE inside the venv after pip install -- usually a pip package (e.g. numpy 2.x)" >&2
  echo "   shadowing the system numpy that JetPack's cv2 was built against. Check the numpy pin in" >&2
  echo "   brain/requirements-jetson.txt; the camera/vision would be silently dead otherwise." >&2
  exit 1
}

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

# Concierge Mode (opt-in; ROBOT_CONCIERGE_MODE) face recognition: download the LOCAL ArcFace model. cv2 is
# already the system OpenCV (apt, above) and onnxruntime comes with the venv, so this is usually just the
# model. Best-effort -- a failure NEVER fails the install (crawler mode doesn't need it). Idempotent.
echo ">> Concierge face-recognition model + deps (for ROBOT_CONCIERGE_MODE; best-effort)"
bash "${REPO}/scripts/provision_face_id.sh" \
  || echo "   (face-id provisioning skipped/failed -- re-run ./scripts/provision_face_id.sh later; crawler mode is unaffected)"

# Earcons + face templates (synthesized/rendered with the venv, same as the Pi installer; committed WAVs kept).
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/wake_word_ack" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/thinking_still_sounds" \
         "${REPO}/sounds/remote_ack_sounds" "${REPO}/sounds/sleep_sounds" "${REPO}/sounds/error_sounds" "${REPO}/sounds/spook_sounds"
if [ -x "${H}/piper/piper/piper" ] && [ -f "${H}/piper/${VOICE}.onnx" ]; then
  echo "   synthesizing any MISSING earcons with Piper (committed ones are kept)..."
  bash "${REPO}/scripts/generate_sounds.sh" || echo "   (earcon generation skipped/failed -- committed WAVs still ship)"
fi
# Halloween Mode jump-scare stingers (sounds/spook_sounds/stinger_*.wav) -- LOUD synthetic hits, stdlib-only
# (no Piper needed). Committed ones are kept; only MISSING ones are synthesized. Only used when
# ROBOT_HALLOWEEN_MODE=1, so a failure here never affects crawler/concierge modes.
bash "${REPO}/scripts/generate_spook_stingers.sh" || echo "   (spook stinger synth skipped/failed -- only affects ROBOT_HALLOWEEN_MODE=1)"
echo "   rendering any MISSING face templates..."
bash "${REPO}/scripts/generate_faces.sh" || echo "   (face template render skipped/failed -- the face still renders procedurally)"
# Halloween Mode spook faces (brain/faces/halloween/*.png) -- committed art is kept; renders any that are
# missing. Only used when ROBOT_HALLOWEEN_MODE=1, so a failure here never affects crawler/concierge modes.
bash "${REPO}/scripts/generate_halloween_faces.sh" || echo "   (halloween face render skipped/failed -- only affects ROBOT_HALLOWEEN_MODE=1)"

# Fill YOUR_USER in config/robot.env with the current Linux user (whisper/piper paths).
if [ -f "${REPO}/config/robot.env" ]; then
  sed -i "s/YOUR_USER/$(whoami)/g" "${REPO}/config/robot.env"
  echo ">> config: whisper/piper paths set to /home/$(whoami)/...  (YOUR_USER -> $(whoami))"
fi

# Apply the JETSON profile to config/robot.env idempotently (same spirit as the YOUR_USER sed above), so a
# skipped hand-edit can't leave the robot driving with the stock Pi BCM pins on the BOARD-numbered header --
# where every ultrasonic gate reads 'clear' and the forward obstacle cut never fires. Gated on the device
# tree so a mistaken run on a Pi can NEVER rewrite a Pi config; each sed rewrites ONLY the stock/empty value,
# so re-runs and later hand-edits are safe. ROBOT_BOARD is forced (not left 'auto') so a mis-detect can't
# silently fall back to the pi5 board profile.
if [ -f "${REPO}/config/robot.env" ] && grep -qiE 'jetson|orin|tegra' /proc/device-tree/model 2>/dev/null; then
  ENVF="${REPO}/config/robot.env"
  sed -i -E 's/^ROBOT_BOARD=auto([[:space:]]|$)/ROBOT_BOARD=jetson\1/'   "$ENVF"   # force the board id
  sed -i -E 's/^(ROBOT_I2C_BUS=)([[:space:]]|#|$)/\17\2/'                "$ENVF"   # 40-pin header bus (empty -> 7)
  # Comment out the six LIVE Pi BCM pin lines so brain/boards/jetson.py's BOARD defaults apply (an env value
  # always beats a board default -- driver.py). No-op on already-commented lines, so re-runs are safe.
  sed -i -E 's/^(ROBOT_(LEFT_|RIGHT_)?(TRIG|ECHO)_PIN=)/# \1/'           "$ENVF"
  echo ">> config: ROBOT_BOARD=jetson, ROBOT_I2C_BUS=7; Pi BCM sensor pins commented out"
  echo "           (jetson BOARD defaults 16/18/29/31/32/33 now apply -- VERIFY them against your wiring +"
  echo "            'sudo /opt/nvidia/jetson-io/jetson-io.py' before driving)."
  # Camera: this script provisions the USB/V4L2 path (the IMX519 CSI driver is the separate step below). If a
  # USB cam node is present and no CSI sensor subdevice is, and the keys are still empty, point them at the USB
  # cam -- empty otherwise means the CSI/Argus pipeline, which has no sensor on a USB-only build.
  if [ -c /dev/video0 ] && ! ls /dev/v4l-subdev* >/dev/null 2>&1; then
    sed -i -E 's/^(ROBOT_CAMERA_BACKEND=)([[:space:]]|#|$)/\1v4l2\2/'    "$ENVF"
    sed -i -E 's/^(ROBOT_CAMERA_SOURCE=)([[:space:]]|#|$)/\10\2/'        "$ENVF"
    echo ">> config: ROBOT_CAMERA_BACKEND=v4l2, ROBOT_CAMERA_SOURCE=0 (USB webcam detected)"
  fi
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

# Field-recovery + logging (secret-free -- NO passwords are ever stored/committed). Both flags default ON and
# are set in config/robot.env (INSTALL-TIME FLAGS section); disable with ROBOT_X=0 in config or inline:
#   * PERSISTENT JOURNAL: if the systemd journal is kept in RAM, `journalctl -u robot.service` is WIPED on
#     every reboot -- you can't read what the boot service did before the last power cycle. Creating
#     /var/log/journal makes it persist to disk (journald auto-caps + rotates). Idempotent (a no-op if it's
#     already persistent). Skip with ROBOT_PERSIST_JOURNAL=0.
#   * COMITUP captive portal: the password-free Wi-Fi backdoor -- when no known network is around, the board
#     hosts its own AP + web page to pick a Wi-Fi (docs/change-pi-wifi.md, Tier 2). It stores NO password;
#     the portal collects the target Wi-Fi's password at runtime. Skip with ROBOT_INSTALL_COMITUP=0.
#     (The Tier-1 phone-hotspot fallback needs a password, so it stays a manual one-liner -- never committed.)
# Persistent journal (robust): an explicit Storage=persistent drop-in + a VERIFY step, via the shared helper
# (single source of truth; also runnable standalone). Fixes the Storage=auto timing trap that silently left a
# field robot logging to RAM -- so a crash/brown-out is readable next boot with 'journalctl -u robot.service -b -1'.
ROBOT_PERSIST_JOURNAL="${ROBOT_PERSIST_JOURNAL}" bash "${REPO}/scripts/enable_persistent_journal.sh" || true
# Detect-captures purge (cron). Concierge Mode keeps the frame that decided each greeting in ~/detect-captures
# so you can see WHY the robot spoke; a 24/7 greeter would otherwise grow that folder without limit. This
# schedules the daily cleanup (scripts/purge_detect_captures.sh). Idempotent, tagged, and skippable with
# ROBOT_DETECT_CAPTURE_CRON=0. Best-effort -- a missing cron daemon never fails the install.
bash "${REPO}/scripts/install_detect_capture_cron.sh" || true
if [ "${ROBOT_INSTALL_COMITUP}" = 1 ]; then
  echo ">> Comitup captive-portal fallback (default ON; password-free Wi-Fi onboarding)..."
  if sudo apt install -y comitup; then
    # enable (NOT --now): activating it mid-install could reshuffle the network and drop your SSH -- let it
    # come up on the reboot at the end of install instead.
    sudo systemctl enable comitup >/dev/null 2>&1 || true
    echo "   installed + enabled for next boot. Stranded -> it broadcasts 'comitup-<id>'; join it from your"
    echo "   phone and pick a Wi-Fi in the portal. Details/restyle: docs/change-pi-wifi.md (Tier 2)."
  else
    echo "   (comitup not installed -- not in the apt repo? see docs/change-pi-wifi.md for the manual steps.)"
  fi
fi

if [ "$BOOTSTRAP" = 1 ]; then
  echo
  echo ">> [bootstrap] enabling boot autostart (systemd) so the brain launches on every boot..."
  # JetPack 6 is Ubuntu 22.04 + NetworkManager, so make network-online.target a REAL wait (matches the Pi
  # installer) -- otherwise robot.service's After=network-online.target completes instantly and the phone
  # HTTP endpoint can come up before Wi-Fi. The brain is offline-capable, so this is convenience ordering.
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

echo ">> DONE -- Jetson AI stack installed (Ollama+CUDA, whisper.cpp+CUDA, Piper, OpenCV camera, face libs, venv)."
echo
echo "Next -- Jetson-specific bring-up:"
echo "   * Reboot so the group membership + any jetson-io pin changes take effect:   sudo reboot"
echo "   * Board + pins: this script set ROBOT_BOARD=jetson + ROBOT_I2C_BUS=7 and commented out the Pi BCM"
echo "     pins in config/robot.env -- VERIFY the jetson BOARD pins (16/18/29/31/32/33) match your wiring."
echo "   * PWM board:  i2cdetect -y 7   (expect 0x40)."
echo "   * Patch YOUR hardware (ESC REQUIRED, + any sensor/IMU/encoder; full catalog: scripts/patches/README.md):"
echo "       ./scripts/patches/common/1_esc_hb_qr_1060.sh   (your ESC -- writes its throttle map)"
echo "   * Prove the wiring (ESC ON, wheels OFF):  ./scripts/run_diagnostics.sh --wheels-off"
echo "   * Camera (USB): this script set it up if it found one; else run ONE of"
echo "       ./scripts/patches/jetson/3_camera_usb_webcam.sh      # any USB webcam"
echo "       ./scripts/patches/jetson/3_camera_usb_ir_1080p.sh    # 1080p IR-CUT night-vision module"
echo "       (CSI camera support was removed -- the supported builds are USB on both boards.)"
echo "   * Drive it:   ./scripts/run_me.sh        (bench: ./scripts/run_me.sh --dry)"
echo "   * Auto-start on every boot (optional):   ./scripts/run_me.sh --install-boot"
echo
echo "${GRN}==============================================================${RST}"
echo "${GRN}   ✓  LocalAiRobot Brain (Jetson) Installed !!!${RST}"
echo "${GRN}==============================================================${RST}"

# Offer to reboot now -- the new device-group membership and any jetson-io pin changes only take effect after
# a reboot. Interactive only: skip cleanly when piped / non-interactive (curl | bash, CI) so it can't hang.
echo
if [ -t 0 ]; then
  printf ">> A reboot is needed for your new device groups, the console boot (and any jetson-io pin changes) to take effect.\n"
  printf "   Reboot now? [y/N] "
  read -r REBOOT_ANS || REBOOT_ANS=""
  case "$REBOOT_ANS" in
    [Yy]*) echo "   rebooting..."; sudo reboot ;;
    *)     echo "   Skipped -- reboot later with:  sudo reboot" ;;
  esac
else
  echo ">> Reboot required for device groups, the console boot (and any jetson-io pin changes) to take effect:  sudo reboot"
fi
