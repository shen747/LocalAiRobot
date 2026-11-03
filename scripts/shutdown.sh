#!/usr/bin/env bash
# shutdown.sh -- stop the robot brain CLEANLY, however it was launched (a --detach run, a foreground
# orphan, the boot service, or a stray). SIGINT first so the brain neutralises the ESC + closes the
# panel/camera; escalate to SIGKILL only if it won't go. By default the Pi STAYS ON; add --poweroff to
# also halt it (your SSH session drops with it). The physical kill switch on the LiPo is the real safety net.
#   (Attached to a foreground run_me.sh? plain Ctrl-C is enough -- and a 2nd Ctrl-C is now safe too.)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

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
  # Resume first: a SUSPENDED brain (you pressed Ctrl-Z) can't act on SIGINT, so it would never run its
  # clean stop and we'd fall through to SIGKILL -- leaving the PCA9685 holding its last pulse. SIGCONT
  # wakes it so the SIGINT below gets handled (neutralise ESC + close devices).
  pkill -CONT -f "$PAT" 2>/dev/null || true
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

# --- Release the GPIO too, not just the brain --------------------------------------------------------
# run_diagnostics.sh (brain/diagnostics.py) and the probe scripts grab the SAME ultrasonic pins as the
# brain. A diagnostics run that was Ctrl-C'd / suspended (Ctrl-Z) / left at a prompt stays ALIVE holding
# those pins, so the next run fails with 'GPIO busy' even though no brain is running -- and the main.py
# pattern above never matched it. Clear those stragglers so the GPIO is actually free.
if pgrep -f 'python.*brain/diagnostics\.py' >/dev/null 2>&1; then
  echo "Releasing GPIO: stopping a lingering diagnostics run (it still holds the ultrasonic pins)..."
  pkill -CONT -f 'python.*brain/diagnostics\.py' 2>/dev/null || true   # wake a Ctrl-Z'd one so it can die
  pkill -INT  -f 'python.*brain/diagnostics\.py' 2>/dev/null || true
  sleep 1
  pkill -KILL -f 'python.*brain/diagnostics\.py' 2>/dev/null || true
fi
# Catch-all: anything OF OURS (a stray probe, a wedged python) still holding a GPIO character device.
# Non-sudo fuser sees your own processes (the brain runs as you), so there's no password prompt.
if command -v fuser >/dev/null 2>&1; then
  for pid in $(fuser /dev/gpiochip* 2>/dev/null | tr -s ' ' '\n'); do
    [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || continue
    if tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "$REPO"; then
      echo "   GPIO still held by PID $pid -- releasing it."
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
fi

pkill -x arecord 2>/dev/null || true   # a mid-flight 4s command recording, if any
echo "Robot stopped."

if [ "$POWEROFF" = 1 ]; then
  echo "Powering off the Pi -- this SSH session will disconnect. Bye."
  sudo poweroff
else
  echo "The Pi is still on (your SSH session is unaffected)."
fi
