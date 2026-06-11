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
  stop_pid "$(cat "$PIDFILE" 2>/dev/null || echo 0)"
  rm -f "$PIDFILE"
else
  echo "No PID file ($PIDFILE) -- looking for a stray brain process instead..."
fi

# belt-and-suspenders: catch any brain/main.py the PID file missed, plus a mid-flight recording.
pkill -INT -f "brain/main.py" 2>/dev/null || true
sleep 0.5
pkill -9 -f "brain/main.py" 2>/dev/null || true
pkill -x arecord 2>/dev/null || true
echo "Robot stopped. The Pi is still on (your SSH session is unaffected)."
