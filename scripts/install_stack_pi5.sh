#!/usr/bin/env bash
# Installs the fully-offline AI stack + Raspberry Pi 5 hardware deps for the RC crawler (arm64):
# Ollama + gemma4:e2b-it-qat (text) + moondream (vision), whisper.cpp, Piper, picamera2, the face-panel library,
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

# --bootstrap: after installing the stack, also enable the systemd boot service so the brain auto-starts
# on every boot (see scripts/robot.service + run_me.sh --install-boot). Off by default -- a plain install
# never touches boot behaviour.
BOOTSTRAP=0
for arg in "$@"; do
  case "$arg" in
    --bootstrap)  BOOTSTRAP=1 ;;
    --imx519-af)  echo "note: the CSI camera support was removed -- this build is USB-only. Run ./scripts/patches/pi5/3_camera_usb_webcam.sh (or 3_camera_usb_ir_1080p.sh)." >&2; exit 2 ;;
    -h|--help)    echo "usage: $(basename "$0") [--bootstrap]"; \
                  echo "  --bootstrap  also install the systemd boot service (brain auto-starts on boot)"; \
                  echo "  (camera? no flag -- run ./scripts/patches/pi5/3_camera_usb_webcam.sh or 3_camera_usb_ir_1080p.sh after)"; exit 0 ;;
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
# Camera boot overlay. DEFAULT IS 'none': camera_auto_detect=1 and NO dtoverlay of ours. That is what a USB
# webcam wants (it is not a CSI device at all and ignores the boot config), and also what every OFFICIAL Pi
# camera wants (OV5647/IMX219/IMX708 are auto-detected). So the default is right for the whole supported set.
#
# It used to default to 'imx519,cam0' for the third-party Arducam module, and that was the wrong default. On a
# USB build it wrote a CSI overlay nobody asked for -- inert while no ribbon camera is attached, but dead boot
# config, and the moment a CSI sensor IS plugged in that overlay takes /dev/video0 out from under the webcam.
# It also auto-installed Arducam's libcamera FORK: a third-party system change, made silently, on machines
# whose camera is a USB device that can never use it. The CSI camera patches are gone (USB only now), so this
# is config-only -- it writes an overlay if you ask for one and installs nothing.
#
#   Need some other CSI sensor? pass its overlay and this writes it verbatim (it is not validated):
#       ROBOT_CAMERA_OVERLAY=imx296,cam1 ./scripts/install_stack_pi5.sh
#   USB webcam / USB IR module (the supported builds): just run the installer, then pick your camera patch:
#       ./scripts/patches/pi5/3_camera_usb_webcam.sh      or  3_camera_usb_ir_1080p.sh
CAMERA_OVERLAY="${ROBOT_CAMERA_OVERLAY:-none}"
CAMCFG=/boot/firmware/config.txt; [ -f "$CAMCFG" ] || CAMCFG=/boot/config.txt
case "$CAMERA_OVERLAY" in
  none|off|auto)
    # USB cameras and auto-detected sensors (OV5647/IMX219/IMX708) both want camera_auto_detect=1 and no
    # overlay of ours. Actively UNDO a previous CSI install so upgrading an existing robot is clean, not just
    # a fresh flash: restore camera_auto_detect=1 and delete our overlay line. Keyed on the [LocalAiRobot
    # camera] TAG rather than the sensor name, so it removes whichever overlay a past install wrote (the old
    # default was dtoverlay=imx519) -- a dtoverlay YOU added by hand is untagged and is left alone.
    if [ -f "$CAMCFG" ]; then
      if grep -qE '^[[:space:]]*camera_auto_detect=' "$CAMCFG"; then
        sudo sed -i -E 's|^[[:space:]]*camera_auto_detect=.*|camera_auto_detect=1   # [LocalAiRobot camera]|' "$CAMCFG"
      fi
      sudo sed -i -E '/^[[:space:]]*dtoverlay=.*\[LocalAiRobot camera\]/d' "$CAMCFG"
      echo "   camera overlay: none -- camera_auto_detect=1 restored, any LocalAiRobot dtoverlay removed (USB + official sensors)."
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
      if grep -qE '^[[:space:]]*dtoverlay=.*\[LocalAiRobot camera\]' "$CAMCFG"; then
        sudo sed -i -E "s|^[[:space:]]*dtoverlay=.*\[LocalAiRobot camera\].*|dtoverlay=${CAMERA_OVERLAY}   # [LocalAiRobot camera]|" "$CAMCFG"
      else
        echo "dtoverlay=${CAMERA_OVERLAY}   # [LocalAiRobot camera]" | sudo tee -a "$CAMCFG" >/dev/null
      fi
      echo "   camera overlay set: camera_auto_detect=0 + dtoverlay=${CAMERA_OVERLAY} (live after the reboot)"
      echo "   nothing listed by 'rpicam-hello --list-cameras' after reboot? camera's on the other CSI port -- re-run with ...,cam1"
      echo "   NOTE: config only. This installs no camera driver or tuning -- the supported builds are USB"
      echo "         (./scripts/patches/pi5/3_camera_usb_webcam.sh or 3_camera_usb_ir_1080p.sh) and need no overlay."
    else
      echo "   (boot config not found -- skipping camera overlay; not a Pi?)"
    fi ;;
esac

# No autofocus step here any more. The supported cameras are USB modules, and both are FIXED focus (the IR
# module's 3.6mm lens has enough depth of field that 1-3m is sharp with nothing to hunt for), so the install
# has no camera driver or tuning work to do at all -- ROBOT_CAMERA_AUTOFOCUS=0 and that's the end of it. The
# Arducam AF fork this used to install was a third-party libcamera replacement that could break on an apt
# libcamera update; it went out with the CSI patches. `git log --diff-filter=D -- scripts/patches/pi5` finds it.

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

# Boot to the CONSOLE (multi-user.target), NOT the graphical desktop. The robot is a HEADLESS appliance and
# its DSI/HDMI face is drawn straight to /dev/fb0 -- under the desktop the Wayland compositor OWNS the
# framebuffer, so the face never shows (and the desktop just wastes RAM/CPU). Idempotent. Opt out with
# ROBOT_BOOT_TO_CONSOLE=0 (keep the desktop). Takes effect on the reboot at the end of install.
if [ "${ROBOT_BOOT_TO_CONSOLE}" = 1 ] && command -v systemctl >/dev/null 2>&1; then
  if sudo systemctl set-default multi-user.target >/dev/null 2>&1; then
    echo "   boot target -> multi-user.target (console; the DSI/HDMI face renders on /dev/fb0)."
    echo "   keep a desktop instead: ROBOT_BOOT_TO_CONSOLE=0, or undo with 'sudo systemctl set-default graphical.target'."
  else
    echo "   (could not set multi-user.target -- set it by hand: sudo systemctl set-default multi-user.target)"
  fi
  # Hide the blinking text-console CURSOR so it doesn't sit on top of the framebuffer face. cmdline.txt is a
  # SINGLE line -> append with sed (never a newline). Idempotent (only added once).
  CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  if [ -f "$CMDLINE" ] && ! grep -q 'vt.global_cursor_default=0' "$CMDLINE"; then
    if sudo sed -i 's/$/ vt.global_cursor_default=0/' "$CMDLINE"; then
      echo "   hid the console cursor (vt.global_cursor_default=0 in $(basename "$CMDLINE"); live after reboot)."
    fi
  fi
fi

echo ">> [4/8] Ollama + models (text intent + vision)"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
# Step 20: guard each pull so a 404 (older Ollama) / network drop prints its OWN recovery hint before
# exiting, instead of set -e aborting silently before the remaining install steps.
if ! ollama pull "${MODEL}"; then
  echo "!! 'ollama pull ${MODEL}' failed -- Gemma 4 needs a RECENT Ollama. Upgrade it" >&2
  echo "   (curl -fsSL https://ollama.com/install.sh | sh) and re-run. Version now: $(ollama --version 2>/dev/null || echo '?')" >&2
  exit 1
fi
if ! ollama pull "${VISION_MODEL}"; then   # dedicated vision VLM (ROBOT_VISION_MODEL); default moondream
  echo "!! 'ollama pull ${VISION_MODEL}' failed -- check the model name / network and re-run." >&2
  exit 1
fi

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
# Step 23: download to a .part then mv into place, so an interrupted transfer can't leave a TRUNCATED
# ${VOICE}.onnx that the [ -f ] guard then treats as done on every re-run (-> a silently mute robot).
[ -f "${VOICE}.onnx" ]      || { curl -fsSLo "${VOICE}.onnx.part"      "${VB}/${VOICE}.onnx"      && mv "${VOICE}.onnx.part"      "${VOICE}.onnx"; }
[ -f "${VOICE}.onnx.json" ] || { curl -fsSLo "${VOICE}.onnx.json.part" "${VB}/${VOICE}.onnx.json" && mv "${VOICE}.onnx.json.part" "${VOICE}.onnx.json"; }

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
# requirements.txt now includes opencv-python-headless<5 + onnxruntime, so Concierge face-id works out of the
# box whenever ROBOT_CONCIERGE_MODE is flipped on -- no mode-gated install. (The USB-webcam patch just sets the
# camera keys now; cv2 is already present.)

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

# Concierge Mode face recognition: download the LOCAL ArcFace model. The deps (cv2/onnxruntime/numpy) are
# already installed above via requirements.txt, so this just fetches the model -- and Concierge is then ready
# whenever ROBOT_CONCIERGE_MODE is toggled on. Best-effort -- a failure NEVER fails the install (crawler mode
# doesn't need it; concierge degrades to a silent smile). Idempotent (skips if the model is already present).
echo ">> Concierge face-recognition model (for ROBOT_CONCIERGE_MODE; best-effort)"
bash "${REPO}/scripts/provision_face_id.sh" \
  || echo "   (face-id provisioning skipped/failed -- re-run ./scripts/provision_face_id.sh later; crawler mode is unaffected)"

# Earcon folders for the spoken ack/greeting/thinking/error cues. The WAVs are generated on your PC with
# Piper and committed (see sounds/README.md), so a fresh clone already has them; this makes sure the folders
# exist AND -- since Piper is installed above -- synthesizes any MISSING cues as a safety net (it never
# overwrites a committed WAV; only --force would). An empty folder is a silent no-op.
mkdir -p "${REPO}/sounds/greeting_sounds" "${REPO}/sounds/wake_word_ack" "${REPO}/sounds/ack_sounds" \
         "${REPO}/sounds/thinking_sounds" "${REPO}/sounds/thinking_still_sounds" \
         "${REPO}/sounds/remote_ack_sounds" "${REPO}/sounds/sleep_sounds" "${REPO}/sounds/error_sounds" "${REPO}/sounds/spook_sounds"
if [ -x "${H}/piper/piper/piper" ] && [ -f "${H}/piper/${VOICE}.onnx" ]; then
  echo "   synthesizing any MISSING earcons with Piper (committed ones are kept)..."
  bash "${REPO}/scripts/generate_sounds.sh" || echo "   (earcon generation skipped/failed -- committed WAVs still ship)"
else
  echo "   (Piper not found at ~/piper -- skipping earcon generation; committed WAVs still ship)"
fi
# Halloween Mode jump-scare stingers (sounds/spook_sounds/stinger_*.wav) -- LOUD synthetic hits, stdlib-only
# (no Piper needed). Committed ones are kept; only MISSING ones are synthesized. Only used when
# ROBOT_HALLOWEEN_MODE=1, so a failure here never affects crawler/concierge modes.
bash "${REPO}/scripts/generate_spook_stingers.sh" || echo "   (spook stinger synth skipped/failed -- only affects ROBOT_HALLOWEEN_MODE=1)"

# Face templates: render any MISSING procedural face PNGs (brain/faces/<state>/01.png) with the brain venv's
# Pillow -- committed art is kept, and new states (e.g. sleeping, inquisitive) get a starting template. The
# face ALSO renders procedurally live with no PNGs, so this is a convenience/consistency step, never required.
echo "   rendering any MISSING face templates (brain/faces/<state>/01.png)..."
bash "${REPO}/scripts/generate_faces.sh" || echo "   (face template render skipped/failed -- the face still renders procedurally)"
# Halloween Mode spook faces (brain/faces/halloween/*.png) -- committed art is kept; renders any that are
# missing. Only used when ROBOT_HALLOWEEN_MODE=1, so a failure here never affects crawler/concierge modes.
bash "${REPO}/scripts/generate_halloween_faces.sh" || echo "   (halloween face render skipped/failed -- only affects ROBOT_HALLOWEEN_MODE=1)"

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

# Field-recovery + logging (secret-free -- NO passwords are ever stored/committed). Both flags default ON and
# are set in config/robot.env (INSTALL-TIME FLAGS section); disable with ROBOT_X=0 in config or inline:
#   * PERSISTENT JOURNAL: Raspberry Pi OS often keeps the systemd journal in RAM, so `journalctl -u
#     robot.service` is WIPED on every reboot -- you can't read what the boot service did before the last
#     power cycle. Creating /var/log/journal makes it persist to disk (journald auto-caps + rotates, so it
#     can't fill the disk). Idempotent. Skip with ROBOT_PERSIST_JOURNAL=0.
#   * COMITUP captive portal: the password-free Wi-Fi backdoor -- when no known network is around, the Pi
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
echo "   3. Patch YOUR hardware -- run ONE patch per part you use (full catalog: scripts/patches/README.md):"
echo "        ./scripts/patches/common/1_esc_hb_qr_1060.sh    # your ESC  (REQUIRED -- writes its throttle map)"
echo "        ./scripts/patches/pi5/3_camera_usb_webcam.sh    # your camera -- or 3_camera_usb_ir_1080p.sh"
echo "                                                        # (IR/night-vision module). Pick exactly ONE."
echo "        # + sensor (common/2_sensor_*) / IMU / encoder / concierge face-id patches as needed"
echo "   4. Prove the wiring (ESC ON, WHEELS OFF the ground):  ./scripts/run_diagnostics.sh --wheels-off"
echo "   5. Drive it:   ./scripts/run_me.sh         (live: say 'hey jarvis' | bench: ./scripts/run_me.sh --dry)"
echo
echo "   Then (optional) auto-start the brain on every boot:   ./scripts/run_me.sh --install-boot"
echo
if [ "$BOOTSTRAP" = 1 ]; then
  echo "   Boot autostart is ENABLED (--bootstrap): the brain starts on the next boot. Watch it with"
  echo "   'journalctl -u robot.service -f'; undo with './scripts/run_me.sh --disable-boot'."
else
  echo "   (The optional --install-boot line above enables boot autostart; or bundle it into the install"
  echo "    next time with --bootstrap.)"
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
  printf ">> A reboot is needed for I2C, your new device groups, the camera overlay, and the console boot to take effect.\n"
  printf "   Reboot now? [y/N] "
  read -r REBOOT_ANS || REBOOT_ANS=""
  case "$REBOOT_ANS" in
    [Yy]*) echo "   rebooting..."; sudo reboot ;;
    *)     echo "   Skipped -- reboot later with:  sudo reboot" ;;
  esac
else
  echo ">> Reboot required for I2C, device groups, the camera overlay, and the console boot to take effect:  sudo reboot"
fi
