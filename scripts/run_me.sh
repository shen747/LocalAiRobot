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
#   --verbose / -v  diagnosis run: live SENSORS front/left/right read-out (even while idle), every
#                   no-echo shown (not collapsed), and DEBUG log level. In CONCIERGE MODE the ultrasonic
#                   read-out + ESC/PWM are dropped (it never drives) and PIR triggers are logged instead.
#                   Combine with any mode (e.g. --live --verbose). Off by default (a little extra load).
#   --detach        run LIVE in the BACKGROUND (survives SSH logout). Stop with scripts/shutdown.sh.
#   --reload        re-apply an edited config/robot.env to a RUNNING robot (restarts the boot service
#                   if it's installed, else the --detach instance).
#   --install-boot  install + enable the systemd unit so the brain auto-starts on every boot.
#   --disable-boot  stop + remove that boot service.
#
# ON LAUNCH, run_me.sh first CLEANLY STOPS any brain already running (a prior foreground orphan, a
# --detach instance, or the boot service) via scripts/shutdown.sh -- so a fresh run always gets the GPIO
# + mic + ESC to itself (no "GPIO busy" / "no input device" clashes from a leftover process). The stop is
# graceful (SIGINT neutralises the ESC + releases devices first); it's an instant no-op when nothing runs.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load the config if present; SILENTLY no-op if config/robot.env is missing (never error on that).
if [ -f config/robot.env ]; then
  set -a; . config/robot.env; set +a
fi

# GPIO backend (gpiozero pin factory) is chosen by the board HAL now, not hard-coded here: brain/boards/pi5.py
# reports GPIOZERO_PIN_FACTORY=lgpio for the Pi 5 (pigpio can't reach the Pi 5's RP1 I/O controller), and
# brain/main.py / hal.current() apply it via os.environ.setdefault BEFORE any device is built. An explicit
# override in your environment or config/robot.env still wins. See brain/hal.py (the board HAL).
# (This does NOT remove the software-timing jitter -- ROBOT_OBSTACLE_DEBOUNCE handles that.)

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
  # Step 29: this script runs WITHOUT `set -e` (deliberate for the launcher paths), so each step must fail
  # LOUDLY -- else a failed sudo/systemctl (expired sudo timestamp in an unattended --bootstrap, read-only
  # /etc) is silently ignored, the caller sees exit 0, and the robot stays dead after the first power cycle.
  if ! sed -e "s#/home/YOUR_USER/LocalAiRobot#${REPO}#g" -e "s/YOUR_USER/${user}/g" "$src" \
        | sudo tee "$unit" >/dev/null; then
    echo "!! could not write $unit (sudo?)" >&2; exit 1
  fi
  sudo systemctl daemon-reload || { echo "!! systemctl daemon-reload failed" >&2; exit 1; }
  sudo systemctl enable robot.service || { echo "!! systemctl enable robot.service failed" >&2; exit 1; }
  echo "   enabled -- starts on the NEXT boot (or now: sudo systemctl start robot.service)."
  echo "   Survives power off/on: auto-starts every boot + auto-restarts after a crash/brown-out (Restart=always)."
  # Persistent journal: the boot service is HEADLESS, so its journal is the ONLY record of why it crashed or
  # brown-out-rebooted -- but Raspberry Pi OS defaults to RAM-only (wiped every reboot). Enable it HERE too (not
  # only in install_stack, which a long-installed robot may predate), via the shared helper that sets an explicit
  # Storage=persistent drop-in + verifies it. Honours ROBOT_PERSIST_JOURNAL=0 to opt out.
  bash "$REPO/scripts/enable_persistent_journal.sh" || true
  # Detect-capture purge: a boot-service robot in Concierge Mode runs 24/7 and keeps the frame behind every
  # greeting in ~/detect-captures (brain/captures.py). Schedule the daily cleanup HERE too (not only in
  # install_stack, which a long-installed robot may predate) so autostart never means unbounded disk growth.
  # Idempotent + tagged; honours ROBOT_DETECT_CAPTURE_CRON=0. Best-effort -- never blocks enabling autostart.
  bash "$REPO/scripts/install_detect_capture_cron.sh" || true
  echo "   watch:  journalctl -u robot.service -f       remove:  ./scripts/run_me.sh --disable-boot"
}
disable_boot() {
  echo ">> Disabling the boot service (the brain no longer auto-starts)."
  sudo systemctl disable --now robot.service 2>/dev/null || true
  sudo rm -f /etc/systemd/system/robot.service
  sudo systemctl daemon-reload 2>/dev/null || true
  echo "   removed. Start it by hand anytime with:  ./scripts/run_me.sh --live"
}

override=""; detach=0; reload=0; verbose=0
# Flags can be combined (e.g. `--live --verbose`), so parse them all, not just $1.
for arg in "$@"; do
  case "$arg" in
    --dry|-d)        override=1 ;;
    --live|-l)       override=0 ;;
    --detach|-D)     override=0; detach=1 ;;
    --reload)        reload=1 ;;
    --verbose|-v)    verbose=1 ;;
    --install-boot)  install_boot; exit 0 ;;
    --disable-boot)  disable_boot; exit 0 ;;
    "")              ;;
    *) echo "usage: $(basename "$0") [--dry|--live|--detach|--reload|--verbose|--install-boot|--disable-boot]"; exit 2 ;;
  esac
done

# --verbose: turn on the live SENSORS read-out + un-collapse the no-echo warnings (driver reads
# ROBOT_SENSOR_VERBOSE) and raise the log level to DEBUG. Exported here so it flows through the exec
# below to the brain. Respects an existing ROBOT_LOG_LEVEL. Safe on a 512GB NVMe (verbose = more log I/O).
if [ "$verbose" = 1 ]; then
  export ROBOT_SENSOR_VERBOSE=1
  export ROBOT_LOG_LEVEL="${ROBOT_LOG_LEVEL:-DEBUG}"
  if [ "${ROBOT_CONCIERGE_MODE:-0}" = 1 ]; then
    echo ">> VERBOSE on (Concierge): PIR motion triggers logged + DEBUG logs (ultrasonics + ESC/PWM unused, so not shown)."
  else
    echo ">> VERBOSE on: live SENSORS front/left/right read-out + every no-echo shown + DEBUG logs."
  fi
fi

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

# Clear the field before we launch: cleanly stop ANY brain already running (a prior foreground orphan, a
# --detach, the boot service, or a stray) so THIS run gets the GPIO + mic + ESC to itself. Without it, a
# leftover instance keeps the pins/mic and the new one dies with "GPIO busy" / "no input device". Reuses
# scripts/shutdown.sh (SIGINT -> neutralise the ESC + release devices; SIGKILL only if it won't go). Runs
# BEFORE the exec/launch below, so it can only ever stop PREVIOUS brains, never the one we're starting.
# Skipped for --reload (it already ran shutdown.sh above); an instant no-op when nothing is running.
if [ "$reload" != 1 ] && { pgrep -f 'python.*brain/main\.py' >/dev/null 2>&1 \
   || { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service 2>/dev/null; }; }; then
  echo ">> A robot brain is already running -- stopping it first (frees the GPIO + mic + ESC)..."
  "$REPO/scripts/shutdown.sh" || true
fi

# Re-apply the USB-speaker default + volume before launching AND refresh the auto-detected device NAMES: USB
# audio volume resets to 0/low across a reboot, and -- critically -- ALSA USB card ids are enumeration-ORDER
# dependent, so across a power cycle the speaker/mic ids can SWAP and silently break the explicit
# ROBOT_SOUNDS_DEVICE / ROBOT_MIC the brain uses (aplay/arecord 'No such file or directory'). A FULL
# setup_audio re-detects + rewrites those names to THIS boot's enumeration; we then re-source so the brain we
# exec below inherits them, re-applying the per-run --verbose overrides the re-source would otherwise reset.
bash "$REPO/scripts/setup_audio.sh" || true
if [ -f config/robot.env ]; then set -a; . config/robot.env; set +a; fi
if [ "$verbose" = 1 ]; then export ROBOT_SENSOR_VERBOSE=1; export ROBOT_LOG_LEVEL="${ROBOT_LOG_LEVEL:-DEBUG}"; fi

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
