#!/usr/bin/env bash
# =============================================================================
#   PATCH:   Vishay VCNL4200 active-IR PROXIMITY sensor as the Concierge wake trigger
#   RUN IF:  you replaced the PIR with an Adafruit VCNL4200 breakout (I2C)
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    switches the wake backend to brain/proximity.py + installs its driver
# =============================================================================
# UNLIKE THE PIR PATCH, THIS ONE IS NOT WIRE-COMPATIBLE. A PIR (and any other digital-pin sensor) hands the Pi a
# single digital HIGH/LOW on ROBOT_PIR_PIN, so swapping those is three wires and a few numbers. The
# VCNL4200 talks I2C: it needs SDA + SCL, it powers up with its proximity sensor DISABLED, and it must be
# configured over the bus before it measures anything. So this patch also flips ROBOT_WAKE_SENSOR, which
# tells brain/main.py to build a ProximityWake instead of a MotionWake.
#
# WIRING -- FOUR wires, and NOT the PIR's pins (GPIO 4 is unused by this backend):
#     VCNL4200         Pi 5
#     --------         ----
#     VIN       ->     3.3V (pin 1)
#     GND       ->     GND  (pin 6)
#     SDA       ->     GPIO2 / SDA (pin 3)
#     SCL       ->     GPIO3 / SCL (pin 5)
#     INT              leave unconnected (we poll; see brain/proximity.py for why)
#   I2C must be enabled:  sudo raspi-config -> Interface Options -> I2C -> Yes   (then reboot)
#   Prove the chip is on the bus:  i2cdetect -y 1     -> expect a device at 0x51
#
# WHY YOU MIGHT WANT THIS. A PIR is PASSIVE: it fires when a warm body CROSSES between its Fresnel zones.
# Someone walking STRAIGHT AT a wall-mounted greeter barely crosses any -- measured on the reference build
# with sensitivity at MAX and the delay pot at MIN, 12 deliberate head-on approaches produced 1-3 detections,
# while the SAME sensor gave 12/12 when the person crossed its field instead. A heated room and winter
# clothing make it worse again (both cut the thermal contrast a PIR depends on), and the HC-SR501 has a
# hardware-fixed ~2.5s blocking time after every trigger, so rapid arrivals physically cannot all register.
# An ACTIVE sensor sidesteps all of that: it emits its own IR and measures the reflection, so head-on
# approach is its BEST case rather than its worst.
#
# WHAT IT COSTS. Useful range is ~50-100cm (Adafruit's own guidance; the 1.5m spec needs ideal conditions),
# so this suits a greeter people walk UP TO, not one watching a corridor. Reflectivity matters -- a black
# coat returns far less than a white shirt -- so measure the threshold against the worst case you care
# about. And like any presence sensor it reports PRESENCE, not movement: one wake per arrival, held while
# somebody stands there.
#
# ! THE THRESHOLDS ARE NOT GUESSABLE. ROBOT_PROXIMITY_NEAR/_FAR are RAW reflected-IR counts, not centimetres,
#   and how fast they rise depends on the surface. The defaults below are a STARTING POINT. Measure yours:
#       sudo systemctl stop robot.service
#       ./scripts/proximity_probe.sh          # empty doorway, then walk up -- in dark clothes as well as light
#   It prints a live bar graph and suggests NEAR/FAR from the session.
#
# Re-runnable (idempotent). Back to a PIR: ./scripts/patches/common/6_motion_pir_hcsr501.sh (and re-wire).
#   Usage:  ./scripts/patches/common/6_motion_vcnl4200.sh [NEAR] [FAR]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RST=$'\033[0m'; else GRN=''; YEL=''; RST=''; fi

NEAR="${1:-300}"; FAR="${2:-200}"
case "$NEAR$FAR" in *[!0-9]*) echo "!! NEAR and FAR must be plain numbers. Usage: $(basename "$0") [NEAR] [FAR]" >&2; exit 2 ;; esac
[ "$FAR" -lt "$NEAR" ] || { echo "!! FAR ($FAR) must be BELOW NEAR ($NEAR) -- the gap is the hysteresis." >&2; exit 2; }

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-34s = %s\n' "$key" "$val"
}

echo ">> Switching the Concierge wake to a VCNL4200 active-IR proximity sensor:"
set_kv ROBOT_WAKE_SENSOR proximity
set_kv ROBOT_PROXIMITY_NEAR "$NEAR"
set_kv ROBOT_PROXIMITY_FAR "$FAR"
# The PIR's hold filter is meaningless here (there is no noisy edge to debounce -- we read a register), and
# the head-on approach this sensor is FOR means the visitor is already close when it fires, so the camera
# does not need a long walk-in window.
set_kv ROBOT_PIR_HOLD_S 0

# The driver is a normal dependency (brain/requirements.txt), but a robot that predates it won't have it
# yet, and install_stack is a heavy way to fetch one pure-Python package. Do it here, best-effort.
PY="$REPO/brain/.venv/bin/python"
if [ -x "$PY" ]; then
  if "$PY" -c "import adafruit_vcnl4200" >/dev/null 2>&1; then
    echo "   driver: adafruit-circuitpython-vcnl4200 already installed"
  else
    echo "   driver: installing adafruit-circuitpython-vcnl4200 into brain/.venv ..."
    "$PY" -m pip install --quiet adafruit-circuitpython-vcnl4200 \
      && echo "   driver: installed" \
      || echo "   ${YEL}driver: install FAILED -- run it by hand, or the sensor stays unavailable:${RST}
       $PY -m pip install adafruit-circuitpython-vcnl4200"
  fi
else
  echo "   ${YEL}brain/.venv not found -- run ./scripts/install_stack_pi5.sh, then re-run this patch.${RST}"
fi

echo
echo "   Wiring (NOT the PIR's pins): VIN->3.3V(1)  GND->GND(6)  SDA->GPIO2(3)  SCL->GPIO3(5).  INT unused."
echo "   I2C on?    sudo raspi-config -> Interface Options -> I2C -> Yes   (reboot after)"
echo "   On the bus? i2cdetect -y 1                  -> expect 0x51"
echo
echo "   ${YEL}NOW MEASURE YOUR THRESHOLDS -- the defaults are a starting point, not a setting:${RST}"
echo "     sudo systemctl stop robot.service"
echo "     ./scripts/proximity_probe.sh     # empty doorway, then walk up (dark clothes too), Ctrl-C"
echo "     ...then re-run this patch with the numbers it suggests:  $(basename "$0") <NEAR> <FAR>"
echo
echo "   Reload:  ./scripts/run_me.sh --reload      Verify:  journalctl -u robot.service | grep 'proximity'"
echo "   Back to a PIR:  ./scripts/patches/common/6_motion_pir_hcsr501.sh   (and re-wire OUT to GPIO 4)"
echo
echo "${GRN}=========================================${RST}"
echo "${GRN}  PATCH APPLIED (VCNL4200 proximity) !!!${RST}"
echo "${GRN}=========================================${RST}"
