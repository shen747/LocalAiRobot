#!/usr/bin/env bash
# shutdown.sh -- stop the robot brain CLEANLY, however it was launched (a --detach run, a foreground
# orphan, the boot service, or a stray). SIGINT first so the brain neutralises the ESC + closes the
# panel/camera; escalate to SIGKILL only if it won't go. By default the Pi STAYS ON; add --poweroff to
# also halt it (your SSH session drops with it). The physical kill switch on the LiPo is the real safety net.
#   (Attached to a foreground run_me.sh? plain Ctrl-C is enough -- and a 2nd Ctrl-C is now safe too.)
set -uo pipefail

# Match ONLY the brain's python process -- never an editor/grep that merely mentions the path
# (e.g. `nano brain/main.py`). We trust the cmdline, not a PID number (those get recycled).
PAT='python.*brain/main\.py'
PIDFILE="$HOME/.localairobot/robot.pid"

POWEROFF=0
case "${1:-}" in
  --poweroff|-p) POWEROFF=1 ;;
  "")            ;;
  *) echo "usage: $(basename "$0") [--poweroff]   (--poweroff: stop the brain, then halt the Pi)"; exit 2 ;;
esac

running()   { pgrep -f "$PAT" >/dev/null 2>&1; }
wait_exit() { local n=$1; while [ "$n" -gt 0 ] && running; do sleep 0.5; n=$((n - 1)); done; }  # waits N/2 s

rm -f "$PIDFILE"   # stale detached-run marker, if any -- the cmdline match is what we actually trust

# If the boot service owns the brain, stop it the systemd way FIRST -- otherwise systemd just
# relaunches whatever we kill below (Restart=on-failure).
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service 2>/dev/null; then
  echo "robot.service is active -- stopping it via systemd (it would relaunch a killed brain)..."
  sudo systemctl stop robot.service 2>/dev/null || true
  wait_exit 10
fi

if ! running; then
  echo "No robot brain running."
else
  echo "Stopping the robot brain with SIGINT (it neutralises the ESC + closes devices)..."
  pkill -INT -f "$PAT" 2>/dev/null || true
  wait_exit 10                                    # a clean exit needs a few seconds
  if running; then
    echo "   didn't stop in 5s -- force-killing (SIGKILL). The ESC may hold its last pulse:"
    echo "   hit the physical kill switch if anything moves."
    pkill -KILL -f "$PAT" 2>/dev/null || true
    wait_exit 4
  fi
  running && echo "   WARNING: a brain process is STILL alive." || echo "   stopped."
fi

pkill -x arecord 2>/dev/null || true   # a mid-flight 4s command recording, if any
echo "Robot stopped."

if [ "$POWEROFF" = 1 ]; then
  echo "Powering off the Pi -- this SSH session will disconnect. Bye."
  sudo poweroff
else
  echo "The Pi is still on (your SSH session is unaffected)."
fi
