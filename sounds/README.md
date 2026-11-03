# Sounds

Short spoken clips tell you what the robot is doing: "Yes?" when it hears the wake word, "Got it" when it
understands, "Working on it" while the slow model thinks. They're made with Piper in the robot's own voice and
committed, so a fresh clone has them. Each time, the robot plays a random clip from the right folder, so a few
variants add variety. An empty folder is simply silent.

| Folder | When it plays | Example |
|---|---|---|
| `greeting_sounds/` | once at start-up | "Systems online" |
| `wake_word_ack/` | the moment it hears "hey jarvis" | "Yes?" |
| `ack_sounds/` | after it accepts your command | "Got it" |
| `thinking_sounds/` | while the model works | "Working on it" |
| `thinking_still_sounds/` | when that takes a while longer | "Still on it" |
| `remote_ack_sounds/` | when a command arrives from the phone | "Incoming request, working on that" |
| `sleep_sounds/` | when a conversation ends | "Okay, chat later." |
| `error_sounds/` | on an error or an obstacle | "Uh oh" |
| `spook_sounds/` | Halloween's jump scare | synthetic stingers, not speech |

## Make or change them

Do this on your PC (Linux, macOS or WSL):

```bash
./scripts/host_setup.sh               # once: installs Piper and its default voice, plus sox
./scripts/generate_sounds.sh          # speaks any clips you don't have yet
./scripts/generate_sounds.sh --force  # speaks every clip again
git add sounds/ && git commit -m "New sounds" && git push
```

The robot gets them with its next `git pull`. To change the words, edit the `say <file> "<words>"` lines near
the bottom of `scripts/generate_sounds.sh`. Without `--force` it skips files that already exist, so a changed
line keeps its old audio until you use `--force` or delete that file. Test a clip with
`aplay sounds/wake_word_ack/yes.wav`.

One clip by hand, in the configured voice:

```bash
set -a; . config/robot.env; set +a
echo "Coming right up" | "$ROBOT_PIPER_BIN" -m "$ROBOT_PIPER_VOICE" \
  ${ROBOT_PIPER_SPEAKER:+--speaker $ROBOT_PIPER_SPEAKER} -f sounds/ack_sounds/coming.wav
```

## Changed the robot's voice?

`./scripts/scan_piper_voice.sh` auditions Piper voices and `--apply` switches the robot to one. The clips don't
change with it, so "Yes?" stays in the old voice while the answer comes in the new one. Regenerate them on the
Pi, where the new voice was downloaded:

```bash
./scripts/generate_sounds.sh --force
```

Its first line names the voice it's about to use; check it before it writes 57 files. Then commit the new clips
(or make them on your PC instead), so you don't end up with two voices in one folder. Re-check name
spellings too: `./scripts/pronounce_my_name.sh <spelling> --name=<Name>`.

The Halloween stingers come from `./scripts/generate_spook_stingers.sh`, which needs no Piper.

## Settings

| Setting | What it does |
|---|---|
| `ROBOT_SOUNDS_ENABLE` | `0` silences every clip |
| `ROBOT_SOUNDS_DEVICE` | the speaker, set by `./scripts/setup_audio.sh` |
| `ROBOT_SOUNDS_GREETING`, `_WAKE_ACK`, `_ACK`, `_THINKING`, `_SLEEP`, `_ERROR` (and the rest) | turn one kind of clip on or off |
| `ROBOT_THINKING_LEAD_MS` | stay quiet on turns faster than this |
| `ROBOT_THINKING_REPEAT_S` | seconds between "thinking" clips; `0` plays it once |

Too quiet? See [volume](../docs/configuration.md#volume). Clips never talk over speech, and in a dry run they're
logged as `SOUND -> <cue>` instead of played.
