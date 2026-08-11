#!/usr/bin/env bash
# scan_piper_voice.sh -- browse every accent Piper can do, audition them in the robot's own voice, keep one.
#
# Piper has no per-accent voice list, and no en_IN locale at all. What it actually has is a mix of
# single-speaker voices (one accent each) and multi-speaker corpora where the accent lives in the SPEAKER ID
# -- 109 of them in en_GB-vctk alone. This flattens both into one catalog you can browse by accent, plays
# candidates through piper -> aplay (exactly what main.py's speak() does), and writes the winner to
# config/robot.env. It never touches config unless you ask with --apply.
#
#   ./scripts/scan_piper_voice.sh                      # summary: every accent + how many options (no network)
#   ./scripts/scan_piper_voice.sh --all                # the FULL catalog, every voice and speaker, by accent
#   ./scripts/scan_piper_voice.sh --accent scottish    # audition an accent (downloads what it needs)
#   ./scripts/scan_piper_voice.sh arctic               # audition one voice (all its listed speakers)
#   ./scripts/scan_piper_voice.sh arctic:15            # audition ONE speaker -- voice:id
#   ./scripts/scan_piper_voice.sh vctk --speakers all  # walk every speaker in a model (Ctrl-C to stop)
#   ./scripts/scan_piper_voice.sh --text "Good evening."   # your own sentence
#   ./scripts/scan_piper_voice.sh --apply arctic:15    # keep it: writes VOICE **and** SPEAKER
#   ./scripts/scan_piper_voice.sh --clean              # delete ONLY what this script downloaded
#
# --apply writes ROBOT_PIPER_VOICE and ROBOT_PIPER_SPEAKER together, and CLEARS the speaker when you go back
# to a single-speaker voice -- a leftover id is passed to a model that has no such speaker. On a
# multi-speaker voice it INSISTS on an id, because Piper silently falls back to speaker 0 and speaker 0 is
# almost never the accent you picked (en_US-arctic 0 is awb, a Scot; en_GB-vctk 0 is p239, English).
#
# ACCENT LABELS: L2-ARCTIC publishes each speaker's first language and CMU ARCTIC's ksp/axb have been
# documented as Indian English for two decades -- those are solid. The VCTK regional labels come from that
# corpus's widely-reproduced speaker-info table and are a STARTING POINT, not a promise. kusal and
# reza_ibrahim have no published accent label at all; they are here on the strength of their dataset name.
# Which is the entire point of a script that plays them to you rather than telling you what they sound like.
#
# Voices are 60-74 MB each. Auditioning a whole accent can be a big download -- it always says how big
# first, and asks. --clean gets the disk back.
set -uo pipefail
cd "$(dirname "$0")/.."   # repo root (= ~/LocalAiRobot on the Pi)
ENV_FILE="config/robot.env"

if [ -t 1 ]; then BLD=$'\033[1m'; GRN=$'\033[1;32m'; YEL=$'\033[1;33m'; CYN=$'\033[1;36m'; DIM=$'\033[2m'; RST=$'\033[0m'
else BLD=''; GRN=''; YEL=''; CYN=''; DIM=''; RST=''; fi

# ---------------------------------------------------------------------------------------------------------
# THE CATALOG.   name | locale | quality | MB | accent | note | speakers("id:label:accent; id:label:accent; ...")
#
# `accent` is the whole voice's accent for single-speaker models, or `mixed` when the accent is per-speaker.
# Speakers are separated by SEMICOLONS, not commas -- the region labels contain commas ("scottish (f, Fife)")
# and a comma separator silently truncated every one of them at the comma.
# Sizes were measured from the CDN, not guessed. Everything here is en_US or en_GB -- those are the only
# English locales Piper publishes.
# ---------------------------------------------------------------------------------------------------------
CATALOG=(
# ---- multi-speaker corpora: where the accents actually live -------------------------------------------
"vctk|en_GB|medium|73|mixed|VCTK -- 109 speakers spanning the British Isles plus American, Canadian, Indian, Australian, South African and NZ. The widest accent range Piper has. Listed below is a curated pick; --speakers all walks all 109.|94:p234:scottish (f, West Dumfries);102:p237:scottish (m, Fife);98:p241:scottish (m, Perth);79:p252:scottish (m, Edinburgh);80:p262:scottish (f, Edinburgh);97:p245:irish (m, Dublin);58:p288:irish (f, Dublin);56:p295:irish (f, Dublin);17:p238:northern-irish (f, Belfast);6:p261:northern-irish (f, Belfast);61:p292:northern-irish (m, Belfast);88:p253:welsh (f, Cardiff);87:p248:indian (f);92:p251:indian (m);26:p376:indian (m);39:p294:american (f, San Francisco);68:p299:american (f, California);75:p345:american (m, Florida);105:p302:canadian (m, Montreal);99:p303:canadian (f, Toronto);35:p314:south-african (f, Cape Town);31:p323:south-african (f, Pretoria);32:p347:south-african (m, Johannesburg);71:p326:australian (m, Sydney);25:p374:australian (m);42:p335:nz (f);82:p227:northern-english (m, Cumbria);1:p236:northern-english (f, Manchester);77:p269:northern-english (f, Newcastle);12:p270:northern-english (m, Yorkshire);104:p256:british (m, Birmingham)"
"l2arctic|en_US|medium|73|mixed|L2-ARCTIC -- NON-NATIVE English, 24 speakers across six first languages, four speakers each (2m/2f). Every speaker's L1 is published, so these accent labels are exact.|10:ASI:indian (m);19:RRBI:indian (m);2:SVBI:indian (f);9:TNI:indian (f);21:ABA:arabic (m);23:SKA:arabic (f);18:YBAA:arabic (m);3:ZHAA:arabic (f);20:BWC:mandarin (m);12:LXC:mandarin (f);13:NCC:mandarin (f);0:TXHC:mandarin (m);11:HJK:korean (f);16:HKK:korean (m);15:YDCK:korean (f);14:YKWK:korean (m);22:EBVS:spanish (m);6:ERMS:spanish (m);7:MBMPS:spanish (f);17:NJS:spanish (f);8:HQTV:vietnamese (m);4:PNV:vietnamese (f);1:THV:vietnamese (f);5:TLV:vietnamese (m)"
"arctic|en_US|medium|73|mixed|CMU ARCTIC -- 18 speakers, mostly American. ksp and axb are the long-documented Indian-English pair; aup, gka and slp also read South Asian but are less firmly labelled.|3:ksp:indian (m);15:axb:indian (f);13:aup:indian (m, less certain);17:gka:indian (m, less certain);12:slp:indian (f, less certain);0:awb:scottish (m);8:jmk:canadian (m);2:slt:american (f);4:clb:american (f);7:bdl:american (m);1:rms:american (m)"
"aru|en_GB|medium|73|british|ARU -- 12 British speakers. The corpus does not publish per-speaker regions, so they are all just British here; listen for the one you like.|0:03:british;1:06:british;2:10:british;3:01:british;4:09:british;5:08:british;6:11:british;7:05:british;8:12:british;9:02:british;10:07:british;11:04:british"
"semaine|en_GB|medium|73|british|SEMAINE -- four British characters built to sound different from each other in TEMPERAMENT (Prudence is even, Spike is abrasive, Obadiah is gloomy, Poppy is bright), not in region.|0:prudence:british (f);1:spike:british (m);2:obadiah:british (m);3:poppy:british (f)"
# ---- single-speaker: Indian / South Asian --------------------------------------------------------------
"kusal|en_US|medium|60|indian|Single speaker. The dataset is named 'kusal' (a Sinhalese name) but Piper publishes NO accent label -- unverified, listen and judge. Drop-in: no speaker id needed.|"
"reza_ibrahim|en_US|medium|60|indian|Single speaker, South-Asian/Middle-Eastern dataset name, again with NO published accent label. Unverified. Drop-in.|"
# ---- single-speaker: British ---------------------------------------------------------------------------
"alan|en_GB|medium|60|british|Single speaker, British male.|"
"cori|en_GB|medium|60|british|Single speaker, British female. Also published at 'high' quality.|"
"jenny_dioco|en_GB|medium|60|british|Single speaker, British female.|"
"southern_english_female|en_GB|low|60|southern-english|Single speaker, Southern English female. The accent IS the voice name.|"
"northern_english_male|en_GB|medium|60|northern-english|Single speaker, Northern English male. The accent IS the voice name.|"
"alba|en_GB|medium|60|scottish|Single speaker. 'Alba' is the Gaelic name for Scotland -- Piper's dedicated Scottish voice.|"
# ---- single-speaker: American --------------------------------------------------------------------------
"lessac|en_US|medium|60|american|YOUR ROBOT'S CURRENT VOICE, unless you have changed it. Audition it alongside the others so you hear the difference rather than guess.|"
"amy|en_US|medium|60|american|Single speaker, American female.|"
"kristin|en_US|medium|60|american|Single speaker, American female.|"
"hfc_female|en_US|medium|60|american|Single speaker, American female.|"
"ljspeech|en_US|medium|60|american|Single speaker, American female. The classic LJSpeech read-aloud voice.|"
"kathleen|en_US|low|60|american|Single speaker, American female.|"
"ryan|en_US|medium|60|american|Single speaker, American male. Also published at 'high' quality.|"
"joe|en_US|medium|60|american|Single speaker, American male.|"
"john|en_US|medium|60|american|Single speaker, American male.|"
"hfc_male|en_US|medium|60|american|Single speaker, American male.|"
"norman|en_US|medium|60|american|Single speaker, American male.|"
"bryce|en_US|medium|60|american|Single speaker, American male.|"
"sam|en_US|medium|60|american|Single speaker, American male.|"
"danny|en_US|low|60|american|Single speaker, American male.|"
)

# Display order + human labels for the accent groups.
ACCENT_ORDER="indian british southern-english northern-english scottish irish northern-irish welsh american canadian australian nz south-african arabic mandarin korean spanish vietnamese"
accent_label() {
  case "$1" in
    indian)          echo "Indian / South Asian" ;;
    british)         echo "British (general)" ;;
    southern-english) echo "Southern English" ;;
    northern-english) echo "Northern English" ;;
    scottish)        echo "Scottish" ;;
    irish)           echo "Irish" ;;
    northern-irish)  echo "Northern Irish" ;;
    welsh)           echo "Welsh" ;;
    american)        echo "American" ;;
    canadian)        echo "Canadian" ;;
    australian)      echo "Australian" ;;
    nz)              echo "New Zealand" ;;
    south-african)   echo "South African" ;;
    arabic)          echo "Arabic-accented English" ;;
    mandarin)        echo "Mandarin-accented English" ;;
    korean)          echo "Korean-accented English" ;;
    spanish)         echo "Spanish-accented English" ;;
    vietnamese)      echo "Vietnamese-accented English" ;;
    *)               echo "$1" ;;
  esac
}

# What it says. Two lines the robot genuinely utters, so you judge the accent on the robot's own cadence
# (a greeting with a name, and a weather line) rather than on a neutral test sentence.
LINES=(
  "Hello, how are you doing, Ruh-mesh?"
  "Good evening. It is eighteen degrees and raining, so take an umbrella."
)
PAUSE=0.7

PIPER="${ROBOT_PIPER_BIN:-$HOME/piper/piper/piper}";      PIPER="${PIPER/#\~/$HOME}"
CUR_VOICE="${ROBOT_PIPER_VOICE:-$HOME/piper/en_US-lessac-medium.onnx}"; CUR_VOICE="${CUR_VOICE/#\~/$HOME}"
CUR_SPK="${ROBOT_PIPER_SPEAKER:-}"
VOICE_DIR="$(dirname "$CUR_VOICE")"           # download beside the voice you already have
MANIFEST="$VOICE_DIR/.scan_piper_voice.downloaded"
HF="https://huggingface.co/rhasspy/piper-voices/resolve/main/en"

# ---- argument parsing ------------------------------------------------------------------------------------
WANT=(); ALL=0; APPLY=""; CLEAN=0; ACCENT=""; SPEAKERS=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)   awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"; exit 0 ;;
    --all)       ALL=1 ;;
    --clean)     CLEAN=1 ;;
    -y|--yes)    YES=1 ;;
    --accent)    shift; ACCENT="${1:-}"; [ -n "$ACCENT" ] || { echo "scan_piper_voice: --accent needs a name" >&2; exit 2; } ;;
    --accent=*)  ACCENT="${1#--accent=}" ;;
    --speakers)  shift; SPEAKERS="${1:-}"; [ -n "$SPEAKERS" ] || { echo "scan_piper_voice: --speakers needs ids or 'all'" >&2; exit 2; } ;;
    --speakers=*) SPEAKERS="${1#--speakers=}" ;;
    --apply)     shift; APPLY="${1:-}"; [ -n "$APPLY" ] || { echo "scan_piper_voice: --apply needs a voice (or voice:id)" >&2; exit 2; } ;;
    --apply=*)   APPLY="${1#--apply=}" ;;
    --text)      shift; [ -n "${1:-}" ] || { echo "scan_piper_voice: --text needs a sentence" >&2; exit 2; }; LINES=("$1") ;;
    --text=*)    LINES=("${1#--text=}") ;;
    --pause)     shift; PAUSE="${1:-0.7}" ;;
    --pause=*)   PAUSE="${1#--pause=}" ;;
    --*)         echo "scan_piper_voice: unknown option '$1'  (--help for usage)" >&2; exit 2 ;;
    *)           WANT+=("$1") ;;
  esac
  shift
done

# ---- catalog helpers ---------------------------------------------------------------------------------------
row_for() {   # row_for NAME -> echoes the catalog row, or nothing
  local want="$1" e
  for e in "${CATALOG[@]}"; do
    [ "${e%%|*}" = "$want" ] && { printf '%s\n' "$e"; return 0; }
  done
  return 1
}

all_names() { local e; for e in "${CATALOG[@]}"; do printf '%s\n' "${e%%|*}"; done; }

# accents_of ROW -> every accent this row can supply, one per line (the row accent, or each speaker's)
accents_of() {
  local name loc q mb acc note spk s sid slab sacc
  IFS='|' read -r name loc q mb acc note spk <<<"$1"
  if [ -z "$spk" ]; then printf '%s\n' "$acc"; return; fi
  IFS=';' read -ra sps <<<"$spk"
  for s in "${sps[@]}"; do
    IFS=':' read -r sid slab sacc <<<"$s"
    printf '%s\n' "${sacc%% *}"          # "scottish (f, Fife)" -> "scottish"
  done
}

# plan_lines -> "name|locale|quality|mb|speakerid|label|accent" for everything currently selected
plan_lines() {
  local e name loc q mb acc note spk s sid slab sacc want vname vspk
  if [ -n "$ACCENT" ]; then
    for e in "${CATALOG[@]}"; do
      IFS='|' read -r name loc q mb acc note spk <<<"$e"
      if [ -z "$spk" ]; then
        [ "$acc" = "$ACCENT" ] && printf '%s|%s|%s|%s||%s|%s\n' "$name" "$loc" "$q" "$mb" "$name" "$acc"
      else
        IFS=';' read -ra sps <<<"$spk"
        for s in "${sps[@]}"; do
          IFS=':' read -r sid slab sacc <<<"$s"
          [ "${sacc%% *}" = "$ACCENT" ] && printf '%s|%s|%s|%s|%s|%s|%s\n' "$name" "$loc" "$q" "$mb" "$sid" "$slab" "$sacc"
        done
      fi
    done
    return
  fi
  for want in "${WANT[@]}"; do
    vname="${want%%:*}"; vspk=""; [ "$want" != "$vname" ] && vspk="${want#*:}"
    e="$(row_for "$vname")" || { echo "!!unknown|$want" ; continue; }
    IFS='|' read -r name loc q mb acc note spk <<<"$e"
    if [ -n "$vspk" ]; then                       # voice:id -- exactly that speaker
      printf '%s|%s|%s|%s|%s|%s\n' "$name" "$loc" "$q" "$mb" "$vspk" "$(spk_meta "$spk" "$vspk")"
    elif [ -n "$SPEAKERS" ] && [ "$SPEAKERS" != "all" ]; then
      IFS=',' read -ra ids <<<"$SPEAKERS"        # user input IS comma-separated; the catalog is not
      for sid in "${ids[@]}"; do printf '%s|%s|%s|%s|%s|%s\n' "$name" "$loc" "$q" "$mb" "$sid" "$(spk_meta "$spk" "$sid")"; done
    elif [ "$SPEAKERS" = "all" ]; then
      printf '%s|%s|%s|%s|ALL|every speaker|%s\n' "$name" "$loc" "$q" "$mb" "$acc"
    elif [ -z "$spk" ]; then
      printf '%s|%s|%s|%s||%s|%s\n' "$name" "$loc" "$q" "$mb" "$name" "$acc"
    else
      IFS=';' read -ra sps <<<"$spk"
      for s in "${sps[@]}"; do
        IFS=':' read -r sid slab sacc <<<"$s"
        printf '%s|%s|%s|%s|%s|%s|%s\n' "$name" "$loc" "$q" "$mb" "$sid" "$slab" "$sacc"
      done
    fi
  done
}

# spk_meta "SPEAKERLIST" ID -> "label|accent" from the catalog, or "speaker ID|" when the id is not listed
# (which is legitimate -- you can name any id in the model, catalogued or not).
spk_meta() {
  local list="$1" want="$2" s sid slab sacc
  IFS=';' read -ra sps <<<"$list"
  for s in ${sps[@]+"${sps[@]}"}; do
    IFS=':' read -r sid slab sacc <<<"$s"
    [ "$sid" = "$want" ] && { printf '%s|%s\n' "$slab" "$sacc"; return; }
  done
  printf 'speaker %s|\n' "$want"
}

onnx_path() { printf '%s/%s-%s-%s.onnx\n' "$VOICE_DIR" "$2" "$1" "$3"; }   # onnx_path NAME LOCALE QUALITY

fetch() {   # fetch URL DEST -- atomic: an interrupted transfer must never leave a truncated .onnx that the
            # [ -s ] guard then treats as done forever (that is a silently mute robot; install_stack_pi5.sh
            # was bitten by exactly this and fixed it the same way).
  local url="$1" dest="$2"
  [ -s "$dest" ] && return 0
  echo "   downloading $(basename "$dest") ..."
  if curl -fL --progress-bar -o "${dest}.part" "$url"; then
    mv "${dest}.part" "$dest"; printf '%s\n' "$dest" >> "$MANIFEST"; return 0
  fi
  rm -f "${dest}.part"; echo "   ${YEL}!! download failed:${RST} $url" >&2; return 1
}

ensure() {   # ensure NAME LOCALE QUALITY -> echoes the .onnx path on success
  local name="$1" loc="$2" q="$3" stem="$2-$1-$3" onnx
  onnx="$(onnx_path "$name" "$loc" "$q")"
  mkdir -p "$VOICE_DIR"
  fetch "$HF/$loc/$name/$q/$stem.onnx"      "$onnx"      || return 1
  fetch "$HF/$loc/$name/$q/$stem.onnx.json" "$onnx.json" || return 1
  printf '%s\n' "$onnx"
}

num_speakers() {   # num_speakers ONNX_PATH -> speaker count from the sidecar json (1 if unreadable)
  local j="$1.json" n
  [ -f "$j" ] || { echo 1; return; }
  n="$(tr -d '\n' < "$j" | grep -o '"num_speakers"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | tr -dc '0-9')"
  echo "${n:-1}"
}

say() {   # say VOICE_PATH [SPEAKER_ID] -- one audition, all LINES, through the robot's own audio path
  local voice="$1" spk="${2:-}" wav line
  local -a extra=()
  [ -n "$spk" ] && extra=(--speaker "$spk")
  for line in "${LINES[@]}"; do
    wav="${ROBOT_TMPDIR:-/tmp}/scanvoice-$$-$RANDOM.wav"
    if ! "$PIPER" -m "$voice" ${extra[@]+"${extra[@]}"} --output_file "$wav" <<<"$line" 2>/dev/null; then
      echo "   ${YEL}!! piper failed on this voice${RST}"; rm -f "$wav"; return 1
    fi
    aplay -q "$wav" 2>/dev/null || echo "   ${YEL}!! aplay failed${RST} (check ~/.asoundrc, 'aplay -l')"
    rm -f "$wav"; sleep "$PAUSE"
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
  a_want="${APPLY%%:*}"; a_id=""; [ "$APPLY" != "$a_want" ] && a_id="${APPLY#*:}"
  row="$(row_for "$a_want")" || { echo "scan_piper_voice: '$a_want' is not in the catalog. Run with no arguments to see it." >&2; exit 2; }
  IFS='|' read -r a_name a_loc a_q a_mb a_acc a_note a_spk <<<"$row"

  if [ -n "$a_spk" ] && [ -z "$a_id" ]; then
    echo "${YEL}'$a_name' is a MULTI-SPEAKER voice -- tell me WHICH speaker.${RST}"
    echo "  Piper falls back to speaker 0 when no id is given, and that is almost never the accent you"
    echo "  auditioned. Pick one from:"
    IFS=';' read -ra sps <<<"$a_spk"
    for s in "${sps[@]}"; do IFS=':' read -r sid slab sacc <<<"$s"; printf '     %s--apply %s:%s%s   %-6s %s\n' "$GRN" "$a_name" "$sid" "$RST" "$slab" "$sacc"; done
    exit 3
  fi
  if [ -z "$a_spk" ] && [ -n "$a_id" ]; then
    echo "scan_piper_voice: '$a_name' is a single-speaker voice -- drop the ':$a_id'." >&2; exit 2
  fi
  if [ -n "$a_id" ] && ! printf '%s' "$a_id" | grep -qE '^[0-9]+$'; then
    echo "scan_piper_voice: speaker id '$a_id' is not a number." >&2; exit 2
  fi
  [ -f "$ENV_FILE" ] || { echo "scan_piper_voice: $ENV_FILE not found (run this from the repo on the robot)" >&2; exit 1; }
  path="$(ensure "$a_name" "$a_loc" "$a_q")" || exit 1
  if [ -n "$a_id" ]; then
    total="$(num_speakers "$path")"
    if [ "$a_id" -ge "$total" ] 2>/dev/null; then
      echo "scan_piper_voice: '$a_name' has $total speakers (0-$((total-1))); $a_id is out of range." >&2; exit 2
    fi
    [ "$SPK_OK" = 1 ] || echo "  ${YEL}Warning:${RST} this piper build reported no --speaker flag; the id may be ignored at runtime."
  fi

  set_kv() {   # replace KEY's VALUE in place, leaving any trailing comment intact; append if absent
    local key="$1" val="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
      sed -i -E "s|^(${key}=)[^[:space:]]*|\1${val}|" "$ENV_FILE"
    else
      printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
  }
  set_kv ROBOT_PIPER_VOICE "$path"
  # ALWAYS write the speaker line, even when clearing it. Going from a multi-speaker voice back to a
  # single-speaker one has to remove the old id, or it gets handed to a model that has no such speaker.
  set_kv ROBOT_PIPER_SPEAKER "$a_id"

  echo "${GRN}Applied${RST} $a_name${a_id:+ speaker $a_id}"
  grep -nE '^ROBOT_PIPER_(VOICE|SPEAKER)=' "$ENV_FILE" | sed 's/^/   /'
  echo
  echo "   Reload the brain:   ./scripts/run_me.sh --reload"
  echo "   Re-tune the say-as: ./scripts/pronounce_my_name.sh Ruh-mesh --name=Ramesh"
  echo "   ${DIM}(a new voice usually needs a different respelling -- Ruh-mesh was tuned for lessac)${RST}"
  echo "   Match the earcons:  ./scripts/generate_sounds.sh --force"
  echo "   ${DIM}(otherwise 'Yes?' and 'Thinking...' keep the OLD voice and clash with every reply)${RST}"
  exit 0
fi

# ---- bare invocation: the accent summary ------------------------------------------------------------------
if [ "${#WANT[@]}" -eq 0 ] && [ -z "$ACCENT" ] && [ "$ALL" = 0 ]; then
  echo
  echo "${BLD}Every accent Piper can do.${RST}  ${DIM}(no en_IN locale exists -- Indian comes from the corpora below)${RST}"
  echo "${DIM}Voices live in $VOICE_DIR  |  speaking with $(basename "$CUR_VOICE")${CUR_SPK:+ speaker $CUR_SPK}${RST}"
  echo
  for acc in $ACCENT_ORDER; do
    voices=""; n=0
    for e in "${CATALOG[@]}"; do
      if accents_of "$e" | grep -qx "$acc"; then voices="$voices ${e%%|*}"; fi
    done
    [ -n "$voices" ] || continue
    n=$(printf '%s\n' $voices | wc -l | tr -d ' ')
    printf '  %s%-28s%s %s\n' "$CYN" "$(accent_label "$acc")" "$RST" "${DIM}${voices# }${RST}"
  done
  echo
  echo "  ${BLD}Browse:${RST}   ./scripts/scan_piper_voice.sh --all              ${DIM}# full catalog, every speaker${RST}"
  echo "  ${BLD}Listen:${RST}   ./scripts/scan_piper_voice.sh --accent scottish  ${DIM}# or: arctic, arctic:15, vctk --speakers all${RST}"
  echo "  ${BLD}Keep:${RST}     ./scripts/scan_piper_voice.sh --apply arctic:15"
  echo "  ${BLD}Free space:${RST} ./scripts/scan_piper_voice.sh --clean"
  echo
  [ "$SPK_OK" = 1 ] || echo "  ${YEL}Note:${RST} this piper build reports no --speaker flag -- multi-speaker voices will only play speaker 0."
  exit 0
fi

# ---- --all: the full catalog, grouped by accent -----------------------------------------------------------
if [ "$ALL" = 1 ] && [ "${#WANT[@]}" -eq 0 ] && [ -z "$ACCENT" ]; then
  echo
  echo "${BLD}Full Piper accent catalog.${RST} ${DIM}Nothing is downloaded or played by --all; pick something and audition it.${RST}"
  echo "${DIM}Speaking with $(basename "$CUR_VOICE")${CUR_SPK:+ speaker $CUR_SPK}${RST}"
  for acc in $ACCENT_ORDER; do
    header_done=0
    for e in "${CATALOG[@]}"; do
      IFS='|' read -r name loc q mb racc note spk <<<"$e"
      onnx="$(onnx_path "$name" "$loc" "$q")"
      [ -s "$onnx" ] && state="${GRN}[downloaded]${RST}" || state="${DIM}[${mb} MB]${RST}"
      if [ -z "$spk" ]; then
        [ "$racc" = "$acc" ] || continue
        [ "$header_done" = 0 ] && { echo; echo "${CYN}${BLD}$(accent_label "$acc")${RST}"; header_done=1; }
        printf '   %s%-24s%s %s  %s--apply %s%s\n' "$BLD" "$name" "$RST" "$state" "$GRN" "$name" "$RST"
        printf '      %s\n' "$note" | fold -s -w 96 | sed '2,$s/^/      /'
      else
        matched=""
        IFS=';' read -ra sps <<<"$spk"
        for s in "${sps[@]}"; do
          IFS=':' read -r sid slab sacc <<<"$s"
          [ "${sacc%% *}" = "$acc" ] && matched="$matched$(printf '\n      %s%-4s%s %-9s %s' "$GRN" "$sid" "$RST" "$slab" "${DIM}${sacc}${RST}")"
        done
        [ -n "$matched" ] || continue
        [ "$header_done" = 0 ] && { echo; echo "${CYN}${BLD}$(accent_label "$acc")${RST}"; header_done=1; }
        printf '   %s%-24s%s %s  %s--apply %s:<id>%s%b\n' "$BLD" "$name" "$RST" "$state" "$GRN" "$name" "$RST" "$matched"
      fi
    done
  done
  echo
  echo "${DIM}vctk lists a curated pick of its 109 speakers -- './scripts/scan_piper_voice.sh vctk --speakers all' walks them all.${RST}"
  exit 0
fi

# ---- build the audition plan ------------------------------------------------------------------------------
# Split the plan into "real" and "unknown" in ONE pass. Never via an unquoted $( ) array assignment -- the
# labels contain spaces ("scottish (f, West Dumfries)") and word-splitting shattered every one of them into
# its own bogus plan entry.
mapfile -t RAW < <(plan_lines)
PLAN=(); bad=0
for p in ${RAW[@]+"${RAW[@]}"}; do
  case "$p" in
    "!!unknown|"*) echo "${YEL}unknown voice '${p#!!unknown|}'${RST} -- run with no arguments to see the catalog"; bad=1 ;;
    *)             PLAN+=("$p") ;;
  esac
done
if [ "${#PLAN[@]}" -eq 0 ]; then
  [ -n "$ACCENT" ] && echo "${YEL}No voice offers accent '$ACCENT'.${RST} Run with no arguments to see the accents that exist."
  exit $(( bad ? 1 : 0 ))
fi

# How much will this pull? Count each MODEL once, not each speaker.
need_mb=0; seen=""
for p in "${PLAN[@]}"; do
  IFS='|' read -r name loc q mb sid slab sacc <<<"$p"
  case " $seen " in *" $name "*) continue ;; esac
  seen="$seen $name"
  [ -s "$(onnx_path "$name" "$loc" "$q")" ] || need_mb=$(( need_mb + mb ))
done
if [ "$need_mb" -gt 0 ]; then
  echo "${YEL}This will download ${need_mb} MB${RST} ($(printf '%s\n' $seen | wc -l | tr -d ' ') model(s) not yet on disk) into $VOICE_DIR."
  if [ "$YES" = 0 ]; then
    if [ -t 0 ]; then
      printf 'Continue? [y/N] '; read -r ans
      case "$ans" in y|Y|yes|YES) ;; *) echo "Aborted. (--clean frees earlier downloads; -y skips this prompt.)"; exit 0 ;; esac
    else
      echo "Not a terminal and no -y given -- refusing to download. Re-run with -y."; exit 1
    fi
  fi
fi

# ---- audition ---------------------------------------------------------------------------------------------
echo
echo "${BLD}Auditioning ${#PLAN[@]} voice(s).${RST} Each says:"
for l in "${LINES[@]}"; do echo "   ${DIM}\"$l\"${RST}"; done
echo

failed=0; last_voice=""
for p in "${PLAN[@]}"; do
  IFS='|' read -r name loc q mb sid slab sacc <<<"$p"
  if [ "$name" != "$last_voice" ]; then
    row="$(row_for "$name")"; note="$(printf '%s' "$row" | cut -d'|' -f6)"
    echo "${BLD}== $name ==${RST} ${DIM}($loc, $q)${RST}"
    printf '   %s\n' "$note" | fold -s -w 96 | sed '2,$s/^/   /'
    last_voice="$name"
  fi
  path="$(ensure "$name" "$loc" "$q")" || { failed=1; continue; }

  if [ "$sid" = "ALL" ]; then                      # --speakers all: walk every id in the model
    total="$(num_speakers "$path")"
    echo "   ${DIM}walking all $total speakers -- Ctrl-C to stop${RST}"
    i=0
    while [ "$i" -lt "$total" ]; do
      echo "   ${BLD}speaker $i${RST} ${DIM}of $((total-1))${RST}   ${GRN}--apply $name:$i${RST}"
      say "$path" "$i" || { failed=1; break; }
      i=$((i+1))
    done
  elif [ -z "$sid" ]; then
    echo "   ${DIM}single speaker${RST}   ${GRN}--apply $name${RST}"
    say "$path" || failed=1
  else
    if [ "$SPK_OK" = 0 ]; then
      echo "   ${YEL}no --speaker flag in this piper build -- you are hearing speaker 0, NOT $slab${RST}"
      say "$path" || failed=1
    else
      echo "   ${BLD}$slab${RST} ${DIM}$sacc${RST}   ${GRN}--apply $name:$sid${RST}"
      say "$path" "$sid" || failed=1
    fi
  fi
  echo
done

echo "${DIM}Done. Keep one with --apply <voice> or --apply <voice>:<id>; --clean removes what was downloaded.${RST}"
exit $(( failed || bad ))
