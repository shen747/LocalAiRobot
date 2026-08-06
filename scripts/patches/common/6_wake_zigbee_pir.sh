#!/usr/bin/env bash
# =============================================================================
#   PATCH:   WIRELESS Zigbee PIR as an EXTRA Concierge wake sensor (OR'd with the wired one)
#   RUN IF:  you have a Zigbee coordinator + Zigbee2MQTT + an MQTT broker on this Pi
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    config only + pip-installs paho-mqtt into brain/.venv
# =============================================================================
# *** ADDITIVE, NOT A REPLACEMENT. ***  Unlike the two 6_motion_pir_*.sh patches -- which write the SAME key
# set with different values, so the last one you run wins -- this one ADDS a second wake source alongside
# whichever wired PIR you already have. It touches none of the ROBOT_PIR_* keys. Whichever sensor detects a
# person FIRST wakes the robot; a hit from the other one within ROBOT_WAKE_DEDUPE_S is the same arrival seen
# twice and collapses into the existing look.
#
# EVERY COMBINATION WORKS, and each part degrades on its own:
#     wired PIR only   -- do not run this patch (or leave ROBOT_ZIGBEE_TOPIC empty). Unchanged behaviour.
#     BOTH             -- the OR. This is the point.
#     WIRELESS only    -- clear ROBOT_PIR_PIN. The Zigbee sensor carries the robot on its own.
#     neither working  -- motion wake goes quiet, the WAKE WORD still works, nothing crashes.
# An unplugged dongle, a stopped broker, a missing paho-mqtt or a flat sensor battery each log one line and
# leave the wired PIR running exactly as it does today.
#
# WHAT IT BUYS YOU: COVERAGE, NOT SPEED. A GPIO pin interrupts in microseconds; a Zigbee event travels
# sensor -> coordinator -> Zigbee2MQTT -> broker -> brain, which is 50-300ms. For the SAME person the wired
# sensor wins nearly every race. Mount the wireless one where the wired one CANNOT see -- round a corner,
# the far end of the hall, the top of the stairs -- or it adds latency and nothing else. The hourly summary
# prints a per-source win count precisely so you can check it is earning its keep.
#
# BEFORE YOU RUN THIS, the plumbing must already work. Full walkthrough (dongle firmware, udev, Mosquitto,
# Zigbee2MQTT, pairing): BENCH_SCRIPTS.md "Adding a wireless Zigbee PIR". Prove it with:
#     mosquitto_sub -h localhost -t 'zigbee2mqtt/#' -v      # then wave at the sensor
# You want to SEE {"occupancy":true} before you configure the robot to listen for it.
#
# Re-runnable (idempotent). Turn it back off with:  ./scripts/patches/common/6_wake_zigbee_pir.sh --off
#   Usage:  ./scripts/patches/common/6_wake_zigbee_pir.sh zigbee2mqtt/<friendly_name>
#           ./scripts/patches/common/6_wake_zigbee_pir.sh --off
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV="$REPO/config/robot.env"
[ -f "$ENV" ] || { echo "config/robot.env not found at $ENV -- run this from the repo on the robot." >&2; exit 1; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RST=$'\033[0m'; else GRN=''; YEL=''; RST=''; fi

TOPIC="${1:-}"
case "$TOPIC" in
  -h|--help) sed -n '2,/^# ===*$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --off)     TOPIC="" ;;
  "")        echo "!! give the Zigbee2MQTT topic for your sensor, e.g." >&2
             echo "     $(basename "$0") zigbee2mqtt/pir_hallway" >&2
             echo "   Find it with:  mosquitto_sub -h localhost -t 'zigbee2mqtt/#' -v   (then wave at it)" >&2
             echo "   Turn the feature off again with:  $(basename "$0") --off" >&2
             exit 2 ;;
  -*)        echo "!! unknown option '$TOPIC' (expected a topic, or --off)" >&2; exit 2 ;;
  */*)       ;;   # looks like a topic path
  *)         echo "!! '$TOPIC' does not look like an MQTT topic. Expected something like" >&2
             echo "     zigbee2mqtt/pir_hallway" >&2
             exit 2 ;;
esac

set_kv() {   # replace KEY's whole line in place (proven pattern from common/1_esc_hb_qr_1060.sh); append if absent
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
  printf '   %-28s = %s\n' "$key" "${val:-(empty)}"
}

if [ -z "$TOPIC" ]; then
  echo ">> Turning the wireless Zigbee wake OFF (the wired PIR is untouched):"
  set_kv ROBOT_ZIGBEE_WAKE 0
  set_kv ROBOT_ZIGBEE_TOPIC ""
  echo
  echo "   Reload:  ./scripts/run_me.sh --reload"
  echo "${GRN}=========================================${RST}"
  echo "${GRN}  ZIGBEE WAKE DISABLED (wired PIR intact) ${RST}"
  echo "${GRN}=========================================${RST}"
  exit 0
fi

echo ">> Adding a wireless Zigbee PIR as a SECOND wake source (OR'd with the wired one):"
set_kv ROBOT_ZIGBEE_WAKE 1
set_kv ROBOT_ZIGBEE_TOPIC "$TOPIC"
set_kv ROBOT_MQTT_HOST "${ROBOT_MQTT_HOST:-localhost}"
set_kv ROBOT_MQTT_PORT "${ROBOT_MQTT_PORT:-1883}"
set_kv ROBOT_ZIGBEE_COOLDOWN_S 2
# The OR window. Short on purpose: the real rate limiter is ROBOT_CONCIERGE_SILENT_COOLDOWN_S, armed AFTER
# the camera has looked, so it throttles with evidence. A long blind window here would also delay a genuine
# second visitor.
set_kv ROBOT_WAKE_DEDUPE_S 2

# paho-mqtt is NOT in brain/requirements.txt on purpose -- it is dead weight on every build without a
# dongle. Install it on demand, into the same venv the brain runs from.
PY="$REPO/brain/.venv/bin/python"
echo
if [ -x "$PY" ]; then
  echo ">> Installing paho-mqtt into brain/.venv ..."
  # 2.x is what brain/zigbee_wake.py is written against (CallbackAPIVersion.VERSION2). Pinning the FLOOR
  # rather than an exact version: 1.x has a different callback signature and would fail at connect time.
  "$PY" -m pip install --quiet --upgrade 'paho-mqtt>=2.0' \
    && echo "   paho-mqtt: $("$PY" -c 'import paho.mqtt as m; print(m.__version__)' 2>/dev/null || echo installed)" \
    || echo "   ${YEL}pip install failed -- fix it, or the Zigbee source stays available=False (harmless).${RST}"
else
  echo "   ${YEL}brain/.venv not found -- run install_stack first, then re-run this patch.${RST}"
fi

echo
echo "   Reload:  ./scripts/run_me.sh --reload"
echo "   Verify:  journalctl -u robot.service -n 40 | grep -E 'wake sources:|zigbee wake:'"
echo "            want:  wake sources: gpio-pir OR zigbee-pir (dedupe 2.0s)"
echo "                   zigbee wake: MQTT connected, subscribed to '$TOPIC'"
echo
echo "   ${YEL}Prove the plumbing FIRST if either line is missing -- the robot is the last thing to blame:${RST}"
echo "     systemctl status mosquitto zigbee2mqtt --no-pager | head -20"
echo "     mosquitto_sub -h localhost -t '$TOPIC' -v          # wave at the sensor, want {\"occupancy\":true}"
echo
echo "   ${YEL}MOUNT IT WHERE THE WIRED PIR CANNOT SEE.${RST} Side by side, it will never win a race and adds"
echo "   nothing. After a week check it is pulling its weight:"
echo "     journalctl -u robot.service | grep 'wake wins:'    # want a non-zero zigbee-pir count"
echo
echo "${GRN}================================================${RST}"
echo "${GRN}  PATCH APPLIED (wireless Zigbee wake, OR'd) !!! ${RST}"
echo "${GRN}================================================${RST}"
