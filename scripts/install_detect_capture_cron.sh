#!/usr/bin/env bash
# install_detect_capture_cron.sh -- schedule the daily purge of ~/detect-captures.
#
# Concierge Mode keeps the frame that decided each greeting (brain/captures.py) so you can see what Jarvis saw
# when it greeted you -- or greeted nobody. That folder only grows, so this installs ONE crontab line running
# scripts/purge_detect_captures.sh daily. Without it a 24/7 greeter on a chattery PIR will eventually eat a
# 250GB NVMe (the brain also self-prunes as a backstop, but cron is what keeps it tidy while the brain is off).
#
# Called automatically by run_me.sh --install-boot, install_stack_pi5.sh and install_stack_jetson.sh; also
# runnable standalone. Idempotent -- the entry is TAGGED, so a re-run REPLACES it rather than stacking copies.
# Honours ROBOT_DETECT_CAPTURE_CRON=0 (skip) and ROBOT_DETECT_CAPTURE=0 (nothing is being captured -> no purge
# needed). Undo:
#   ./scripts/install_detect_capture_cron.sh --remove
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TAG="# [LocalAiRobot detect-captures purge]"
PURGE="$REPO/scripts/purge_detect_captures.sh"
# Default 04:17 daily: off the hour so it can't pile onto every other cron job in the house, and in the small
# hours when nobody is being greeted. Override with ROBOT_DETECT_CAPTURE_CRON_SCHEDULE (5-field cron syntax).
SCHEDULE="${ROBOT_DETECT_CAPTURE_CRON_SCHEDULE:-17 4 * * *}"
LOGF="$HOME/.localairobot/detect-captures-purge.log"

REMOVE=0
for arg in "$@"; do
  case "$arg" in
    --remove|-r) REMOVE=1 ;;
    -h|--help) echo "usage: $(basename "$0") [--remove]"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Strip any existing tagged entry. This is BOTH the uninstall path and the first half of a re-install, so the
# two can't drift apart. `crontab -l` exits non-zero when there's no crontab at all -> tolerate it.
_drop_existing() {
  local cur
  cur="$(crontab -l 2>/dev/null || true)"
  printf '%s\n' "$cur" | grep -vF "$TAG" | grep -v '^$' | crontab - 2>/dev/null
}

# Opt-outs are checked FIRST, so a robot that deliberately wants no cron entry gets a message about ITS choice
# rather than a complaint about a missing crontab it was never going to use.
if [ "$REMOVE" != 1 ] && [ "${ROBOT_DETECT_CAPTURE_CRON:-1}" = 0 ]; then
  echo ">> detect-captures purge: skipped (ROBOT_DETECT_CAPTURE_CRON=0). Purge by hand: $PURGE"
  exit 0
fi
if [ "$REMOVE" != 1 ] && [ "${ROBOT_DETECT_CAPTURE:-1}" = 0 ]; then
  echo ">> detect-captures purge: skipped (ROBOT_DETECT_CAPTURE=0 -- no captures are being kept)."
  REMOVE=1   # a previously-installed entry would now be pointless -> fall through and drop it
fi

if ! command -v crontab >/dev/null 2>&1; then
  echo ">> detect-captures purge: 'crontab' not found -- skipping the schedule."
  echo "   Install it (sudo apt install cron) then re-run, or purge by hand: $PURGE"
  echo "   (The brain still self-prunes ~/detect-captures at startup, so it stays bounded meanwhile.)"
  exit 0
fi

if [ "$REMOVE" = 1 ]; then
  echo ">> Removing the detect-captures purge cron entry."
  _drop_existing
  echo "   removed. (Captures in ~/detect-captures are left alone -- delete them yourself if you want.)"
  exit 0
fi
[ -f "$PURGE" ] || { echo "!! missing $PURGE -- cannot schedule the purge" >&2; exit 1; }
chmod +x "$PURGE" 2>/dev/null || true
mkdir -p "$(dirname "$LOGF")" 2>/dev/null || true

# Rebuild the crontab: everything EXCEPT our tagged line, plus a fresh tagged line. Output is appended to a
# small log (and truncated first) so a failing purge leaves evidence instead of mailing root forever.
_drop_existing
CUR="$(crontab -l 2>/dev/null || true)"
ENTRY="${SCHEDULE} /bin/bash ${PURGE} >${LOGF} 2>&1 ${TAG}"
if printf '%s\n%s\n' "$CUR" "$ENTRY" | grep -v '^$' | crontab -; then
  echo ">> detect-captures purge scheduled (cron): ${SCHEDULE}"
  echo "   keeps ${ROBOT_DETECT_CAPTURE_KEEP_DAYS:-7} day(s) of ${ROBOT_DETECT_CAPTURE_DIR:-~/detect-captures}, capped at ${ROBOT_DETECT_CAPTURE_MAX_MB:-2000}MB"
  echo "   check it:  crontab -l | grep detect-captures     last run:  cat $LOGF"
  echo "   run now:   $PURGE          preview:  $PURGE --dry-run"
else
  echo "!! could not write the crontab -- schedule it by hand:" >&2
  echo "   (crontab -l; echo '${ENTRY}') | crontab -" >&2
  exit 1
fi
