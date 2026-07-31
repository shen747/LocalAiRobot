#!/usr/bin/env bash
# purge_detect_captures.sh -- delete old detect-captures so a 24/7 greeter can't fill the disk.
#
# In Concierge Mode every PIR motion wake makes Jarvis look, and the frame that DECIDED the outcome is kept in
# ~/detect-captures (brain/captures.py) so you can see what it saw when it greeted -- or greeted a ghost. Left
# alone that folder only grows, so a cron job runs this DAILY. The cron entry is installed by
# scripts/install_detect_capture_cron.sh (which run_me.sh --install-boot and both install_stack_*.sh call).
#
# Two limits, applied in order -- age first, then a hard size cap for the case where a chattery sensor
# out-captures the daily schedule:
#   ROBOT_DETECT_CAPTURE_KEEP_DAYS   delete captures older than this many days   (default 7; 0 = no age limit)
#   ROBOT_DETECT_CAPTURE_MAX_MB      then delete OLDEST-first until under this   (default 2000; 0 = no cap)
#   ROBOT_DETECT_CAPTURE_DIR         which folder                               (default ~/detect-captures)
#
# Safe by construction: it only ever removes *.jpg files DIRECTLY inside the target folder (never recurses,
# never touches subdirectories), and refuses to run against $HOME or / even if the config points there.
# Run it by hand any time; --dry-run shows what would go without deleting.
#   ./scripts/purge_detect_captures.sh            # purge now
#   ./scripts/purge_detect_captures.sh --dry-run  # just report
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Load config so a cron run (which has almost no environment) sees the same settings as the brain. An inline
# env var still wins -- note the value already in the environment is re-applied after sourcing.
_KEEP="${ROBOT_DETECT_CAPTURE_KEEP_DAYS:-}"; _MAX="${ROBOT_DETECT_CAPTURE_MAX_MB:-}"; _DIR="${ROBOT_DETECT_CAPTURE_DIR:-}"
if [ -f "$REPO/config/robot.env" ]; then set -a; . "$REPO/config/robot.env"; set +a; fi
[ -n "$_KEEP" ] && ROBOT_DETECT_CAPTURE_KEEP_DAYS="$_KEEP"
[ -n "$_MAX" ]  && ROBOT_DETECT_CAPTURE_MAX_MB="$_MAX"
[ -n "$_DIR" ]  && ROBOT_DETECT_CAPTURE_DIR="$_DIR"

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY=1 ;;
    -h|--help) echo "usage: $(basename "$0") [--dry-run]"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

DIR="${ROBOT_DETECT_CAPTURE_DIR:-$HOME/detect-captures}"
DIR="${DIR/#\~/$HOME}"                              # expand a leading ~ (config files store it literally)
KEEP_DAYS="${ROBOT_DETECT_CAPTURE_KEEP_DAYS:-7}"
MAX_MB="${ROBOT_DETECT_CAPTURE_MAX_MB:-2000}"

# Refuse to operate anywhere that isn't a dedicated captures folder. A typo'd/empty config must never turn a
# scheduled cleanup into a scheduled data loss, so this bails rather than "helpfully" defaulting.
case "$DIR" in
  ""|"/"|"$HOME"|"$HOME/") echo "!! refusing to purge $DIR -- set ROBOT_DETECT_CAPTURE_DIR to a dedicated folder" >&2; exit 1 ;;
esac
[ -d "$DIR" ] || { echo ">> detect-captures: nothing to do ($DIR does not exist yet)."; exit 0; }

_count() { find "$DIR" -maxdepth 1 -type f -name '*.jpg' 2>/dev/null | wc -l | tr -d ' '; }
_size_mb() { du -sm "$DIR" 2>/dev/null | cut -f1; }

BEFORE_N="$(_count)"; BEFORE_MB="$(_size_mb)"
echo ">> detect-captures: $DIR -- ${BEFORE_N} file(s), ${BEFORE_MB}MB (keep ${KEEP_DAYS}d, cap ${MAX_MB}MB)"

# --- 1. age ------------------------------------------------------------------------------------------------
# -maxdepth 1 -type f -name '*.jpg' keeps this to our own capture files; -mtime +N is "older than N days".
if [ "${KEEP_DAYS}" != "0" ]; then
  if [ "$DRY" = 1 ]; then
    n="$(find "$DIR" -maxdepth 1 -type f -name '*.jpg' -mtime "+${KEEP_DAYS}" 2>/dev/null | wc -l | tr -d ' ')"
    echo "   [dry-run] would delete ${n} capture(s) older than ${KEEP_DAYS} day(s)"
  else
    find "$DIR" -maxdepth 1 -type f -name '*.jpg' -mtime "+${KEEP_DAYS}" -delete 2>/dev/null
  fi
fi

# --- 2. size cap -------------------------------------------------------------------------------------------
# Still over the cap after the age pass (a chattery PIR can out-capture a DAILY purge) -> drop the oldest
# files until it fits. Sorted by mtime so the newest -- the ones you'd actually want to review -- survive.
if [ "${MAX_MB}" != "0" ]; then
  cur="$(_size_mb)"
  if [ -n "$cur" ] && [ "$cur" -gt "$MAX_MB" ] 2>/dev/null; then
    echo "   over the ${MAX_MB}MB cap (${cur}MB) -- dropping oldest captures until it fits"
    # NUL-delimited + read -d '' so filenames can never be word-split (they're generated, but this is a
    # delete loop -- it gets the careful treatment regardless).
    while IFS= read -r -d '' -u 3 f; do
      cur="$(_size_mb)"; [ -n "$cur" ] && [ "$cur" -le "$MAX_MB" ] 2>/dev/null && break
      if [ "$DRY" = 1 ]; then echo "   [dry-run] would delete $f"; else rm -f -- "$f"; fi
      [ "$DRY" = 1 ] && break   # dry-run can't shrink the folder, so don't loop forever
    done 3< <(find "$DIR" -maxdepth 1 -type f -name '*.jpg' -printf '%T@ %p\0' 2>/dev/null \
              | sort -zn | sed -z 's/^[0-9.]* //')
  fi
fi

AFTER_N="$(_count)"; AFTER_MB="$(_size_mb)"
if [ "$DRY" = 1 ]; then
  echo ">> [dry-run] nothing deleted."
else
  echo ">> detect-captures: now ${AFTER_N} file(s), ${AFTER_MB}MB (removed $((BEFORE_N - AFTER_N)))"
fi
