#!/usr/bin/env bash
# =============================================================================
#   PATCH:   ULIIK 60A brushed crawler ESC (F/R via its 5-slot program card)
#   RUN IF:  your drive ESC is the ULIIK 60A crawler ESC (540, 11/13/16/20T)
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    writes the bench-swept ULIIK throttle map (offset neutral + dead-band)
# =============================================================================
# HARDWARE: set the program card to Forward/Reverse ("crawler") DIRECT reverse.  Re-runnable (idempotent).
# !! VOLTAGE: this ESC is listed 7.4V / 2S. CONFIRM its max cell count accepts YOUR pack before connecting --
#    a 2S-only ESC on a 3S (11.1V) battery can be destroyed.
# It does NOT set ROBOT_ESC_CHANNEL: that's your WIRING, not the ESC model -- keep your channel (e.g. 14).
#
# WHY these values (bench-swept on this unit; they DRIFT + differ per ESC -- re-sweep if arming/creep changes):
#   ROBOT_ESC_NEUTRAL=0.40    OFFSET neutral (1700us): holding it arms + stops (cell tones -> a long "ready" beep).
#                             (0.25/1625us is BELOW it -- the ESC won't arm or stop there; it armed when the
#                             sweep climbed up to 0.40.)
#   ROBOT_ESC_FWD_FLOOR=0.55  forward has a dead-band -- it won't pull until ~1775us (0.55); below that = nothing.
#   ROBOT_ESC_FWD_CEIL=0.90   capped top speed (1950us), indoor-friendly. speed 1..100 maps 0.55..0.90.
#   ROBOT_ESC_REV_FLOOR=0.25  reverse crawl floor (1625us). The bare creep (~0.35/1675us) is too close to
#                             neutral to move under load, so the floor sits a bit deeper. speed 1..100 maps 0.25..0.00.
#   ROBOT_ESC_REV_CEIL=0.00   reverse top speed (1500us); deeper below neutral = faster (go negative for more).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

set_kv() {   # replace the whole line (adds a [uliik_60a] provenance tag), or append it
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}   # [uliik_60a]|" "$ENV"
  else
    printf '%s=%s   # [uliik_60a]\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-28s = %s\n' "$key" "$val"
}

echo ">> Patching config/robot.env for a ULIIK 60A crawler ESC (F/R direct reverse):"
set_kv ROBOT_ESC_NEUTRAL           0.40
set_kv ROBOT_ESC_FWD_FLOOR         0.55
set_kv ROBOT_ESC_FWD_CEIL          0.90
set_kv ROBOT_ESC_REV_FLOOR         0.25
set_kv ROBOT_ESC_REV_CEIL          0.00
set_kv ROBOT_ESC_REVERSE_DOUBLETAP 0
set_kv ROBOT_THROTTLE_INVERT       0

echo
echo ">> ULIIK 60A throttle map applied."
echo "   HARDWARE: program card -> Forward/Reverse (direct reverse)."
echo "   VOLTAGE:  confirm this ESC accepts your pack (listed 7.4V/2S) BEFORE connecting a 3S battery."
echo "   CHANNEL:  NOT changed -- keep your wiring's channel (e.g. 14), or set it:"
echo "               sed -i 's|^ROBOT_ESC_CHANNEL=.*|ROBOT_ESC_CHANNEL=<N>|' config/robot.env"
echo "   DRIFT:    if arming stops or the crawl point moves, re-sweep (docs/wiring/pwm-esc-servo-wiring.md)."
echo "   Next:     ESC ON, wheels OFF the ground, then  ./scripts/run_diagnostics.sh --wheels-off"
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED (ULIIK 60A) !!!${RST}"
echo "${GRN}===============================${RST}"
