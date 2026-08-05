#!/usr/bin/env bash
# =============================================================================
#   PATCH:   Panasonic PaPIRs (EKMB/EKMC) industrial PIR as the Concierge wake
#   RUN IF:  you fitted a PaPIRs instead of the hobby HC-SR501
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only -- the pull, warm-up and wake timings this part needs
# =============================================================================
# The step up from scripts/patches/common/6_motion_pir_hcsr501.sh. Same backend, same three wires, same
# ROBOT_PIR_PIN -- brain/pir.py reads a digital HIGH/LOW either way, so no code changes. What differs is
# every NUMBER, because this is the bare industrial part rather than a hobby module.
#
# Reference part: EKMB1105112 (WL series, 1uA, black lens). Its siblings EKMB1205112 (2uA) and
# EKMB1305112K (6uA) share every figure below. THE EKMC SERIES IS DIFFERENT -- see ROBOT_PIR_HOLD_S.
#
# +---------------------------------------------------------------------------------------------------+
# | !! 3.3V ONLY. 5V DESTROYS THIS SENSOR. !!                                                          |
# |    Operating voltage 2.3-4.0 VDC; ABSOLUTE MAXIMUM rating 4.5 VDC (datasheet 4-3/4-4). 5V is past  |
# |    the destruct limit for the part itself -- quite apart from putting 5V on a Pi GPIO that is       |
# |    3.3V-only and NOT 5V-tolerant. The HC-SR501 wants pin 2; this wants pin 1. Do not reuse the      |
# |    old VCC wire without moving it.                                                                 |
# +---------------------------------------------------------------------------------------------------+
#
# WIRING -- three wires, same signal pin as any other PIR:
#     PaPIRs           Pi 5
#     ------           ----
#     VDD       ->     3.3V (pin 1)        <- NOT pin 2. See the box above.
#     GND       ->     GND  (pin 6, 9, ...)
#     OUT       ->     GPIO 4 (pin 7)      <- or whatever BCM pin you pass as this script's argument
#   + a 0.1uF ceramic capacitor ACROSS VDD/GND, right at the sensor. This is REQUIRED, not tidiness:
#     6-3(8) says "power supply noise can cause operating errors... install a capacitor on the sensor's
#     power supply pin", and noise immunity is only +/-20V. Add 10-100uF bulk if the wire run is long,
#     and use SHIELDED cable kept SHORT (6-3(6)).
#   ! The pin ORDER is a drawing in the datasheet, not text. Read it off the "Bottom VIEW" on page 1 --
#     do NOT assume the schematic order in the 4-7 wiring diagram is the physical order.
#   ! Handle it by the body: 6-3(5) warns +/-200V of static can destroy it.
#
# MOUNTING -- fussier than a hobby module, and the numbers are specified:
#   * The LENS must protrude >= 6.2mm past your enclosure for the full detection area (A and B), or
#     >= 0.7mm for area A alone (7-1/7-2). Recessed deeper you are optically cropping it, which looks
#     exactly like a dead sensor. Make the panel hole TAPERED or oversized, not a tight straight bore.
#   * NO GLASS OR ACRYLIC in front of it (6-1(2a)) -- they block IR. A "protective window" disables it.
#   * Keep it away from HVAC vents, hot/cold draughts and humidifier vapour (6-1(1c)). On the reference
#     build a PIR mounted in the enclosure's only vent hole -- watching the Pi's own warm exhaust --
#     produced 465 false wakes an HOUR. Mount it OUTSIDE the case, in still air.
#   * It is still a PASSIVE sensor: it fires when a body CROSSES between its detection zones, so aim it
#     ACROSS the traffic rather than down the approach lane. This part is much better at head-on than a
#     hobby module (88 zones over 122 degrees vs roughly a dozen), but the physics is unchanged.
#
# Re-runnable (idempotent). Back to the hobby module: 6_motion_pir_hcsr501.sh -- it rewrites the SAME
# keys with that sensor's values, so switching either way is complete.
#   Usage:  ./scripts/patches/common/6_motion_pir_papirs.sh [BCM_PIN]
#           BCM_PIN is optional -- given, it sets ROBOT_PIR_PIN; omitted, whatever you already have is kept.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RST=$'\033[0m'; else GRN=''; YEL=''; RST=''; fi
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

echo ">> Setting the Concierge wake up for a Panasonic PaPIRs:"
# This patch writes the SAME KEY SET as 6_motion_pir_hcsr501.sh, only with different values. That is
# deliberate: a patch that sets a key its counterpart does not is how you end up half-switched. This repo
# has already been bitten by exactly that -- an earlier sensor patch flipped ROBOT_WAKE_SENSOR while the
# PIR patch did not, so switching back left the brain polling hardware that had been unplugged.
set_kv ROBOT_WAKE_SENSOR pir
# SOURCES current only (Iout max 100uA), ACTIVE-HIGH, and high-impedance when idle (4-4 + the 4-7 wiring
# diagram, which shows current flowing OUT of the sensor into your load). It cannot pull the pin down
# itself, so it NEEDS the internal pull-down to define its LOW state -- float the pin and it wanders and
# invents visitors all night. The Pi's ~50k pull-down draws ~66uA at 3.3V, inside the 100uA budget, and
# Vout is still specified >= Vdd-0.5V = 2.8V against that load, over the Pi's ~2.31V logic-high threshold.
set_kv ROBOT_PIR_PULL down
# Circuit Stability Time (Twu) is 25s TYPICAL but 210s MAXIMUM (4-4), and during it "the output's status
# is undefined (ON/OFF) and detection is therefore not guaranteed" (4-5). 210 is the only spec-compliant
# value and it costs almost nothing -- THE WAKE WORD STILL WORKS throughout, and a wall-mounted concierge
# boots about once a day.
set_kv ROBOT_PIR_WARMUP_S 210
# Keep the noise re-read: this part DOES latch. 4-5 gives it a Standby Hold Time of ~2.6s typ (extended
# while motion continues) then a ~1.3s Mask Time, so "still HIGH 100ms later" separates a person from a
# spike exactly as designed. (The EKMC series has NO hold time, NO mask time and NO minimum pulse width --
# on THAT part this must be 0, or every real visitor is discarded as noise.)
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
echo "   ${YEL}VDD -> 3.3V (pin 1), NOT 5V: 5V is past this sensor's 4.5V absolute maximum and kills it.${RST}"
echo "   ${YEL}A 0.1uF cap across VDD/GND at the sensor is REQUIRED, and the lens needs >=6.2mm clearance.${RST}"
echo
echo "   Reload:     ./scripts/run_me.sh --reload     (or reboot)"
echo "   Verify:     journalctl -u robot.service -n 40 | grep 'PIR motion wake'"
echo "               want:  warming up for 210s ...   then  armed on BCM GPIO <pin> ... pull=down"
echo "   Prove it:   ./scripts/pir_sensor_probe.sh    (walk past 10x -- the count should track your passes)"
echo
echo "   ${YEL}ON THE BENCH, drop the warm-up or you will debug a working sensor:${RST}"
echo "     sed -i 's|^ROBOT_PIR_WARMUP_S=.*|ROBOT_PIR_WARMUP_S=30|' config/robot.env"
echo "   3.5 minutes of silence after every restart is the spec, but it looks exactly like a dead sensor."
echo
echo "${GRN}===================================${RST}"
echo "${GRN}  PATCH APPLIED (PaPIRs PIR) !!!${RST}"
echo "${GRN}===================================${RST}"
