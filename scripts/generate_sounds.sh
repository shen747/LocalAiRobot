#!/usr/bin/env bash
# generate_sounds.sh -- synthesize the robot's SPOKEN earcons with Piper, on your PC. Creates the sound
# folders and speaks a default set into them; edit the word list at the bottom to make them your own,
# then commit. Run this AFTER ./scripts/host_setup.sh (which installs Piper + the en_US-lessac voice).
#
#   ./scripts/generate_sounds.sh          # write ONLY cues you don't already have (safe to re-run -- skips existing)
#   ./scripts/generate_sounds.sh --force  # re-synthesize ALL of them (overwrite existing)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PIPER="$HOME/piper/piper/piper"
VOICE="$HOME/piper/en_US-lessac-medium.onnx"

FORCE=0
case "${1:-}" in
  -f|--force) FORCE=1 ;;
  -h|--help)  echo "usage: $(basename "$0") [--force]   (default: skip cues that already exist)"; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (use --force)"; exit 2 ;;
esac

if [ ! -x "$PIPER" ] || [ ! -f "$VOICE" ]; then
  echo "Piper isn't installed where this expects it:" >&2
  [ -x "$PIPER" ] || echo "   missing binary: $PIPER" >&2
  [ -f "$VOICE" ] || echo "   missing voice:  $VOICE" >&2
  echo "Run  ./scripts/host_setup.sh  first (installs Piper + the voice into ~/piper)." >&2
  exit 1
fi

cd "$REPO"
mkdir -p sounds/greeting_sounds sounds/wake_word_ack sounds/ack_sounds \
         sounds/thinking_sounds sounds/thinking_still_sounds sounds/remote_ack_sounds \
         sounds/sleep_sounds sounds/error_sounds sounds/spook_sounds

# say <out.wav> "<words>": speak the words into the WAV with Piper (the robot's own voice). Skips a file
# that already exists (so re-running never regenerates/clobbers what's there) unless --force.
made=0; kept=0
say() {
  if [ -e "$1" ] && [ "$FORCE" != 1 ]; then
    printf '  kept   %-38s (exists -- --force to overwrite)\n' "$1"
    kept=$((kept + 1)); return
  fi
  if echo "$2" | "$PIPER" -m "$VOICE" -f "$1" >/dev/null 2>&1; then
    printf '  wrote  %-38s "%s"\n' "$1" "$2"
    made=$((made + 1))
  else
    echo "  !! FAILED $1 -- test Piper by hand: echo hi | \"$PIPER\" -m \"$VOICE\" -f /tmp/t.wav" >&2
  fi
}

# ---- EDIT THESE to change the words/voice (several variants per folder = random variety) ----
# greeting -- once at boot
say sounds/greeting_sounds/online.wav    "Systems online"
say sounds/greeting_sounds/ready.wav     "Ready to go"
# wake_word_ack -- the instant "hey jarvis" fires (responding to being summoned)
say sounds/wake_word_ack/yes.wav         "Yes?"
say sounds/wake_word_ack/hi.wav          "Hi"
say sounds/wake_word_ack/hello.wav       "Hello there"
# ack -- after a SPOKEN command is accepted. KEEP THESE VERY SHORT: they play IMMEDIATELY on every
# command (both backends), before any thinking, so a snappy word beats a sentence. (>=12 for variety.)
say sounds/ack_sounds/got_it.wav         "Got it"
say sounds/ack_sounds/on_it.wav          "On it"
say sounds/ack_sounds/ok.wav             "Okay"
say sounds/ack_sounds/sure.wav           "Sure thing"
say sounds/ack_sounds/yep.wav            "Yep"
say sounds/ack_sounds/roger.wav          "Roger that"
say sounds/ack_sounds/will_do.wav        "Will do"
say sounds/ack_sounds/understood.wav     "Understood"
say sounds/ack_sounds/copy_that.wav      "Copy that"
say sounds/ack_sounds/right_away.wav     "Right away"
say sounds/ack_sounds/no_problem.wav     "No problem"
say sounds/ack_sounds/you_bet.wav        "You bet"
# thinking -- the FIRST processing cue while the model runs (picked at random). On REMOTE it only
# plays if the reply is slow (>~4s) and then just once; on LOCAL it leads the re-cues below. (>=12.)
say sounds/thinking_sounds/working.wav          "Working on it"
say sounds/thinking_sounds/thinking.wav         "Let me think"
say sounds/thinking_sounds/one_moment.wav       "One moment"
say sounds/thinking_sounds/let_me_see.wav       "Let me see"
say sounds/thinking_sounds/hmm.wav              "Hmm"
say sounds/thinking_sounds/give_me_a_sec.wav    "Give me a second"
say sounds/thinking_sounds/processing.wav       "Processing"
say sounds/thinking_sounds/just_a_sec.wav       "Just a sec"
say sounds/thinking_sounds/let_me_check.wav     "Let me check"
say sounds/thinking_sounds/figuring.wav         "Let me figure this out"
say sounds/thinking_sounds/working_that_out.wav "Working that out"
say sounds/thinking_sounds/thinking2.wav        "Thinking"
# thinking_still -- RE-CUES after the first one, every ~10s while STILL busy (local only; picked at
# random). The word "still" reassures the user it hasn't stalled. (>=12.)
say sounds/thinking_still_sounds/still_working.wav    "Still working on it"
say sounds/thinking_still_sounds/still_on_it.wav      "Still on it"
say sounds/thinking_still_sounds/almost_there.wav     "Almost there"
say sounds/thinking_still_sounds/nearly_done.wav      "Nearly done"
say sounds/thinking_still_sounds/hang_on.wav          "Hang on, still thinking"
say sounds/thinking_still_sounds/still_thinking.wav   "Still thinking"
say sounds/thinking_still_sounds/bear_with_me.wav     "Bear with me"
say sounds/thinking_still_sounds/little_longer.wav    "Just a little longer"
say sounds/thinking_still_sounds/still_figuring.wav   "Still figuring it out"
say sounds/thinking_still_sounds/almost_got_it.wav    "Almost got it"
say sounds/thinking_still_sounds/still_processing.wav "Still processing"
say sounds/thinking_still_sounds/wont_be_long.wav     "Won't be long"
# remote_ack -- a phone/HTTP command just arrived (played when the remote turn starts, so if you're
# mid-chat the robot audibly excuses itself to go handle it).
say sounds/remote_ack_sounds/remote_got_it.wav   "Got a remote command, on it now"
say sounds/remote_ack_sounds/remote_incoming.wav "Incoming request, working on that"
say sounds/remote_ack_sounds/remote_received.wav "Received a remote command, one moment"
# sleep -- spoken when a CONVERSATION ends on silence, then it goes back to wake-word-only.
# KEEP THESE SHORT, and do NOT put "say hey jarvis" in them. Every one of the previous twelve ended with
# some version of "just say hey jarvis to wake me", which meant the robot recited its own instruction
# manual every single time a chat trailed off -- to the same handful of people, several times a day, who
# learned it the first week. A goodbye should sound like a person leaving a room, not a kiosk.
# It is also on the BLOCKING path (main.py play_blocking) -- it holds the audio lock until the clip ends,
# so a five-second sign-off is five more seconds the robot can't hear the next visitor.
# Picked at random -- >=12 for variety. (If you ever DO want the reminder back, add one line that mentions
# it; at 1-in-12 it gets said occasionally instead of every time.)
say sounds/sleep_sounds/chat_later.wav   "Okay, chat later."
say sounds/sleep_sounds/see_ya.wav       "Okay, see ya."
say sounds/sleep_sounds/catch_you.wav    "Catch you later."
say sounds/sleep_sounds/talk_soon.wav    "Talk soon."
say sounds/sleep_sounds/later_then.wav   "Later, then."
say sounds/sleep_sounds/right_here.wav   "I'll be right here."
say sounds/sleep_sounds/napping.wav      "I'll be napping."
say sounds/sleep_sounds/standby.wav      "Back to standby."
say sounds/sleep_sounds/no_worries.wav   "No worries, take your time."
say sounds/sleep_sounds/anytime.wav      "Anytime you need me."
say sounds/sleep_sounds/all_good.wav     "All good. Bye for now."
say sounds/sleep_sounds/sounds_good.wav  "Sounds good."
# error -- obstacle / LLM error
say sounds/error_sounds/uh_oh.wav        "Uh oh"

# spook -- Halloween Mode (ROBOT_HALLOWEEN_MODE=1): the jump-scare earcon played BEFORE the spooky greeting
# is NOT a Piper voice line -- it's a set of LOUD, peak-amplitude synthetic stingers (screech/boom/shriek)
# so the scare is as loud + sudden as the hardware allows. Those are made by a separate stdlib-only script:
#     ./scripts/generate_spook_stingers.sh
# (they're committed, so a fresh clone already has them). Nothing to Piper here.

echo ">> done: $made written, $kept kept (already existed; use --force to overwrite)."
echo "   Test a clip:  aplay sounds/wake_word_ack/yes.wav"
echo "   Keep them?    git add sounds/ && git commit -m \"Spoken earcons\""
