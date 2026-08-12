#!/usr/bin/env bash
# =============================================================================
#   PATCH:   classic hobby PIR (HC-SR501 / Adafruit) as the Concierge wake  -- THE DEFAULT
#   RUN IF:  your wake sensor is a cheap PIR module with two potentiometers
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only -- the shipped wake timings that survive a chattering sensor
# =============================================================================
# The cheap option, and the shipped default. A few pounds, works out of the box, and the settings below are
# tuned around the fact that it false-triggers a lot next to a warm Pi.
#
# WANT BETTER DETECTION? scripts/patches/common/6_motion_pir_papirs.sh configures a Panasonic PaPIRs --
# same backend, same three wires, no code change, but 88 detection zones instead of roughly a dozen, which
# is what actually decides whether someone walking STRAIGHT AT it gets seen. It runs on 3.3V, NOT 5V.
#
# WIRING:
#     PIR              Pi 5
#     ---              ----
#     VCC       ->     5V   (pin 2 or 4)   <- 5V, NOT 3.3V: a PIR on 3.3V typically won't trigger
#     GND       ->     GND  (pin 6, 9, ...)
#     OUT       ->     GPIO 4 (pin 7)      <- 3.3V logic, Pi-safe, no divider needed
#   Full guide: docs/wiring/pir-sensor-wiring.md
#   ! The 5V above is specific to THIS module (it has an on-board regulator). A PaPIRs on 5V is destroyed.
#
# WHY THESE DEFENCES EXIST. A PIR next to a warm Pi false-triggers relentlessly -- a
# 19-hour field log recorded 3,786 motion wakes of which 3,741 (98.8%) had nobody in front of the camera, and
# 1,232 of those landed between midnight and 6am in an empty house. These three keys are what make that
# survivable rather than crippling:
#     ROBOT_PIR_HOLD_S                   0    the spike re-read, deliberately OFF -- it guesses from pulse
#                                             LENGTH, and a part that stops latching (a fault, or an EKMC
#                                             fitted by mistake) makes it discard every real visitor while
#                                             the motion LED keeps flashing. Stage A filters on evidence.
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
# The only argument is an optional BCM pin -- reject anything else rather than writing it into robot.env
# as ROBOT_PIR_PIN (same guard as the other wake-sensor patches).
case "$PIN" in
  -h|--help) sed -n '2,/^# ===*$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  ""|[0-9]|[0-9][0-9]) ;;
  *) echo "!! '$PIN' is not a BCM pin number. Usage: $(basename "$0") [BCM_PIN]" >&2; exit 2 ;;
esac

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-34s = %s\n' "$key" "$val"
}

echo ">> Restoring the Concierge wake defaults for a classic hobby PIR:"
# This patch writes the SAME KEY SET as 6_motion_pir_papirs.sh, only with different values -- so switching
# in either direction is COMPLETE. A patch that leaves a key its counterpart set is how you end up
# half-switched, and this repo has already been bitten by it once.
set_kv ROBOT_WAKE_SENSOR pir
# Push-pull output: it drives both states hard, so the internal pull-down is only insurance that an
# UNWIRED pin reads LOW instead of floating and inventing motion.
set_kv ROBOT_PIR_PULL down
# No warm-up gate. This module settles in ~5s, which is over before the brain finishes starting -- unlike a
# PaPIRs, whose output is undefined for up to 210s. Coming FROM that patch, this is the line that stops you
# sitting through 3.5 minutes of silence wondering what broke.
set_kv ROBOT_PIR_WARMUP_S 0
# Spike re-read OFF. This module DOES latch (~2.5s), so 0.1 is defensible on paper -- but the filter infers
# "person" from pulse LENGTH, so the day the part stops latching (ageing module, sagging supply, or an EKMC
# fitted by mistake) it discards 100% of real visitors while the motion LED still flashes: a silently deaf
# robot, and only a DEBUG line to say so. Across every field run before that it caught ZERO real noise, so
# it was pure latency on every greeting. Stage A settles the same question in ~120ms with a picture.
set_kv ROBOT_PIR_HOLD_S 0
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
echo "   AIM IT ACROSS THE TRAFFIC, not down the approach lane -- a PIR fires when a body CROSSES between"
echo "   its zones, so head-on is its worst case (12 head-on approaches gave 1-3 detections here; the same"
echo "   sensor gave 12/12 across). Mount it OUTSIDE the case, away from the Pi's warm exhaust."
echo
echo "${GRN}==============================${RST}"
echo "${GRN}  PATCH APPLIED (PIR) !!!${RST}"
echo "${GRN}==============================${RST}"
