#!/usr/bin/env bash
# =============================================================================
#   PATCH:   classic PIR motion sensor (HC-SR501 / Adafruit) as the Concierge wake  -- THE DEFAULT
#   RUN IF:  you're on a PIR, or going BACK to one after trying the mmWave radar
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only -- restores the wake timings that survive a chattering sensor
# =============================================================================
# This is the exact opposite of common/6_motion_ld2410c.sh and restores the shipped defaults. Run it if the
# radar didn't suit you -- most likely because it sees through walls, or because Jarvis lives in a room you
# occupy all day (a presence sensor sits permanently HIGH there and stops producing wake edges at all,
# whereas a PIR reporting MOVEMENT keeps working).
#
# WIRING (unchanged -- both sensors present the same 3 wires):
#     PIR              Pi 5
#     ---              ----
#     VCC       ->     5V   (pin 2 or 4)   <- 5V, NOT 3.3V: a PIR on 3.3V typically won't trigger
#     GND       ->     GND  (pin 6, 9, ...)
#     OUT       ->     GPIO 4 (pin 7)      <- 3.3V logic, Pi-safe, no divider needed
#   Full guide: docs/wiring/pir-sensor-wiring.md
#
# WHY THESE NUMBERS ARE HIGHER THAN THE RADAR'S. A PIR next to a warm Pi false-triggers relentlessly -- a
# 19-hour field log recorded 3,786 motion wakes of which 3,741 (98.8%) had nobody in front of the camera, and
# 1,232 of those landed between midnight and 6am in an empty house. These three keys are what make that
# survivable rather than crippling:
#     ROBOT_PIR_HOLD_S                   0.1  re-read the pin after the edge; a spike that already fell was
#                                             electrical noise, not a person.
#     ROBOT_CONCIERGE_SILENT_COOLDOWN_S  4    after a look that found nobody, stop looking for a moment --
#                                             the only thing stopping a chattering sensor running the camera
#                                             flat out all night.
#     ROBOT_CONCIERGE_PROBE_TRIES        2    take a second cheap frame before giving up, so a visitor
#                                             caught mid-stride isn't written off on one unlucky frame.
#
# Re-runnable (idempotent).
#   Usage:  ./scripts/patches/common/6_motion_pir_hcsr501.sh [BCM_PIN]
#           BCM_PIN is optional -- given, it sets ROBOT_PIR_PIN; omitted, whatever you already have is kept.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi
PIN="${1:-}"

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-34s = %s\n' "$key" "$val"
}

echo ">> Restoring the Concierge wake defaults for a classic PIR:"
set_kv ROBOT_PIR_HOLD_S 0.1
set_kv ROBOT_CONCIERGE_SILENT_COOLDOWN_S 4
set_kv ROBOT_CONCIERGE_PROBE_TRIES 2
set_kv ROBOT_CONCIERGE_PROBE_INTERVAL_S 0.15
set_kv ROBOT_CONCIERGE_RECOGNIZE_S 7
if [ -n "$PIN" ]; then
  set_kv ROBOT_PIR_PIN "$PIN"
else
  printf '   %-34s = %s (unchanged -- pass a BCM pin to move it)\n' "ROBOT_PIR_PIN" \
         "$(grep -E '^ROBOT_PIR_PIN=' "$ENV" | head -1 | cut -d= -f2 | awk '{print $1}')"
fi
echo
echo "   Wiring: VCC->5V (NOT 3.3V), GND->GND, OUT->the GPIO above.  docs/wiring/pir-sensor-wiring.md"
echo "   Prove it:   ./scripts/pir_sensor_probe.sh    (wave = MOTION, then SILENT for 2 min in an empty room)"
echo "   Reload:     ./scripts/run_me.sh --reload     (or reboot)"
echo
echo "${GRN}==============================${RST}"
echo "${GRN}  PATCH APPLIED (PIR) !!!${RST}"
echo "${GRN}==============================${RST}"
