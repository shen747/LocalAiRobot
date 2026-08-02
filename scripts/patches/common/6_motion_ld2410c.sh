#!/usr/bin/env bash
# =============================================================================
#   PATCH:   HLK-LD2410C 24GHz mmWave PRESENCE radar as the Concierge wake trigger
#   RUN IF:  you replaced the PIR with an LD2410/LD2410C module AND the robot sits
#            somewhere people PASS THROUGH -- read the field result below first
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only -- retunes the wake timings for a sensor that doesn't lie
# =============================================================================
#
#  *** FIELD RESULT: THIS SENSOR FAILED IN A ROOM THE OWNER OCCUPIES ALL DAY. ***
#
#  Tried on a wall-mounted concierge in a home office. What was measured over ~25 minutes:
#      * FALSE wakes:      ZERO. Every single edge had a real person in front of the camera
#                          (11-16 face hits each). Against the PIR's 465 false wakes an HOUR,
#                          that part was a total success.
#      * USABLE wakes:     4 in 25 minutes -- gaps of 851s, 306s, 341s.
#      * Suppressed by us: 0 cooldown, 0 debounce, 0 noise. Nothing was filtered in software.
#  So the sensor was not lying and the pipeline was not dropping anything: the OUT pin simply
#  stayed HIGH. gpiozero triggers on the LOW->HIGH edge, and there were almost no edges to fire on.
#
#  WHY: this reports PRESENCE, out to 5-6m, THROUGH plasterboard. In a room its owner occupies,
#  "is anyone present?" is permanently YES -- including through the wall from the next room. So the
#  output only drops when the whole area is empty for longer than the absence delay, and you get one
#  edge per genuine absence rather than one per arrival. Walking past it twelve times produced ONE wake.
#
#  A PIR answers "did something just MOVE?", which is much closer to "did someone just ARRIVE?" --
#  the question a greeter actually needs. Its 465 lies/hour are survivable (that is exactly what the
#  two-stage wake in brain/main.py exists for); having no edges at all is not.
#
#  USE THIS SENSOR WHERE PRESENCE ACTUALLY CHANGES: a front door, a porch, a garage, a hallway
#  people cross and leave. There it is strictly better than a PIR -- no fan-exhaust or sunlight
#  false triggers, it sees someone who walks up and stands still, and it works from INSIDE a sealed
#  plastic enclosure (24GHz passes through polycarbonate; a PIR needs a hole).
#
#  BEFORE RETRYING IT IN AN OCCUPIED ROOM: range-gate it over the UART first (TX/RX, 256000 baud)
#  down to roughly the camera's useful distance (~2m). Then presence changes as someone approaches
#  the robot instead of being true whenever they are in the house. Untried here -- it is the obvious
#  next experiment, not a proven fix.
#
#  Going back:  ./scripts/patches/common/6_motion_pir_hcsr501.sh   (and re-wire the PIR to GPIO 4)
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
# ...AND STAGE A IS SWITCHED OFF ENTIRELY, WHICH NEEDS EXPLAINING:
#     ROBOT_CONCIERGE_PROBE_TRIES   2 -> 0   no cheap pre-check at all; go straight to the full window
#     ROBOT_CONCIERGE_RECOGNIZE_S   7 -> 10  and give the visitor 10s to walk into the camera's view
#
# STAGE A IS A LIE DETECTOR, AND THIS SENSOR DOESN'T LIE. The probe exists to kill a chattering PIR's 465
# false wakes an hour for ~120ms each, so the expensive recognition window only ever runs for a real person.
# On this radar the measured rate is ~6 wakes an hour and every one of them is a real person. There is
# nothing left to filter -- so all Stage A does is spend 1.6 seconds deciding whether to bother looking
# properly, and throw the visitor away if they weren't in frame during those 1.6 seconds.
#
# And they very often aren't, because of a RANGE/FIELD-OF-VIEW MISMATCH. The radar detects a person out to
# 5-6m and through a doorway; the camera sees a narrow cone in front of the unit. A probe run on the wall
# measured it: of 103 frames taken while someone was moving around the hallway, only 15 had a face in them
# at all -- but every single one of those was 69-221px, i.e. comfortably ABOVE the 60px floor and easily
# recognisable. The camera is aimed correctly and exposed correctly. The person is simply not in its cone
# most of the time, so sampling 4 frames from a 1.6s window mostly samples empty hallway.
#
# With PROBE_TRIES=0 the recognition window runs directly: ~50 looks over 10s (RECOGNIZE_INTERVAL_S 0.2),
# returning the instant it gets ROBOT_CONCIERGE_FACE_HITS frames with a face. That is 50 chances to catch a
# 3-5 frame burst instead of 4, and it costs ~300 frames/hour at 6 wakes/hour -- LESS than the 1,860/hour the
# PIR was spending on Stage A alone. The durable refinement is to range-gate the radar over its UART so it
# stops firing at people who are still four metres away; this makes it work well in the meantime.
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
# The only argument is an optional BCM pin. Guard the obvious mistakes -- without this, `--help` is taken
# as a pin number and written straight into config/robot.env as ROBOT_PIR_PIN=--help (asked for it, got it).
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

echo ">> Retuning the Concierge wake for an LD2410C mmWave presence sensor:"
set_kv ROBOT_PIR_HOLD_S 0
set_kv ROBOT_CONCIERGE_SILENT_COOLDOWN_S 1
set_kv ROBOT_CONCIERGE_PROBE_TRIES 0
set_kv ROBOT_CONCIERGE_RECOGNIZE_S 10
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
