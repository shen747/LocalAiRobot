#!/usr/bin/env bash
# =============================================================================
#   PATCH:   HLK-LD2410C 24GHz mmWave PRESENCE radar as the Concierge wake trigger
#   RUN IF:  you replaced the PIR with an LD2410/LD2410C module
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only -- retunes the wake timings for a sensor that doesn't lie
# =============================================================================
# NO CODE CHANGES ARE NEEDED. The LD2410C's OUT pin is a plain digital presence line -- HIGH while a person
# is there, LOW when not -- which is exactly what gpiozero.MotionSensor already reads in brain/pir.py. As far
# as the brain is concerned it is still "the PIR pin", so ROBOT_PIR_PIN and the whole two-stage wake carry
# over untouched. This patch only relaxes the defences that existed to survive a LYING sensor.
#
# WIRING (3 wires; TX/RX only needed for tuning, see below):
#     LD2410C          Pi 5
#     -------          ----
#     VCC       ->     5V   (pin 2 or 4)
#     GND       ->     GND  (pin 6, 9, ...)
#     OUT       ->     GPIO 4 (pin 7)     <- the SAME pin the PIR used; nothing else changes
#     TX / RX          leave unconnected unless you're range-gating over serial
#
#   ! CHECK THE LOGIC LEVEL FIRST. The LD2410C datasheet specifies 3.3V IO, which is Pi-safe. Clones vary.
#     If you're unsure, meter OUT against GND while the sensor is triggered BEFORE wiring it to a GPIO --
#     5V on a Pi GPIO destroys the pin (and possibly the SoC).
#
# WHY THE TIMINGS CHANGE. Every knob this patch touches exists because an HC-SR501 next to a warm Pi lies
# constantly -- a 19-hour field log had 3,786 motion wakes of which 3,741 (98.8%) had nobody in front of the
# camera, and 1,232 of those were between midnight and 6am in an empty house. The hold filter, the silent
# cooldown and the second probe frame are all damage control for that. A presence radar doesn't false-trigger
# on fan exhaust or sunlight, so paying for those defences is just latency:
#     ROBOT_PIR_HOLD_S             0.1 -> 0    the hold re-reads the pin to reject sub-100ms electrical
#                                              spikes. It has caught ZERO in every run to date even on the
#                                              PIR, and mmWave OUT is firmware-debounced. Pure delay.
#     ROBOT_CONCIERGE_SILENT_COOLDOWN_S  4 -> 1  deaf time after a "nobody there" look. On the PIR this was
#                                              load-bearing (it stopped a chattering sensor running the
#                                              camera flat out) and cost ~43% of wall clock deaf. With a
#                                              sensor that only fires for real people there is almost
#                                              nothing to cool down from.
# Expected: PIR-to-greeting drops from ~0.89s (with a ~43% chance of waiting out a cooldown first) to
# ~0.7s essentially every time. The log line to check is: journalctl -u robot.service | grep 'from PIR'
#
# ...AND ONE KNOB GOES THE OTHER WAY, WHICH IS THE OPPOSITE OF WHAT YOU'D EXPECT:
#     ROBOT_CONCIERGE_PROBE_TRIES        2 -> 4   MORE camera looks, not fewer
#     ROBOT_CONCIERGE_PROBE_INTERVAL_S 0.15 -> 0.4  spread over ~1.6s instead of ~0.3s
# The first version of this patch cut the probe to a SINGLE frame, on the reasoning that a sensor which
# doesn't lie is worth believing immediately. That was wrong, and the field log showed it: the radar fired,
# the camera took one look 1.2s later, saw nobody usable, and went back to sleep.
#
# The reason is RANGE MISMATCH. This radar detects a person out to 5-6m. The camera can only resolve a face
# big enough to recognise (ROBOT_FACE_ID_MIN_FACE_PX, default 60px in a 512px frame) at roughly 2m. So the
# sensor correctly fires while the visitor is still four metres away and walking in -- and a single frame
# taken 1.2 seconds later catches them before they have arrived. The PIR never had this problem because its
# useful range happened to match the camera's.
#
# So the probe now spans the time it takes someone to walk the last few metres. It still returns EARLY the
# moment it sees anyone, so a visitor already standing in front of the camera costs exactly one frame -- and
# an empty room costs 4 frames only ~6 times an hour on this sensor, versus 465 on the PIR. Cheap either way.
# The durable fix is to range-gate the radar over its UART so it stops firing at people the camera can't yet
# see; these numbers are what makes it work well in the meantime.
#
# BEHAVIOUR CHANGE WORTH KNOWING. A PIR reports MOVEMENT; this reports PRESENCE. OUT goes HIGH when someone
# arrives and STAYS HIGH while they stand there, even perfectly still. gpiozero fires on the rising edge, so
# you get ONE clean wake per arrival instead of a stream -- which is what ROBOT_CONCIERGE_GREET_COOLDOWN_S
# was faking, and it also fixes the thing PIRs are worst at (someone who walks up and then stops moving).
# The flip side: if Jarvis lives in a room YOU occupy all day, OUT can sit HIGH for hours and produce no new
# edges at all. For a door greeter that's ideal; for a desk companion a PIR may genuinely suit better.
#
# ! IT SEES THROUGH WALLS. 24GHz radar passes through plasterboard, so out of the box it will very likely
#   detect someone in the next room. That is the #1 complaint with these modules and it is a TUNING problem,
#   not a fault. Range-gate it over the UART (TX/RX, 256000 baud 8N1) -- per-distance-gate sensitivity plus
#   an absence delay. Budget an evening. Until then, expect wakes from people who never arrive.
#
# Re-runnable (idempotent). Opposite of common/6_motion_pir_hcsr501.sh, which puts the PIR defaults back.
#   Usage:  ./scripts/patches/common/6_motion_ld2410c.sh [BCM_PIN]
#           BCM_PIN is optional -- given, it sets ROBOT_PIR_PIN; omitted, whatever you already have is kept.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RST=$'\033[0m'; else GRN=''; YEL=''; RST=''; fi
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

echo ">> Retuning the Concierge wake for an LD2410C mmWave presence sensor:"
set_kv ROBOT_PIR_HOLD_S 0
set_kv ROBOT_CONCIERGE_SILENT_COOLDOWN_S 1
set_kv ROBOT_CONCIERGE_PROBE_TRIES 4
set_kv ROBOT_CONCIERGE_PROBE_INTERVAL_S 0.4
if [ -n "$PIN" ]; then
  set_kv ROBOT_PIR_PIN "$PIN"
else
  printf '   %-34s = %s (unchanged -- pass a BCM pin to move it)\n' "ROBOT_PIR_PIN" \
         "$(grep -E '^ROBOT_PIR_PIN=' "$ENV" | head -1 | cut -d= -f2 | awk '{print $1}')"
fi
echo
echo "   Wiring: VCC->5V, GND->GND, OUT->the GPIO above. TX/RX unconnected unless you're range-gating."
echo "   ${YEL}Verify OUT is 3.3V logic before connecting it to a GPIO -- 5V will kill the pin.${RST}"
echo
echo "   Prove it:   ./scripts/pir_sensor_probe.sh        (wave = MOTION; then walk away and watch it drop)"
echo "   Reload:     ./scripts/run_me.sh --reload         (or reboot)"
echo "   Measure:    journalctl -u robot.service | grep 'from PIR'   -- expect ~0.7s, consistently"
echo
echo "   ${YEL}Seeing wakes from the next room? That's 24GHz going through plasterboard, not a fault --${RST}"
echo "   ${YEL}range-gate it over the UART (256000 baud) or re-site the sensor.${RST}"
echo
echo "${GRN}=====================================${RST}"
echo "${GRN}  PATCH APPLIED (LD2410C mmWave) !!!${RST}"
echo "${GRN}=====================================${RST}"
