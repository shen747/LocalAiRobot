#!/usr/bin/env bash
# cleanup_pi.sh -- undo scripts/install_stack_pi5.sh and free the disk it used.
# Three phases: (1) DRY RUN that lists exactly what exists and will be removed/reset,
# (2) asks "Shall I proceed?", (3) only on y/yes does it execute. Re-runnable.
#
#   ./scripts/cleanup_pi.sh            plan, confirm, then remove downloads + Ollama/models + venv + data
#   ./scripts/cleanup_pi.sh --deep     ALSO apt-remove the installer's packages + disable I2C
#   ./scripts/cleanup_pi.sh --repo     ALSO delete the ~/LocalAiRobot clone itself (final step)
#   ./scripts/cleanup_pi.sh --models <name>   remove ONE Ollama model only (to switch the local model)
#   ./scripts/cleanup_pi.sh -n         DRY RUN only: print the plan and exit (never deletes)
#   ./scripts/cleanup_pi.sh -y         skip the confirmation prompt (for non-interactive use)
set -uo pipefail

H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEEP=0; REPO_RM=0; YES=0; DRYRUN=0; MODELS_RM=""
while [ $# -gt 0 ]; do case "$1" in
  --deep)       DEEP=1 ;;
  --repo)       REPO_RM=1 ;;
  -y|--yes)     YES=1 ;;
  -n|--dry-run) DRYRUN=1 ;;
  --models)     shift; MODELS_RM="${1:-}"; [ -n "$MODELS_RM" ] || { echo "--models needs a model name, e.g. --models gemma3:4b"; exit 2; } ;;
  --models=*)   MODELS_RM="${1#--models=}" ;;
  *) echo "unknown option: $1 (use --deep, --repo, --models <name>, -n, -y)"; exit 2 ;;
esac; shift; done

# ---------- safety guards (refuse to delete the wrong thing) ----------
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  echo "Refusing to run as root: sudo/root resolves \$HOME and the repo wrong, so it would clean" >&2
  echo "the wrong files. Run as your normal user -- it sudo's only the steps that need it." >&2
  exit 1
fi
if [ -z "${REPO:-}" ] || [ ! -d "$REPO/scripts" ] || [ ! -d "$REPO/brain" ]; then
  echo "Refusing to run: \$REPO ('${REPO:-}') is not a LocalAiRobot clone (needs scripts/ and brain/)." >&2
  echo "Run it as  ./scripts/cleanup_pi.sh  from inside your clone." >&2
  exit 1
fi

# ---------- --models <name>: just remove ONE Ollama model (for switching the local model) ----------
if [ -n "$MODELS_RM" ]; then
  command -v ollama >/dev/null 2>&1 || { echo "Ollama isn't installed -- nothing to remove."; exit 0; }
  echo "=== cleanup_pi.sh --models -- DRY RUN (nothing removed yet) ==="
  if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qF -- "$MODELS_RM"; then
    echo "Would remove ONE Ollama model (Ollama + venv + repo all kept):"
    echo "   ollama rm $MODELS_RM"
  else
    echo "Model '$MODELS_RM' is not in 'ollama list' -- nothing to do."; exit 0
  fi
  [ "$DRYRUN" = 1 ] && { echo; echo "Dry run only (-n): no changes made."; exit 0; }
  if [ "$YES" != 1 ]; then
    echo; read -r -p "Shall I proceed? [y/N] " ans || true
    case "${ans:-}" in y|Y|yes|YES) ;; *) echo "Aborted -- nothing was changed."; exit 1 ;; esac
  fi
  echo; echo ">> ollama rm $MODELS_RM"; ollama rm "$MODELS_RM"
  echo "=== DONE.  Switch models: set ROBOT_MODEL / ROBOT_VISION_MODEL in config/robot.env, then 'ollama pull <new>'. ==="
  exit 0
fi

# ---------- phase 1: DRY RUN -- detect real state, build the plan ----------
PLAN=()        # human-readable lines shown to the user
RM_PATHS=()    # files/dirs that exist and will be deleted
APT_PKGS=()    # installer packages currently installed (only with --deep)
SVC=0; OLLAMA=0; I2C_OFF=0; CAM_OFF=0; CAMCFG=""
sz() { du -sh "$1" 2>/dev/null | cut -f1; }   # best-effort size; '' if unreadable

for p in "$H/whisper.cpp" "$H/piper" "$H/turing-smart-screen-python" "$H/openwakeword" \
         "$REPO/brain/.venv" "$H/.localairobot" /tmp/test.wav /tmp/test.jpg; do
  if [ -e "$p" ] || [ -L "$p" ]; then
    RM_PATHS+=("$p"); s="$(sz "$p")"; PLAN+=("delete   $p${s:+   ($s)}")
  fi
done

if [ -f /etc/systemd/system/robot.service ]; then
  SVC=1; PLAN+=("reset    stop + disable + remove  /etc/systemd/system/robot.service  (+ disable the NetworkManager-wait-online boot wait)")
fi

if command -v ollama >/dev/null 2>&1 || [ -e /usr/local/bin/ollama ]; then
  OLLAMA=1
  PLAN+=("remove   Ollama: service + binary + libs + 'ollama' user + models (/usr/local/lib/ollama, /usr/share/ollama, ~/.ollama, ~3 GB)")
fi

BASHRC_LINE=0
if [ -f "$H/.bashrc" ] && grep -q "LocalAiRobot/config/robot.env" "$H/.bashrc"; then
  BASHRC_LINE=1; PLAN+=("reset    remove the config/robot.env auto-load line(s) from ~/.bashrc")
fi

# ~/.asoundrc written by scripts/setup_audio.sh (marker-guarded so a hand-written one is left alone).
ASOUND_RM=0
if [ -f "$H/.asoundrc" ] && grep -qF "LocalAiRobot: default audio device" "$H/.asoundrc"; then
  ASOUND_RM=1
  if [ -f "$H/.asoundrc.bak" ]; then
    PLAN+=("reset    remove ~/.asoundrc (setup_audio.sh) + restore your ~/.asoundrc.bak")
  else
    PLAN+=("reset    remove ~/.asoundrc (setup_audio.sh -> restores ALSA's default output)")
  fi
fi

if [ "$DEEP" = 1 ]; then
  for pkg in python3-picamera2 portaudio19-dev libasound2-dev i2c-tools \
             cmake build-essential python3-dev python3-venv; do
    dpkg -s "$pkg" >/dev/null 2>&1 && APT_PKGS+=("$pkg")
  done
  [ ${#APT_PKGS[@]} -gt 0 ] && PLAN+=("apt-rm   ${APT_PKGS[*]}")
  for f in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$f" ] && grep -Eq '^[[:space:]]*dtparam=i2c_arm=on' "$f"; then
      I2C_OFF=1; PLAN+=("reset    disable I2C in $f (raspi-config do_i2c 1)"); break
    fi
  done
  for f in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$f" ] && grep -q '# \[LocalAiRobot camera\]' "$f"; then
      CAM_OFF=1; CAMCFG="$f"; PLAN+=("reset    remove the IMX519 camera overlay from $f (restore camera_auto_detect=1)"); break
    fi
  done
  for f in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$f" ] && grep -q '# \[LocalAiRobot power\]' "$f"; then
      PWR_OFF=1; PWRCFG="$f"; PLAN+=("reset    remove usb_max_current_enable from $f (restore the 600mA USB default)"); break
    fi
  done
fi

[ "$REPO_RM" = 1 ] && PLAN+=("delete   $REPO   (this clone, incl. this script -- runs LAST)")

echo "=== cleanup_pi.sh -- DRY RUN (nothing has been deleted) ==="
if [ ${#PLAN[@]} -eq 0 ]; then
  echo "Nothing to do: the install artifacts are already gone."
  exit 0
fi
echo "These items exist and WOULD be removed / reset:"
echo
for line in "${PLAN[@]}"; do echo "   $line"; done
echo
echo "(Ollama / apt / I2C / service steps use sudo -- you may be asked for your password.)"
[ "$DEEP" = 1 ] && echo "(left as-is: group memberships i2c/gpio/dialout/audio/video + git/curl/alsa-utils -- harmless / OS defaults)"

[ "$DRYRUN" = 1 ] && { echo; echo "Dry run only (-n): no changes made."; exit 0; }

# ---------- phase 2: confirm ----------
if [ "$YES" != 1 ]; then
  echo
  read -r -p "Shall I proceed? [y/N] " ans || true
  case "${ans:-}" in
    y|Y|yes|YES) ;;
    *) echo "Aborted -- nothing was changed."; exit 1 ;;
  esac
fi

# ---------- phase 3: execute exactly what the plan listed ----------
echo
echo "=== executing ==="
if [ ${#RM_PATHS[@]} -gt 0 ]; then
  for p in "${RM_PATHS[@]}"; do echo ">> rm -rf $p"; rm -rf "$p"; done
fi

if [ "$SVC" = 1 ]; then
  echo ">> remove robot.service"
  sudo systemctl disable --now robot.service 2>/dev/null || true
  sudo rm -f /etc/systemd/system/robot.service
  # undo the network wait that install_stack_pi5.sh --bootstrap enables alongside the boot service
  sudo systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
  sudo systemctl daemon-reload 2>/dev/null || true
fi

if [ "$OLLAMA" = 1 ]; then
  echo ">> remove Ollama"
  sudo systemctl disable --now ollama 2>/dev/null || true
  sudo rm -f /etc/systemd/system/ollama.service
  OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
  [ -n "$OLLAMA_BIN" ] && sudo rm -f "$OLLAMA_BIN"
  sudo rm -f /usr/local/bin/ollama
  sudo rm -rf /usr/local/lib/ollama    # runtime libs (modern installer puts them here)
  sudo rm -rf /usr/share/ollama
  rm -rf "$H/.ollama"
  sudo userdel ollama 2>/dev/null || true
  sudo groupdel ollama 2>/dev/null || true
  sudo systemctl daemon-reload 2>/dev/null || true
fi

if [ "$DEEP" = 1 ] && [ ${#APT_PKGS[@]} -gt 0 ]; then
  echo ">> apt-remove ${APT_PKGS[*]}"
  sudo apt-get remove -y "${APT_PKGS[@]}" 2>/dev/null || true
  sudo apt-get autoremove -y 2>/dev/null || true
fi

if [ "$I2C_OFF" = 1 ]; then
  echo ">> disable I2C"
  sudo raspi-config nonint do_i2c 1 2>/dev/null || echo "   (raspi-config missing -- remove dtparam=i2c_arm=on by hand)"
fi

if [ "$CAM_OFF" = 1 ] && [ -n "${CAMCFG:-}" ]; then
  echo ">> remove the IMX519 camera overlay from $CAMCFG (restore auto-detect)"
  sudo sed -i -E '/^[[:space:]]*dtoverlay=imx519.*# \[LocalAiRobot camera\]/d' "$CAMCFG" || true
  sudo sed -i -E 's|^[[:space:]]*camera_auto_detect=0[[:space:]]*# \[LocalAiRobot camera\].*|camera_auto_detect=1|' "$CAMCFG" || true
  sudo rm -f "$CAMCFG.localairobot.bak"
fi

if [ "${PWR_OFF:-0}" = 1 ] && [ -n "${PWRCFG:-}" ]; then
  echo ">> remove usb_max_current_enable from $PWRCFG (restore the 600mA USB default)"
  sudo sed -i -E '/^[[:space:]]*usb_max_current_enable=.*# \[LocalAiRobot power\]/d' "$PWRCFG" || true
fi

if [ "$BASHRC_LINE" = 1 ]; then
  echo ">> remove the robot.env auto-load from ~/.bashrc"
  # Drops the installer's marker comment AND any line sourcing LocalAiRobot/config/robot.env
  # (covers the hand-added one-liner from the docs too).
  sed -i -e '/# LocalAiRobot: auto-load robot.env/d' \
         -e '/LocalAiRobot\/config\/robot\.env/d' "$H/.bashrc"
fi

if [ "$ASOUND_RM" = 1 ]; then
  echo ">> remove ~/.asoundrc (generated by setup_audio.sh -- restores ALSA's default output)"
  rm -f "$H/.asoundrc"
  if [ -f "$H/.asoundrc.bak" ]; then
    mv -f "$H/.asoundrc.bak" "$H/.asoundrc"
    echo "   restored your previous ~/.asoundrc from ~/.asoundrc.bak"
  fi
fi

echo
echo "=== DONE.  Reboot to finish (clears /tmp; applies the I2C change):  sudo reboot ==="

if [ "$REPO_RM" = 1 ]; then
  echo ">> rm -rf $REPO   (the clone -- this script goes with it)"
  cd "$H"; rm -rf "$REPO"
fi
