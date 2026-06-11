#!/usr/bin/env bash
# run_me.sh -- the single launcher for the robot brain.
#
# The MODE comes from ROBOT_DRY_RUN in config/robot.env (override per run with --dry / --live):
#   DRY RUN (ROBOT_DRY_RUN=1) -> runs in the FOREGROUND, interactive: nothing drives, it just logs
#       what it WOULD do. ENTER = talk, 'r' = simulate the "Richard" wake, q = quit.
#   LIVE    (ROBOT_DRY_RUN=0) -> runs DETACHED in the background (survives SSH logout) and listens for
#       "Richard". Stop it with scripts/shutdown.sh; watch it with: tail -f ~/.localairobot/robot.log
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load the config if it's there; SILENTLY no-op if config/robot.env is missing (e.g. a half-removed
# clone) -- never error out just because the file isn't present.
if [ -f config/robot.env ]; then
  set -a; . config/robot.env; set +a
fi

# Mode: --dry / --live override the config; otherwise ROBOT_DRY_RUN (default 0 = live).
override=""
case "${1:-}" in
  --dry|-d)  override=1 ;;
  --live|-l) override=0 ;;
  "")        ;;
  *) echo "usage: $(basename "$0") [--dry | --live]"; exit 2 ;;
esac
DRY="${override:-${ROBOT_DRY_RUN:-0}}"

PY="$REPO/brain/.venv/bin/python"
MAIN="$REPO/brain/main.py"

if [ "$DRY" = 1 ]; then
  echo ">> DRY RUN -- no hardware will be driven.   ENTER=talk  ·  'r'=simulate \"Richard\"  ·  q=quit"
  exec env ROBOT_DRY_RUN=1 "$PY" "$MAIN"
fi

# ---------- LIVE: detached, survives SSH logout ----------
DIR="$HOME/.localairobot"; mkdir -p "$DIR"
PIDFILE="$DIR/robot.pid"; LOG="$DIR/robot.log"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "Robot is already running (PID $(cat "$PIDFILE")). Stop it first:  ./scripts/shutdown.sh"
  exit 1
fi
echo ">> Bringing the robot to LIFE (live hardware). Logs -> $LOG"
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
