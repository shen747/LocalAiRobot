#!/usr/bin/env bash
# shutdown.sh -- stop a DETACHED brain (started by run_me.sh --detach). Does NOT power off the Pi.
# (A foreground run_me.sh? just Ctrl-C. The boot service? use: sudo systemctl stop robot.service.)
# Sends SIGINT first so the brain runs its cleanup (neutralises the ESC + closes the panel/camera),
# then escalates only if it won't stop. The physical kill switch on the LiPo is the real safety net.
set -uo pipefail
DIR="$HOME/.localairobot"; PIDFILE="$DIR/robot.pid"

stop_pid() {
  pid="$1"; i=0
  kill -0 "$pid" 2>/dev/null || return 0
  echo "Stopping robot (PID $pid) with SIGINT (lets it neutralise the motor + close cleanly)..."
  kill -INT "$pid" 2>/dev/null || true
  while [ $i -lt 10 ] && kill -0 "$pid" 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
  kill -0 "$pid" 2>/dev/null || { echo "   stopped cleanly."; return 0; }
  echo "   still running -- a second SIGINT..."
  kill -INT "$pid" 2>/dev/null || true
  i=0; while [ $i -lt 6 ] && kill -0 "$pid" 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
  kill -0 "$pid" 2>/dev/null || { echo "   stopped."; return 0; }
  echo "   force-killing (SIGKILL). The ESC may hold its last pulse -- hit the physical kill switch if it moves."
  kill -9 "$pid" 2>/dev/null || true
}

if [ -f "$PIDFILE" ]; then
  pid="$(cat "$PIDFILE" 2>/dev/null || echo 0)"
  # Only signal the PID if it is still OUR brain -- PIDs get recycled (the PID file survives a
  # reboot), and we must never SIGINT/SIGKILL some unrelated process that inherited the number.
  if kill -0 "$pid" 2>/dev/null && grep -aq "brain/main.py" "/proc/$pid/cmdline" 2>/dev/null; then
    stop_pid "$pid"
  else
    echo "PID file is stale (PID $pid is not the robot brain) -- ignoring it."
  fi
  rm -f "$PIDFILE"
else
  echo "No PID file ($PIDFILE) -- looking for a stray brain process instead..."
fi

# belt-and-suspenders: catch any brain/main.py the PID file missed. SIGINT first, then WAIT --
# a clean shutdown needs a few seconds (control-thread join + ESC neutral + device closes), and
# a premature SIGKILL would abort that cleanup, leaving the ESC on its last pulse.
if pgrep -f "brain/main.py" >/dev/null 2>&1; then
  pkill -INT -f "brain/main.py" 2>/dev/null || true
  i=0
  while [ $i -lt 10 ] && pgrep -f "brain/main.py" >/dev/null 2>&1; do sleep 0.5; i=$((i + 1)); done
  if pgrep -f "brain/main.py" >/dev/null 2>&1; then
    echo "   stray brain still running after 5s -- force-killing (SIGKILL)."
    echo "   The ESC may hold its last pulse: hit the physical kill switch if anything moves."
    pkill -9 -f "brain/main.py" 2>/dev/null || true
  fi
fi
pkill -x arecord 2>/dev/null || true   # a mid-flight 4s command recording, if any
echo "Robot stopped. The Pi is still on (your SSH session is unaffected)."
