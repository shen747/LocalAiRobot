#!/usr/bin/env bash
# scripts/patches/hb_qr_1080_g2.sh -- apply config for a HobbyWing QuicRun WP-1080-G2 brushed ESC in F/R
# ("crawler") mode. Same QuicRun family as the WP-1060 (patches/hb_qr_1060.sh), so it starts from the same
# tested F/R throttle map. It deliberately does NOT touch ROBOT_ESC_CHANNEL: hb_qr_1060.sh hard-codes ch14
# for one specific build (that is INF-07, an unfixed quirk) -- a generic 1080-G2 build leaves the ESC on the
# default ch0. If your ESC signal lead is elsewhere, set it yourself (printed below). Re-runnable.
#
# BENCH-VERIFY these on YOUR ESC (wheels OFF): the neutral/floor/ceil are family defaults, not per-unit
# calibrated. If arming stops or throttle maps wrong, re-sweep them (docs/wiring/pwm-esc-servo-wiring.md).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the Pi." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

set_kv() {   # replace the whole line (adds a [hb_qr_1080_g2] provenance tag), or append it
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}   # [hb_qr_1080_g2]|" "$ENV"
  else
    printf '%s=%s   # [hb_qr_1080_g2]\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-28s = %s\n' "$key" "$val"
}

echo ">> Patching config/robot.env for a HobbyWing QuicRun WP-1080-G2 (F/R direct reverse):"
set_kv ROBOT_ESC_NEUTRAL           0.35
set_kv ROBOT_ESC_FWD_FLOOR         0.45
set_kv ROBOT_ESC_FWD_CEIL          1.00
set_kv ROBOT_ESC_REV_FLOOR         0.20
set_kv ROBOT_ESC_REV_CEIL          0.10
set_kv ROBOT_ESC_REVERSE_DOUBLETAP 0
set_kv ROBOT_THROTTLE_INVERT       0
# NOTE: ROBOT_ESC_CHANNEL is intentionally NOT set (default ch0). On a different channel:
#   sed -i 's|^ROBOT_ESC_CHANNEL=.*|ROBOT_ESC_CHANNEL=<N>|' config/robot.env

echo
echo ">> QuicRun WP-1080-G2 defaults applied. HARDWARE: set the program card / jumper to F/R direct reverse."
echo "   Next:  ESC ON, wheels OFF the ground, then  ./scripts/run_diagnostics.sh --wheels-off  (bench-verify)."
echo
echo "${GRN}===============================${RST}"
echo "${GRN}  PATCH APPLIED (WP-1080-G2) !!!${RST}"
echo "${GRN}===============================${RST}"
