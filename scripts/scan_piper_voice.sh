#!/usr/bin/env bash
# scan_piper_voice.sh -- audition Piper voices for an INDIAN-ACCENTED robot, then set the winner.
#
# Piper has NO en_IN locale, so an Indian accent has to come from one of two places:
#   * a SINGLE-speaker voice whose source dataset is South Asian -- a drop-in ROBOT_PIPER_VOICE change
#   * a MULTI-speaker corpus with documented Indian speakers     -- needs a speaker id (see the warning below)
# This downloads the candidates next to your current voice, speaks the SAME lines through each one on the
# robot's own audio path (piper -> aplay, exactly what main.py's speak() does), and prints the config change
# for whichever you liked. It never touches config/robot.env unless you ask with --apply.
#
#   ./scripts/scan_piper_voice.sh                     # LIST the candidates + what is already downloaded (no network)
#   ./scripts/scan_piper_voice.sh --all               # fetch what is missing, then audition every one
#   ./scripts/scan_piper_voice.sh l2arctic            # audition one (downloads it first if needed)
#   ./scripts/scan_piper_voice.sh l2arctic kusal      # ...or several
#   ./scripts/scan_piper_voice.sh --text "Good evening. It is raining."   # your own sentence
#   ./scripts/scan_piper_voice.sh --apply kusal       # write ROBOT_PIPER_VOICE into config/robot.env
#   ./scripts/scan_piper_voice.sh --clean             # delete ONLY the voices this script downloaded
#
# !! MULTI-SPEAKER VOICES DO NOT WORK YET. brain/main.py synthesizes with `piper -m <voice>` and no
#    --speaker flag, so it always gets speaker 0 -- which on l2arctic is TXHC (a Mandarin-L1 speaker) and on
#    arctic is awb (Scottish). Neither is what you auditioned. --apply refuses those until the brain learns a
#    ROBOT_PIPER_SPEAKER key. The single-speaker candidates below work TODAY with no code change.
#
# Each voice is a 60-73 MB download from huggingface.co. --clean gets the disk back.
set -uo pipefail
cd "$(dirname "$0")/.."   # repo root (= ~/LocalAiRobot on the Pi)
ENV_FILE="config/robot.env"

if [ -t 1 ]; then BLD=$'\033[1m'; GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; DIM=$'\033[2m'; RST=$'\033[0m'
else BLD=''; GRN=''; YEL=''; DIM=''; RST=''; fi

# ---------------------------------------------------------------------------------------------------------
# The candidates.   name | quality | download MB | note | speakers("id:label:accent,...", empty = single-speaker)
#
# Accent labels are only as good as their source. L2-ARCTIC publishes each speaker's first language, and CMU
# ARCTIC's ksp/axb have been documented as Indian English for twenty years -- those are facts. The two
# single-speaker entries are inferences from the DATASET NAME ONLY; Piper ships no accent label for them,
# which is exactly why this script plays them to you instead of me telling you what they sound like.
# ---------------------------------------------------------------------------------------------------------
CANDIDATES=(
"l2arctic|medium|73|L2-ARCTIC -- a corpus of NON-NATIVE English, four of whose speakers are native Hindi. The most explicitly Indian-accented thing Piper has.|10:ASI:Hindi L1 (m),19:RRBI:Hindi L1 (m),2:SVBI:Hindi L1 (f),9:TNI:Hindi L1 (f)"
"arctic|medium|73|CMU ARCTIC -- ksp and axb are the long-documented Indian-English speakers; the other three read South Asian but are less well labelled.|3:ksp:Indian (m),15:axb:Indian (f),13:aup:South Asian (m),17:gka:South Asian (m),12:slp:South Asian (f)"
"kusal|medium|60|Single speaker. Dataset is named 'kusal' (a Sinhalese name) but Piper publishes NO accent label -- listen and judge. Drop-in, no code change.|"
"reza_ibrahim|medium|60|Single speaker, South-Asian/Middle-Eastern dataset name, again with NO published accent label. Drop-in, no code change.|"
"lessac|medium|60|YOUR CURRENT VOICE -- auditioned last so you hear the difference rather than guess at it.|"
)

# What it says. Two lines the robot genuinely utters, so you are judging the accent on the robot's own
# cadence (a greeting with a name, and a weather line) rather than on a neutral test sentence.
LINES=(
  "Hello, how are you doing, Ruh-mesh?"
  "Good evening. It is eighteen degrees and raining, so take an umbrella."
)
PAUSE=0.7

PIPER="${ROBOT_PIPER_BIN:-$HOME/piper/piper/piper}"
PIPER="${PIPER/#\~/$HOME}"
CUR_VOICE="${ROBOT_PIPER_VOICE:-$HOME/piper/en_US-lessac-medium.onnx}"
CUR_VOICE="${CUR_VOICE/#\~/$HOME}"
VOICE_DIR="$(dirname "$CUR_VOICE")"           # download beside the voice you already have
MANIFEST="$VOICE_DIR/.scan_piper_voice.downloaded"
HF="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US"

# ---- argument parsing ------------------------------------------------------------------------------------
WANT=(); ALL=0; APPLY=""; CLEAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"; exit 0 ;;
    --all)     ALL=1 ;;
    --clean)   CLEAN=1 ;;
    --apply)   shift; APPLY="${1:-}"; [ -n "$APPLY" ] || { echo "scan_piper_voice: --apply needs a voice name" >&2; exit 2; } ;;
    --apply=*) APPLY="${1#--apply=}" ;;
    --text)    shift; [ -n "${1:-}" ] || { echo "scan_piper_voice: --text needs a sentence" >&2; exit 2; }; LINES=("$1") ;;
    --text=*)  LINES=("${1#--text=}") ;;
    --pause)   shift; PAUSE="${1:-0.7}" ;;
    --*)       echo "scan_piper_voice: unknown option '$1'" >&2; exit 2 ;;
    *)         WANT+=("$1") ;;
  esac
  shift
done

# ---- helpers ---------------------------------------------------------------------------------------------
entry_for() {   # entry_for NAME -> echoes the candidate row, or nothing
  local want="$1" e
  for e in "${CANDIDATES[@]}"; do
    [ "${e%%|*}" = "$want" ] && { printf '%s\n' "$e"; return 0; }
  done
  return 1
}

fetch() {   # fetch URL DEST -- atomic: an interrupted 64MB transfer must never leave a truncated .onnx that
            # the [ -s ] guard then treats as done forever (that is a silently mute robot; install_stack_pi5.sh
            # was bitten by exactly this and fixed it the same way).
  local url="$1" dest="$2"
  [ -s "$dest" ] && return 0
  echo "   downloading $(basename "$dest") ..."
  if curl -fL --progress-bar -o "${dest}.part" "$url"; then
    mv "${dest}.part" "$dest"
    printf '%s\n' "$dest" >> "$MANIFEST"
    return 0
  fi
  rm -f "${dest}.part"
  echo "   ${YEL}!! download failed:${RST} $url" >&2
  return 1
}

ensure() {   # ensure NAME QUALITY -> echoes the .onnx path on success
  local name="$1" q="$2" stem="en_US-$1-$2" onnx
  onnx="$VOICE_DIR/en_US-$1-$2.onnx"
  mkdir -p "$VOICE_DIR"
  fetch "$HF/$name/$q/$stem.onnx"      "$onnx"      || return 1
  fetch "$HF/$name/$q/$stem.onnx.json" "$onnx.json" || return 1
  printf '%s\n' "$onnx"
}

say() {   # say VOICE_PATH [SPEAKER_ID] -- one audition, all LINES, through the robot's own audio path
  local voice="$1" spk="${2:-}" wav rc line
  local -a extra=()
  [ -n "$spk" ] && extra=(--speaker "$spk")
  for line in "${LINES[@]}"; do
    wav="${ROBOT_TMPDIR:-/tmp}/scanvoice-$$-$RANDOM.wav"
    if ! "$PIPER" -m "$voice" ${extra[@]+"${extra[@]}"} --output_file "$wav" <<<"$line" 2>/dev/null; then
      echo "   ${YEL}!! piper failed on this voice${RST}"; rm -f "$wav"; return 1
    fi
    aplay -q "$wav" 2>/dev/null || echo "   ${YEL}!! aplay failed${RST} (check ~/.asoundrc, 'aplay -l')"
    rm -f "$wav"
    sleep "$PAUSE"
  done
  return 0
}

# ---- --clean ---------------------------------------------------------------------------------------------
if [ "$CLEAN" = 1 ]; then
  [ -f "$MANIFEST" ] || { echo "Nothing to clean -- this script has not downloaded anything into $VOICE_DIR."; exit 0; }
  n=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # Never delete the voice the robot is actually configured to speak with, even if we fetched it.
    case "$f" in "$CUR_VOICE"|"$CUR_VOICE.json") echo "   keeping $(basename "$f") -- it is your current ROBOT_PIPER_VOICE"; continue ;; esac
    [ -e "$f" ] && { rm -f "$f"; echo "   removed $(basename "$f")"; n=$((n+1)); }
  done < "$MANIFEST"
  rm -f "$MANIFEST"
  echo "${GRN}Cleaned $n file(s) from $VOICE_DIR.${RST}"
  exit 0
fi

# ---- preflight -------------------------------------------------------------------------------------------
[ -x "$PIPER" ] || { echo "scan_piper_voice: piper not found at $PIPER" >&2
                     echo "  set ROBOT_PIPER_BIN, or run ./scripts/install_stack_pi5.sh" >&2; exit 1; }
HELP="$("$PIPER" --help 2>&1 || true)"
case "$HELP" in *--speaker*) SPK_OK=1 ;; *) SPK_OK=0 ;; esac

# ---- --apply ---------------------------------------------------------------------------------------------
if [ -n "$APPLY" ]; then
  row="$(entry_for "$APPLY")" || { echo "scan_piper_voice: '$APPLY' is not one of: $(for e in "${CANDIDATES[@]}"; do printf '%s ' "${e%%|*}"; done)" >&2; exit 2; }
  IFS='|' read -r a_name a_q a_mb a_note a_spk <<<"$row"
  if [ -n "$a_spk" ]; then
    echo "${YEL}Refusing to apply '$a_name'.${RST}"
    echo "  It is a MULTI-SPEAKER model, and brain/main.py speaks with 'piper -m <voice>' and no --speaker"
    echo "  flag -- so the robot would use speaker 0, not the one you auditioned. Applying this needs a"
    echo "  ROBOT_PIPER_SPEAKER config key wired into speak() first."
    exit 3
  fi
  [ -f "$ENV_FILE" ] || { echo "scan_piper_voice: $ENV_FILE not found (run this from the repo on the robot)" >&2; exit 1; }
  path="$(ensure "$a_name" "$a_q")" || exit 1
  # Replace only the VALUE, leaving any trailing comment on that line intact (same sed shape the
  # BENCH_SCRIPTS debug-log blocks use, so ON/OFF round-trips byte-identically).
  if grep -qE '^ROBOT_PIPER_VOICE=' "$ENV_FILE"; then
    sed -i -E "s|^(ROBOT_PIPER_VOICE=)[^[:space:]]*|\1${path}|" "$ENV_FILE"
  else
    printf 'ROBOT_PIPER_VOICE=%s\n' "$path" >> "$ENV_FILE"
  fi
  echo "${GRN}Set${RST} ROBOT_PIPER_VOICE = $path"
  grep -nE '^ROBOT_PIPER_VOICE=' "$ENV_FILE" | sed 's/^/   /'
  echo
  echo "   Apply it:   ./scripts/run_me.sh --reload"
  echo "   Re-tune the say-as for it:   ./scripts/pronounce_my_name.sh Ruh-mesh --name=Ramesh"
  echo "   ${DIM}(a new voice usually needs a different respelling -- the old one was tuned for lessac)${RST}"
  exit 0
fi

# ---- decide what to audition -----------------------------------------------------------------------------
if [ "$ALL" = 1 ]; then
  WANT=(); for e in "${CANDIDATES[@]}"; do WANT+=("${e%%|*}"); done
fi

# ---- bare invocation: just list ---------------------------------------------------------------------------
if [ "${#WANT[@]}" -eq 0 ]; then
  echo
  echo "${BLD}Piper has no en_IN voice.${RST} These are the ways to get an Indian accent out of it."
  echo "${DIM}Voices live in $VOICE_DIR   |   currently speaking with $(basename "$CUR_VOICE")${RST}"
  echo
  for e in "${CANDIDATES[@]}"; do
    IFS='|' read -r name q mb note spk <<<"$e"
    onnx="$VOICE_DIR/en_US-$name-$q.onnx"
    if [ -s "$onnx" ]; then state="${GRN}downloaded${RST}"; else state="${DIM}${mb} MB to fetch${RST}"; fi
    if [ -n "$spk" ]; then kind="${YEL}multi-speaker${RST}"; else kind="${GRN}single speaker${RST}"; fi
    printf '  %s%-14s%s  %s  (%s)\n' "$BLD" "$name" "$RST" "$kind" "$state"
    printf '     %s\n' "$note" | fold -s -w 100 | sed '2,$s/^/     /'
    if [ -n "$spk" ]; then
      IFS=',' read -ra sps <<<"$spk"
      printf '     speakers:'
      for s in "${sps[@]}"; do IFS=':' read -r sid slab sacc <<<"$s"; printf ' %s=%s %s ' "$sid" "$slab" "$sacc"; done
      printf '\n'
    fi
    echo
  done
  echo "  Hear them:   ./scripts/scan_piper_voice.sh --all        ${DIM}# or name just the ones you want${RST}"
  echo "  Keep one:    ./scripts/scan_piper_voice.sh --apply kusal"
  echo "  Free space:  ./scripts/scan_piper_voice.sh --clean"
  echo
  [ "$SPK_OK" = 1 ] || echo "  ${YEL}Note:${RST} this piper build has no --speaker flag, so the multi-speaker rows will only play speaker 0."
  exit 0
fi

# ---- audition --------------------------------------------------------------------------------------------
echo
echo "${BLD}Auditioning ${#WANT[@]} voice(s).${RST} Each says:"
for l in "${LINES[@]}"; do echo "   ${DIM}\"$l\"${RST}"; done
echo

failed=0
for want in "${WANT[@]}"; do
  row="$(entry_for "$want")" || { echo "${YEL}skipping '$want'${RST} -- not a candidate (run with no arguments to see the list)"; failed=1; continue; }
  IFS='|' read -r name q mb note spk <<<"$row"
  echo "${BLD}== $name ==${RST}  $note"
  path="$(ensure "$name" "$q")" || { failed=1; echo; continue; }

  if [ -z "$spk" ]; then
    echo "   ${DIM}single speaker${RST}"
    say "$path" || failed=1
    echo "   ${GRN}keep it:${RST} ./scripts/scan_piper_voice.sh --apply $name"
  elif [ "$SPK_OK" = 0 ]; then
    echo "   ${YEL}this piper build has no --speaker flag -- playing speaker 0 only, which is NOT the Indian one${RST}"
    say "$path" || failed=1
  else
    IFS=',' read -ra sps <<<"$spk"
    for s in "${sps[@]}"; do
      IFS=':' read -r sid slab sacc <<<"$s"
      echo "   ${BLD}speaker $sid${RST} -- $slab, $sacc"
      say "$path" "$sid" || { failed=1; break; }
    done
    echo "   ${YEL}not usable yet${RST} -- main.py passes no --speaker, so the robot would get speaker 0."
  fi
  echo
done

echo "${DIM}Done. --apply <name> sets a single-speaker voice; --clean removes what was downloaded.${RST}"
exit $failed
