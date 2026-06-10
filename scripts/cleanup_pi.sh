#!/usr/bin/env bash
# cleanup_pi.sh -- undo scripts/install_stack.sh and free the disk it used.
# Three phases: (1) DRY RUN that lists exactly what exists and will be removed/reset,
# (2) asks "Shall I proceed?", (3) only on y/yes does it execute. Re-runnable.
#
#   ./scripts/cleanup_pi.sh            plan, confirm, then remove downloads + Ollama/models + venv + data
#   ./scripts/cleanup_pi.sh --deep     ALSO apt-remove the installer's packages + disable I2C
#   ./scripts/cleanup_pi.sh --repo     ALSO delete the ~/LocalAiRobot clone itself (final step)
#   ./scripts/cleanup_pi.sh -n         DRY RUN only: print the plan and exit (never deletes)
#   ./scripts/cleanup_pi.sh -y         skip the confirmation prompt (for non-interactive use)
set -uo pipefail

H="${HOME}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEEP=0; REPO_RM=0; YES=0; DRYRUN=0
for a in "$@"; do case "$a" in
  --deep)       DEEP=1 ;;
  --repo)       REPO_RM=1 ;;
  -y|--yes)     YES=1 ;;
  -n|--dry-run) DRYRUN=1 ;;
  *) echo "unknown option: $a (use --deep, --repo, -n, -y)"; exit 2 ;;
esac; done

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

# ---------- phase 1: DRY RUN -- detect real state, build the plan ----------
PLAN=()        # human-readable lines shown to the user
RM_PATHS=()    # files/dirs that exist and will be deleted
APT_PKGS=()    # installer packages currently installed (only with --deep)
SVC=0; OLLAMA=0; I2C_OFF=0
sz() { du -sh "$1" 2>/dev/null | cut -f1; }   # best-effort size; '' if unreadable

for p in "$H/whisper.cpp" "$H/piper" "$H/turing-smart-screen-python" \
         "$REPO/brain/.venv" "$H/.localairobot" /tmp/test.wav /tmp/test.jpg; do
  if [ -e "$p" ] || [ -L "$p" ]; then
    RM_PATHS+=("$p"); s="$(sz "$p")"; PLAN+=("delete   $p${s:+   ($s)}")
  fi
done

if [ -f /etc/systemd/system/robot.service ]; then
  SVC=1; PLAN+=("reset    stop + disable + remove  /etc/systemd/system/robot.service")
fi

if command -v ollama >/dev/null 2>&1 || [ -e /usr/local/bin/ollama ]; then
  OLLAMA=1
  PLAN+=("remove   Ollama: service + binary + 'ollama' user + models (/usr/share/ollama, ~/.ollama, ~3 GB)")
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
  sudo systemctl daemon-reload 2>/dev/null || true
fi

if [ "$OLLAMA" = 1 ]; then
  echo ">> remove Ollama"
  sudo systemctl disable --now ollama 2>/dev/null || true
  sudo rm -f /etc/systemd/system/ollama.service
  OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
  [ -n "$OLLAMA_BIN" ] && sudo rm -f "$OLLAMA_BIN"
  sudo rm -f /usr/local/bin/ollama
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

echo
echo "=== DONE.  Reboot to finish (clears /tmp; applies the I2C change):  sudo reboot ==="

if [ "$REPO_RM" = 1 ]; then
  echo ">> rm -rf $REPO   (the clone -- this script goes with it)"
  cd "$H"; rm -rf "$REPO"
fi
