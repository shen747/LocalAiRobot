#!/usr/bin/env bash
# generate_sounds.sh -- synthesize the robot's SPOKEN earcons with Piper, on your PC. Creates the sound
# folders and speaks a default set into them; edit the word list at the bottom to make them your own,
# then commit. Run this AFTER ./scripts/host_setup.sh (which installs Piper + the en_US-lessac voice).
#
#   ./scripts/generate_sounds.sh        # write the default spoken cues (overwrites same-named files)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PIPER="$HOME/piper/piper/piper"
VOICE="$HOME/piper/en_US-lessac-medium.onnx"

if [ ! -x "$PIPER" ] || [ ! -f "$VOICE" ]; then
  echo "Piper isn't installed where this expects it:" >&2
  [ -x "$PIPER" ] || echo "   missing binary: $PIPER" >&2
  [ -f "$VOICE" ] || echo "   missing voice:  $VOICE" >&2
  echo "Run  ./scripts/host_setup.sh  first (installs Piper + the voice into ~/piper)." >&2
  exit 1
fi

cd "$REPO"
mkdir -p sounds/greeting_sounds sounds/wake_word_ack sounds/ack_sounds \
         sounds/thinking_sounds sounds/error_sounds

# say <out.wav> "<words>": speak the words into the WAV with Piper (the robot's own voice).
say() {
  if echo "$2" | "$PIPER" -m "$VOICE" -f "$1" >/dev/null 2>&1; then
    printf '  wrote  %-38s "%s"\n' "$1" "$2"
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
# ack -- after a SPOKEN command is accepted (confirming it)
say sounds/ack_sounds/got_it.wav         "Got it"
say sounds/ack_sounds/on_it.wav          "On it"
# thinking -- looped while the model runs
say sounds/thinking_sounds/working.wav   "Working on it"
say sounds/thinking_sounds/thinking.wav  "Let me think"
# error -- obstacle / LLM error
say sounds/error_sounds/uh_oh.wav        "Uh oh"

echo ">> done. Test a clip:   aplay sounds/ack_sounds/yes.wav"
echo "   Keep them?           git add sounds/ && git commit -m \"Spoken earcons\""
