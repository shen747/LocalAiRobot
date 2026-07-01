#!/usr/bin/env bash
# run_me.sh -- the single launcher for the robot brain.
#
# MODE comes from ROBOT_DRY_RUN in config/robot.env (override per run with --dry / --live):
#   --dry  / ROBOT_DRY_RUN=1 -> FOREGROUND, interactive, nothing drives (logs what it WOULD do).
#   --live / ROBOT_DRY_RUN=0 -> FOREGROUND, interactive, REAL hardware.
# Both run in the foreground: q = quit, Ctrl-C = stop (neutralises the motor on the way out).
# --live triggers on the WAKE WORD only; --dry adds ENTER = talk and 'r' = simulate a wake (no mic
# needed on the bench). Foreground is what you want for bench + calibration.
#
# Other actions:
#   --detach        run LIVE in the BACKGROUND (survives SSH logout). Stop with scripts/shutdown.sh.
#   --reload        re-apply an edited config/robot.env to a RUNNING robot (restarts the boot service
#                   if it's installed, else the --detach instance).
#   --install-boot  install + enable the systemd unit so the brain auto-starts on every boot.
#   --disable-boot  stop + remove that boot service.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load the config if present; SILENTLY no-op if config/robot.env is missing (never error on that).
if [ -f config/robot.env ]; then
  set -a; . config/robot.env; set +a
fi

# --- boot-service helpers (systemd autostart; kept in here so there's no extra script file) ---
install_boot() {
  local user="$(id -un)" src="$REPO/scripts/robot.service" unit="/etc/systemd/system/robot.service"
  [ -f "$src" ] || { echo "missing template: $src" >&2; exit 1; }
  if [ "${ROBOT_DRY_RUN:-0}" = 1 ]; then
    echo "!! config/robot.env has ROBOT_DRY_RUN=1 -- the boot service would run in DRY RUN (no driving)."
    echo "   Set ROBOT_DRY_RUN=0 before you rely on autostart."
  fi
  echo ">> Installing the boot service (the brain will auto-start on every boot, headless)."
  # Fill the template in with the REAL repo path + username, then drop it in place.
  sed -e "s#/home/YOUR_USER/LocalAiRobot#${REPO}#g" -e "s/YOUR_USER/${user}/g" "$src" \
    | sudo tee "$unit" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable robot.service
  echo "   enabled -- starts on the NEXT boot (or now: sudo systemctl start robot.service)."
  echo "   watch:  journalctl -u robot.service -f       remove:  ./scripts/run_me.sh --disable-boot"
}
disable_boot() {
  echo ">> Disabling the boot service (the brain no longer auto-starts)."
  sudo systemctl disable --now robot.service 2>/dev/null || true
  sudo rm -f /etc/systemd/system/robot.service
  sudo systemctl daemon-reload 2>/dev/null || true
  echo "   removed. Start it by hand anytime with:  ./scripts/run_me.sh --live"
}

override=""; detach=0; reload=0
case "${1:-}" in
  --dry|-d)        override=1 ;;
  --live|-l)       override=0 ;;
  --detach|-D)     override=0; detach=1 ;;
  --reload)        reload=1 ;;
  --install-boot)  install_boot; exit 0 ;;
  --disable-boot)  disable_boot; exit 0 ;;
  "")              ;;
  *) echo "usage: $(basename "$0") [--dry|--live|--detach|--reload|--install-boot|--disable-boot]"; exit 2 ;;
esac

# A robot.pid is only trusted if its PID is still OUR brain: PIDs get recycled (especially after a
# reboot, since ~/.localairobot/robot.pid survives one), and `kill -0` alone can't tell a stale
# entry from a live brain.
pid_is_brain() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && \
    grep -aq "brain/main.py" "/proc/$pid/cmdline" 2>/dev/null
}

# --reload: restart whatever is RUNNING so it re-reads the (just-edited) config/robot.env.
# Nothing running -> refuse (a "reload" must never cold-start a live robot by surprise).
if [ "$reload" = 1 ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service; then
    echo ">> Reloading via systemd (restart re-reads config/robot.env)..."
    sudo systemctl restart robot.service
    echo "   restarted. Watch:  journalctl -u robot.service -f"
    exit 0
  fi
  if pid_is_brain "$(cat "$HOME/.localairobot/robot.pid" 2>/dev/null)"; then
    echo ">> Reloading: stopping the detached brain, then relaunching it with the new config..."
    "$REPO/scripts/shutdown.sh" || true
    override=0; detach=1
  else
    echo "Nothing to reload: no boot service active and no detached brain running."
    echo "Start one explicitly:  ./scripts/run_me.sh --live   (or --detach)"
    exit 1
  fi
fi

DRY="${override:-${ROBOT_DRY_RUN:-0}}"
PY="$REPO/brain/.venv/bin/python"
MAIN="$REPO/brain/main.py"

# DRY RUN -> foreground, interactive.
if [ "$DRY" = 1 ]; then
  echo ">> DRY RUN -- no hardware will be driven.   ENTER=talk · 'r'=simulate wake · q=quit"
  exec env ROBOT_DRY_RUN=1 "$PY" "$MAIN"
fi

# LIVE, foreground (default) -> attached + interactive; Ctrl-C neutralises the motor on the way out.
if [ "$detach" = 0 ]; then
  echo ">> LIVE (foreground) -- REAL hardware.  Say the wake word to talk · q=quit · Ctrl-C=stop."
  echo "   Motor silent? The brain holds NEUTRAL at startup -- have the ESC powered ON while it holds"
  echo "   neutral (or power-cycle it now); wait for its long 'ready' beep to confirm it armed."
  exec env ROBOT_DRY_RUN=0 "$PY" "$MAIN"
fi

# LIVE, detached (--detach) -> background, survives SSH logout. Stop with scripts/shutdown.sh.
DIR="$HOME/.localairobot"; mkdir -p "$DIR"
PIDFILE="$DIR/robot.pid"; LOG="$DIR/robot.log"
if pid_is_brain "$(cat "$PIDFILE" 2>/dev/null)"; then
  echo "Robot is already running (PID $(cat "$PIDFILE")). Stop it first:  ./scripts/shutdown.sh"
  exit 1
fi
rm -f "$PIDFILE"   # stale leftover (e.g. from before a reboot) -- safe to clear
echo ">> Bringing the robot to LIFE, detached (survives SSH logout). Logs -> $LOG"
echo "   Say the wake word to wake it.  Stop with ./scripts/shutdown.sh (does NOT power off the Pi)."
echo "   (Needs a working wake word -- detached has no keyboard, so with no wake model it just exits.)"
# stdin MUST be /dev/null: left attached to the SSH terminal it would hit EOF on logout and the
# brain would misread that as endless ENTER presses (a hot record/transcribe loop on ambient noise).
ROBOT_DRY_RUN=0 nohup "$PY" "$MAIN" </dev/null >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 1
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "   started (PID $(cat "$PIDFILE")).  Watch it:  tail -f $LOG"
else
  echo "   !! it exited immediately -- check the log:  tail -n 40 $LOG"
  rm -f "$PIDFILE"; exit 1
fi
