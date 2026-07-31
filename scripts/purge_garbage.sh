#!/usr/bin/env bash
# purge_garbage.sh -- wipe a robot back to a CLEAN SLATE before a fresh run.
#
# The workflow this exists for: you SSH into the Pi after a fixing session on your PC, pull the new code,
# and want the next run's evidence to be about the NEW code -- not tangled up with a week of captures from
# the old behaviour, a journal that still holds the bug you just fixed, and a memory.db whose face
# embeddings were computed by the code path you just replaced. So:
#
#     ssh ramesh@LocalAiRobot-Jarvis.local
#     cd ~/LocalAiRobot && git pull
#     ./scripts/purge_garbage.sh              # <- this script (asks before anything destructive)
#     bash enroll_all.sh                      # re-enrol faces (REQUIRED if you wiped the db)
#     ./scripts/run_me.sh --install-boot && sudo reboot
#
# WHAT IT CLEARS  (pick with flags; default = the safe subset, --all = everything)
#   captures   ~/detect-captures/*.jpg           every kept motion frame          SAFE
#   logs       journalctl for robot.service,     so the next log starts at line 1 SAFE
#              ~/.localairobot/robot.log, repo *.log
#   cache      apt archives, pip cache, thumbnails, old journals, and the         SAFE
#              __pycache__ of YOUR code (brain/.venv is deliberately left alone)
#   db         ~/.localairobot/memory.db         faces + memories + who it is     ***DESTRUCTIVE***
#
# THE DATABASE IS THE ONE THAT BITES. memory.db holds every enrolled FACE (with the pronunciation and the
# relationship description you typed), everything the robot has been told to remember, and its own identity
# record. Deleting it is not "clearing a cache" -- it is forgetting everyone. It is only touched with an
# explicit --db / --all AND a typed confirmation, and a timestamped backup is written first either way.
#
# WHEN YOU *DO* WANT --db: right after installing the YuNet landmark detector for the first time. Face
# embeddings are only comparable with others made the same way, and embeddings created before 5-point
# alignment are not comparable with aligned ones -- a half-migrated db recognises nobody. Wipe, re-enrol,
# done. (See brain/face_id.py and scripts/provision_face_id.sh.)
#
#   ./scripts/purge_garbage.sh                 # captures + logs + cache   (keeps the db)
#   ./scripts/purge_garbage.sh --all           # ...AND the database       (asks first)
#   ./scripts/purge_garbage.sh --dry-run       # show what WOULD go, touch nothing
#   ./scripts/purge_garbage.sh --captures      # just one category (--logs / --cache / --db also work)
#   ./scripts/purge_garbage.sh --all --yes     # unattended (skips the typed confirmation)
#
# RUN IT AS YOURSELF -- *** NOT *** WITH sudo. It calls sudo itself for the two steps that need it (the
# systemd journal and apt). Under sudo, HOME becomes /root and every path retargets to root's home, so it
# would find no captures and no database and report success having purged nothing. It refuses to run as
# root for exactly that reason.
#   "sudo: ./scripts/purge_garbage.sh: command not found" means one of two things -- you used sudo (drop
#   it), or the file lost its +x bit (git on Windows doesn't carry file modes unless they're staged with
#   `git add --chmod=+x`). Either fix works:   bash ./scripts/purge_garbage.sh   |   chmod +x scripts/*.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Load config so this behaves the same from cron/SSH as it does for the brain -- but an INLINE env var must
# still win, so remember what was already set and re-apply it after sourcing (same pattern as
# purge_detect_captures.sh). Without this, `ROBOT_MEMORY_DB=/tmp/x.db ./purge_garbage.sh --db` would silently
# ignore the override and delete the db named in config/robot.env instead. On a script whose job is deleting
# things, "the path you typed was quietly replaced" is not an acceptable failure mode.
_ENV_CAPDIR="${ROBOT_DETECT_CAPTURE_DIR:-}"; _ENV_DB="${ROBOT_MEMORY_DB:-}"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
[ -n "$_ENV_CAPDIR" ] && ROBOT_DETECT_CAPTURE_DIR="$_ENV_CAPDIR"
[ -n "$_ENV_DB" ] && ROBOT_MEMORY_DB="$_ENV_DB"

DRY=0; YES=0; WANT_CAPTURES=0; WANT_LOGS=0; WANT_CACHE=0; WANT_DB=0; PICKED=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY=1 ;;
    --yes|-y)     YES=1 ;;
    --captures)   WANT_CAPTURES=1; PICKED=1 ;;
    --logs)       WANT_LOGS=1;     PICKED=1 ;;
    --cache)      WANT_CACHE=1;    PICKED=1 ;;
    --db)         WANT_DB=1;       PICKED=1 ;;
    --all|-a)     WANT_CAPTURES=1; WANT_LOGS=1; WANT_CACHE=1; WANT_DB=1; PICKED=1 ;;
    # Print the whole header block, however long it grows: everything from line 2 up to the line before
    # `set -uo pipefail`. A hard-coded range silently truncates --help the moment the header is edited.
    -h|--help)    sed -n "2,$(($(grep -n '^set -uo pipefail' "$0" | head -1 | cut -d: -f1) - 1))p" "$0" \
                    | sed 's/^# \{0,1\}//'; exit 0 ;;
    "")           ;;
    *) echo "unknown option: $arg  (try --help)" >&2; exit 2 ;;
  esac
done
# No category flags at all -> the SAFE default set. The db is never in a default run.
if [ "$PICKED" = 0 ]; then WANT_CAPTURES=1; WANT_LOGS=1; WANT_CACHE=1; fi

if [ -t 1 ]; then B=$'\033[1m'; GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; RED=$'\033[1;31m'; RST=$'\033[0m'
else B=''; GRN=''; YEL=''; RED=''; RST=''; fi

CAPTURE_DIR="$(eval echo "${ROBOT_DETECT_CAPTURE_DIR:-$HOME/detect-captures}")"
DB="$(eval echo "${ROBOT_MEMORY_DB:-$HOME/.localairobot/memory.db}")"
STATE="$HOME/.localairobot"
BACKUPS="$STATE/backups"
freed_kb=0

note()  { printf '%s\n' "$*"; }
step()  { printf '\n%s>> %s%s\n' "$B" "$*" "$RST"; }
did()   { printf '   %s%s%s\n' "$GRN" "$*" "$RST"; }
skip()  { printf '   %s(skipped: %s)%s\n' "$YEL" "$*" "$RST"; }

# du in KB for a path that may not exist; used only to report how much came back.
size_kb() { [ -e "$1" ] && du -sk "$1" 2>/dev/null | awk '{print $1}' || echo 0; }
human()  { awk -v k="$1" 'BEGIN{ if(k>1048576) printf "%.1f GB", k/1048576; else if(k>1024) printf "%.0f MB", k/1024; else printf "%d KB", k }'; }

# run <description> <command...>: honour --dry-run uniformly, so EVERY destructive action in this script
# goes through one gate and none of them can forget to check it.
run() {
  local desc="$1"; shift
  if [ "$DRY" = 1 ]; then printf '   %s[dry-run]%s would %s\n' "$YEL" "$RST" "$desc"; return 0; fi
  "$@"
}

# DON'T RUN THIS UNDER sudo. It is written to run as YOU, and calls sudo itself for the two things that
# need it (journalctl, apt). Run the whole thing as root and sudo resets HOME to /root, so every path below
# silently retargets: ~/detect-captures -> /root/detect-captures, ~/.localairobot/memory.db -> /root/...
# None of those exist, so the script cheerfully reports "nothing to purge" while the real captures and the
# real database sit untouched -- a purge that looks like it worked and did nothing is worse than an error.
# (And with --db it would hunt for a database in the wrong home entirely.)
# Refuse only the ACTUAL mistake -- root via sudo from a normal account -- rather than root as such. A robot
# genuinely running its brain as root has HOME=/root legitimately, and there is nothing wrong with purging
# it; blocking that would be a bug of its own. SUDO_USER is what distinguishes the two.
if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  SUDO_HOME="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
  SUDO_HOME="${SUDO_HOME:-/home/$SUDO_USER}"
  note "${RED}Don't run this with sudo.${RST}  It calls sudo itself for the journal + apt steps."
  note ""
  note "sudo reset HOME to ${B}/root${RST}, so every path below would retarget away from ${B}$SUDO_USER${RST}:"
  note "    captures : /root/detect-captures        ${YEL}(yours: $SUDO_HOME/detect-captures)${RST}"
  note "    database : /root/.localairobot/memory.db  ${YEL}(yours: $SUDO_HOME/.localairobot/memory.db)${RST}"
  note "Neither exists, so it would purge NOTHING and still report success."
  note ""
  note "Run it as yourself instead:"
  note "    ${B}cd $REPO && ./scripts/purge_garbage.sh $*${RST}"
  note ""
  note "Getting 'command not found' without sudo? The +x bit didn't survive the commit -- either works:"
  note "    ${B}bash ./scripts/purge_garbage.sh $*${RST}"
  note "    ${B}chmod +x scripts/*.sh${RST}"
  exit 1
fi

# Is this actually the robot? This script runs `sudo apt-get clean`, vacuums the systemd journal and deletes
# *.log out of the repo -- all correct on the Pi, all unwelcome on the PC you develop on, where those same
# repo logs are the evidence you just pulled off the robot. A robot always has ~/.localairobot (the brain
# creates it) or an installed robot.service; a dev box normally has neither.
if [ ! -d "$STATE" ] && ! { command -v systemctl >/dev/null 2>&1 && \
     systemctl list-unit-files robot.service >/dev/null 2>&1 && \
     [ -f /etc/systemd/system/robot.service ]; }; then
  note "${YEL}This does not look like the robot:${RST} no $STATE and no installed robot.service."
  note "This script cleans APT caches, wipes the systemd journal and deletes *.log from $REPO --"
  note "on a development machine those logs are usually the evidence you just pulled off the Pi."
  if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then
    note "${YEL}(continuing anyway: --yes/--dry-run given)${RST}"
  elif [ -t 0 ]; then
    printf 'Continue anyway? type %sYES%s: ' "$B" "$RST"; read -r reply
    [ "$reply" = "YES" ] || { note "Aborted."; exit 1; }
  else
    note "${RED}Aborted${RST} -- re-run with --yes if you really mean it."; exit 1
  fi
fi

note "${B}LocalAiRobot -- purge to a clean slate${RST}"
note "  repo         : $REPO"
note "  captures dir : $CAPTURE_DIR"
note "  database     : $DB"
[ "$DRY" = 1 ] && note "  ${YEL}DRY RUN -- nothing will be deleted${RST}"

# ---------------------------------------------------------------------------------------------------
# Stop the brain first. Deleting the journal / db / captures under a RUNNING robot is how you get a
# half-written sqlite file and a service that keeps re-creating what you just removed.
# ---------------------------------------------------------------------------------------------------
BRAIN_WAS_RUNNING=0
if pgrep -f 'python.*brain/main\.py' >/dev/null 2>&1 || \
   { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service 2>/dev/null; }; then
  BRAIN_WAS_RUNNING=1
  step "Stopping the running brain (it would recreate what we delete, and sqlite hates being wiped live)"
  run "stop the brain via scripts/shutdown.sh" bash "$REPO/scripts/shutdown.sh" >/dev/null 2>&1 || true
  did "stopped"
else
  step "No brain running -- nothing to stop"
fi

# ---------------------------------------------------------------------------------------------------
# 1. DETECT CAPTURES
# ---------------------------------------------------------------------------------------------------
if [ "$WANT_CAPTURES" = 1 ]; then
  step "1. Detect captures  (every frame Jarvis kept behind a greeting or a silent look)"
  # Same safety rail as purge_detect_captures.sh: only *.jpg DIRECTLY inside a dedicated folder, and never
  # $HOME or / even if the config is wrong. A typo in ROBOT_DETECT_CAPTURE_DIR must not become data loss.
  case "$CAPTURE_DIR" in
    "$HOME"|"$HOME/"|/|"") skip "refusing to purge '$CAPTURE_DIR' -- that is \$HOME or /" ;;
    *)
      if [ -d "$CAPTURE_DIR" ]; then
        n=$(find "$CAPTURE_DIR" -maxdepth 1 -type f -name '*.jpg' 2>/dev/null | wc -l | tr -d ' ')
        kb=$(size_kb "$CAPTURE_DIR"); freed_kb=$((freed_kb + kb))
        if [ "$n" -gt 0 ]; then
          run "delete $n capture(s) ($(human "$kb")) from $CAPTURE_DIR" \
              find "$CAPTURE_DIR" -maxdepth 1 -type f -name '*.jpg' -delete
          [ "$DRY" = 1 ] || did "deleted $n capture(s), freed $(human "$kb")"
        else
          skip "no *.jpg in $CAPTURE_DIR"
        fi
      else
        skip "$CAPTURE_DIR does not exist"
      fi ;;
  esac
fi

# ---------------------------------------------------------------------------------------------------
# 2. LOGS  -- so the next `journalctl -u robot.service` starts at line 1 of the new code
# ---------------------------------------------------------------------------------------------------
if [ "$WANT_LOGS" = 1 ]; then
  step "2. Logs  (so the next run's journal is ONLY the new code)"
  if command -v journalctl >/dev/null 2>&1; then
    # Rotate first, then vacuum to nothing: --vacuum-time only removes ARCHIVED journals, so without the
    # rotate the still-active file survives and you keep reading yesterday's bug in today's log.
    run "rotate + vacuum the systemd journal" sudo journalctl --rotate >/dev/null 2>&1 || true
    run "vacuum archived journals to 1s" sudo journalctl --vacuum-time=1s >/dev/null 2>&1 || true
    [ "$DRY" = 1 ] || did "systemd journal rotated + vacuumed (robot.service history cleared)"
  else
    skip "no journalctl on this box"
  fi
  # The --detach launcher's own log, and any log pulled/left in the repo.
  for f in "$STATE/robot.log" "$REPO"/*.log; do
    [ -e "$f" ] || continue
    kb=$(size_kb "$f"); freed_kb=$((freed_kb + kb))
    run "delete $f ($(human "$kb"))" rm -f "$f"
    [ "$DRY" = 1 ] || did "removed $(basename "$f")  ($(human "$kb"))"
  done
fi

# ---------------------------------------------------------------------------------------------------
# 3. CACHES  -- pure reclaimed disk, nothing here is state
# ---------------------------------------------------------------------------------------------------
if [ "$WANT_CACHE" = 1 ]; then
  step "3. Caches  (apt / pip / bytecode / thumbnails -- all regenerate themselves)"
  if command -v apt-get >/dev/null 2>&1; then
    kb=$(size_kb /var/cache/apt/archives); freed_kb=$((freed_kb + kb))
    run "apt-get clean ($(human "$kb") of downloaded .debs)" sudo apt-get clean >/dev/null 2>&1 || true
    run "apt-get autoremove unused packages" sudo apt-get -y autoremove --purge >/dev/null 2>&1 || true
    [ "$DRY" = 1 ] || did "apt cache cleaned + orphaned packages removed  ($(human "$kb"))"
  fi
  for d in "$HOME/.cache/pip" "$HOME/.cache/thumbnails" "$HOME/.cache/pypoetry"; do
    [ -d "$d" ] || continue
    kb=$(size_kb "$d"); freed_kb=$((freed_kb + kb))
    run "delete $d ($(human "$kb"))" rm -rf "$d"
    [ "$DRY" = 1 ] || did "cleared $(basename "$d") cache  ($(human "$kb"))"
  done
  # Stale .pyc from an OLD checkout is a real failure mode, not just disk: a pull that DELETES a module
  # leaves its .pyc behind, and on some layouts Python will still import it -- so the robot runs code that
  # no longer exists in git. Clearing them makes "what's running" match "what's checked out".
  #
  # SKIP THE VENV. brain/.venv/lib/.../site-packages is full of __pycache__ too -- on a real robot that is
  # ~478 of the 480 directories here -- but it is NOT a git checkout, so none of it can be stale in the sense
  # above. Deleting it fixes nothing and makes the next start recompile every dependency. Only OUR code
  # (brain/, scripts/, tools/) can go out of sync with git, so only our code gets swept.
  # ONE prune expression, used for both the count and the delete, so the two can never disagree about what
  # is in scope. The trailing -prune on the match itself stops find descending into a directory it is about
  # to remove (which is what produces spurious "No such file or directory" noise).
  pyc_find() {                      # $@ = the action (-print, or -exec rm -rf {} +)
    find "$REPO" \( -path "$REPO/brain/.venv" -o -name node_modules \) -prune -o \
         -name '__pycache__' -type d -prune "$@" 2>/dev/null
  }
  pyc=$(pyc_find -print | wc -l | tr -d ' ')
  if [ "$pyc" -gt 0 ]; then
    run "delete $pyc __pycache__ dir(s) of OUR code (brain/.venv left alone)" \
        pyc_find -exec rm -rf {} +
    [ "$DRY" = 1 ] || did "removed $pyc __pycache__ dir(s) -- no stale bytecode from the old checkout"
  fi
  if command -v journalctl >/dev/null 2>&1 && [ "$WANT_LOGS" = 0 ]; then
    # Only when we did NOT already blow the journal away above.
    run "cap the systemd journal at 100M" sudo journalctl --vacuum-size=100M >/dev/null 2>&1 || true
    [ "$DRY" = 1 ] || did "journal capped at 100M"
  fi
fi

# ---------------------------------------------------------------------------------------------------
# 4. DATABASE  -- the destructive one. Backup, confirm, then wipe.
# ---------------------------------------------------------------------------------------------------
if [ "$WANT_DB" = 1 ]; then
  step "4. ${RED}Memory database${RST}  (faces, enrolments, remembered facts, the robot's identity)"
  if [ ! -f "$DB" ]; then
    skip "$DB does not exist -- nothing to wipe"
  else
    faces="?"; people="?"
    if command -v sqlite3 >/dev/null 2>&1; then
      faces=$(sqlite3 "$DB" 'select count(*) from faces' 2>/dev/null || echo '?')
      people=$(sqlite3 "$DB" 'select count(distinct name) from faces' 2>/dev/null || echo '?')
    fi
    note "   about to delete: $DB"
    note "   it currently holds ${B}${faces} face photo(s) across ${people} person/people${RST}"
    note "   ${YEL}re-enrolling afterwards needs ~/people_named/ + enroll_all.sh to still be on this box${RST}"
    if [ ! -d "$HOME/people_named" ] && [ ! -f "$REPO/enroll_all.sh" ]; then
      note "   ${RED}WARNING: neither ~/people_named/ nor enroll_all.sh is here -- you would NOT be able to"
      note "   re-enrol without copying them from your PC first (runbook section 3).${RST}"
    fi
    ok=1
    if [ "$YES" != 1 ] && [ "$DRY" != 1 ]; then
      if [ -t 0 ]; then
        printf '   type %sWIPE%s to confirm: ' "$B" "$RST"
        read -r reply
        [ "$reply" = "WIPE" ] || { ok=0; skip "not confirmed -- database left alone"; }
      else
        ok=0; skip "not a terminal and no --yes -- database left alone (never wipe unattended by accident)"
      fi
    fi
    if [ "$ok" = 1 ]; then
      # ALWAYS back up before deleting, even when confirmed. This is the one file on the robot that cannot
      # be rebuilt from git, and a backup costs a few hundred KB. Kept under ~/.localairobot/backups.
      run "back up the db to $BACKUPS/" mkdir -p "$BACKUPS"
      stamp="$(date +%Y%m%d-%H%M%S)"
      run "copy $DB -> $BACKUPS/memory-$stamp.db" cp -f "$DB" "$BACKUPS/memory-$stamp.db"
      [ "$DRY" = 1 ] || did "backed up -> $BACKUPS/memory-$stamp.db"
      kb=$(size_kb "$DB"); freed_kb=$((freed_kb + kb))
      # -wal / -shm too: leaving a write-ahead log behind next to a deleted db can resurrect rows.
      run "delete $DB (+ -wal/-shm)" rm -f "$DB" "$DB-wal" "$DB-shm"
      [ "$DRY" = 1 ] || did "database wiped -- the robot will start with no memories and no faces"
    fi
  fi
fi

# ---------------------------------------------------------------------------------------------------
note ""
if [ "$DRY" = 1 ]; then
  note "${YEL}DRY RUN complete -- nothing was deleted. Re-run without --dry-run to do it.${RST}"
  exit 0
fi
note "${GRN}Purge complete.${RST}  Reclaimed roughly $(human "$freed_kb")."
df -h / 2>/dev/null | awk 'NR==1||NR==2{print "   "$0}'
note ""
note "${B}Next steps${RST}"
if [ "$WANT_DB" = 1 ]; then
  note "  1. ${B}Re-enrol faces${RST} -- the db is empty, so Jarvis knows nobody until you do:"
  note "       cd $REPO && bash enroll_all.sh && ./scripts/rag.sh --faces"
  note "  2. Start it:  ./scripts/run_me.sh --install-boot && sudo reboot"
else
  note "  1. Start it:  ./scripts/run_me.sh --install-boot && sudo reboot"
fi
note "  Then watch the first minute:  journalctl -u robot.service -f"
if [ "$BRAIN_WAS_RUNNING" = 1 ]; then
  note ""
  note "  ${YEL}Note: the brain was running and this script stopped it -- it is still stopped.${RST}"
  note "  ${YEL}Start it again with the line above (or: sudo systemctl start robot.service).${RST}"
fi
exit 0
