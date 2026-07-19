#!/usr/bin/env bash
# =============================================================================
#   PATCH:   HobbyWing QuicRun WP-1060 brushed ESC (F/R "crawler" mode)
#   RUN IF:  your drive ESC is a QuicRun WP-1060 (this build's ESC)
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    writes the bench-tested ESC throttle map + ROBOT_ESC_CHANNEL=14
# =============================================================================
# HARDWARE: Mode jumper cap OFF entirely (= F/R direct reverse); LIPO cap ON.  Re-runnable (idempotent).
#
# WHY these values (all bench-tested -- see BENCH_SCRIPTS.md / docs/wiring/pwm-esc-servo-wiring.md):
#   ROBOT_ESC_NEUTRAL=0.25       this ESC's neutral is OFFSET (~1625us, not 1500us). Holding it arms + STOPS
#                                (cell tones + a long "ready" beep). It DRIFTS -- re-sweep if arming stops.
#   ROBOT_MIN_FORWARD_SPEED=0.55 forward has a dead-band -- it won't PULL until ~1775us (0.55); below that =
#                                nothing (bench: 0.55 crawls, 0.45 does nothing). speed 1..100 maps 0.55..0.95.
#   ROBOT_MAX_FORWARD_SPEED=0.95 capped top speed (~1975us), indoor-friendly.
#   ROBOT_MIN_REVERSE_SPEED=-0.01 slowest reverse that engages. Reverse kicks in JUST below the 1500us center:
#                                0.00 (1500us) sits ON the boundary and does NOT move (field-confirmed: it just
#                                sat still), but -0.01 (1495us) already creeps back (bench-confirmed). The offset
#                                0.25 neutral (1625us) + the forward dead-band leave a dead zone ~1500-1775us, so
#                                the floor MUST be a hair past 0.00. (Neutral drifts -- nudge to -0.03 for margin.)
#   ROBOT_MAX_REVERSE_SPEED=-0.20 fastest reverse (more negative = faster); -0.20 = 1400us. Bench-sweep to taste.
#   ROBOT_ESC_REVERSE_DOUBLETAP=0   F/R jumper = direct reverse (no F/B/R "double-click").
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
# green success banner (only when writing to a real terminal)
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

set_kv() {   # set_kv KEY VALUE -> replace the whole line (adds a [hb_qr_1060] provenance tag), or append it
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}   # [hb_qr_1060]|" "$ENV"
  else
    printf '%s=%s   # [hb_qr_1060]\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-28s = %s\n' "$key" "$val"
}

echo ">> Patching config/robot.env for a HobbyWing QuicRun WP-1060 (F/R direct reverse):"
set_kv ROBOT_ESC_NEUTRAL           0.25
set_kv ROBOT_MIN_FORWARD_SPEED     0.55
set_kv ROBOT_MAX_FORWARD_SPEED     0.95
set_kv ROBOT_MIN_REVERSE_SPEED     -0.01
set_kv ROBOT_MAX_REVERSE_SPEED     -0.20
set_kv ROBOT_ESC_REVERSE_DOUBLETAP 0
set_kv ROBOT_THROTTLE_INVERT       0
set_kv ROBOT_ESC_CHANNEL           14

echo
echo ">> QuicRun WP-1060 defaults applied to config/robot.env."
echo "   HARDWARE: pull the ESC's Mode jumper cap OFF entirely (= F/R direct reverse); leave the LIPO cap ON."
echo "   CHANNEL:  set ROBOT_ESC_CHANNEL=14 -- this build's ESC signal lead is on PCA9685 ch14."
echo "             On a DIFFERENT channel? change it with:"
echo "               sed -i 's|^ROBOT_ESC_CHANNEL=.*|ROBOT_ESC_CHANNEL=<N>|' config/robot.env"
echo "   Next:     ESC ON, wheels OFF the ground, then  ./scripts/run_diagnostics.sh --wheels-off"
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED !!!${RST}"
echo "${GRN}===============================${RST}"
