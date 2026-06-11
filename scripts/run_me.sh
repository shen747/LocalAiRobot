#!/usr/bin/env bash
# run_me.sh -- the single launcher for the robot brain.
#
# MODE comes from ROBOT_DRY_RUN in config/robot.env (override per run with --dry / --live):
#   --dry  / ROBOT_DRY_RUN=1 -> FOREGROUND, interactive, nothing drives (logs what it WOULD do).
#   --live / ROBOT_DRY_RUN=0 -> FOREGROUND, interactive, REAL hardware.
# Both are interactive: ENTER = talk, 'r' = simulate the "Richard" wake, q = quit, Ctrl-C = stop
# (Ctrl-C neutralises the motor on the way out). Foreground is what you want for bench + calibration.
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

# --reload: restart whatever is running so it re-reads the (just-edited) config/robot.env.
if [ "$reload" = 1 ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service; then
    echo ">> Reloading via systemd (restart re-reads config/robot.env)..."
    sudo systemctl restart robot.service
    echo "   restarted. Watch:  journalctl -u robot.service -f"
    exit 0
  fi
  echo ">> Reloading: stopping the running brain, then relaunching it detached with the new config..."
  "$REPO/scripts/shutdown.sh" || true
  override=0; detach=1
fi

DRY="${override:-${ROBOT_DRY_RUN:-0}}"
PY="$REPO/brain/.venv/bin/python"
MAIN="$REPO/brain/main.py"

# DRY RUN -> foreground, interactive.
if [ "$DRY" = 1 ]; then
  echo ">> DRY RUN -- no hardware will be driven.   ENTER=talk · 'r'=simulate \"Richard\" · q=quit"
  exec env ROBOT_DRY_RUN=1 "$PY" "$MAIN"
fi

# LIVE, foreground (default) -> attached + interactive; Ctrl-C neutralises the motor on the way out.
if [ "$detach" = 0 ]; then
  echo ">> LIVE (foreground) -- REAL hardware.  ENTER=talk · 'r'=wake · q=quit · Ctrl-C=stop."
  exec env ROBOT_DRY_RUN=0 "$PY" "$MAIN"
fi

# LIVE, detached (--detach) -> background, survives SSH logout. Stop with scripts/shutdown.sh.
DIR="$HOME/.localairobot"; mkdir -p "$DIR"
PIDFILE="$DIR/robot.pid"; LOG="$DIR/robot.log"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "Robot is already running (PID $(cat "$PIDFILE")). Stop it first:  ./scripts/shutdown.sh"
  exit 1
fi
echo ">> Bringing the robot to LIFE, detached (survives SSH logout). Logs -> $LOG"
echo "   Say \"Richard\" to wake it.  Stop with ./scripts/shutdown.sh (does NOT power off the Pi)."
ROBOT_DRY_RUN=0 nohup "$PY" "$MAIN" >"$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 1
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "   started (PID $(cat "$PIDFILE")).  Watch it:  tail -f $LOG"
else
  echo "   !! it exited immediately -- check the log:  tail -n 40 $LOG"
  rm -f "$PIDFILE"; exit 1
fi
