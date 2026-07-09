# Earcons — spoken audio cues

Short **spoken** WAV cues are part of how the robot talks to you: a greeting at boot, an "I heard you"
on every wake, a "thinking" cue while the (slow) model runs, and an error cue. You **generate them with
Piper on your PC** (instructions below — the same voice the robot speaks with) and **commit them**, so
they ship with the repo. `brain/sounds.py` plays a **random** clip from the matching folder each time, so
dropping a few variants in a folder adds variety.

```
sounds/
  greeting_sounds/   # played once on boot/arm             (e.g. "Systems online")
  wake_word_ack/     # the instant "hey jarvis" fires      (e.g. "Yes?", "Hi", "Hello there")  < ~1 s
  ack_sounds/        # after a spoken command is accepted  (e.g. "Got it", "On it")  < ~1 s
  thinking_sounds/   # looped WHILE the model runs          (e.g. "Working on it")  ~1–2 s, loopable
  error_sounds/      # on obstacle / LLM error              (e.g. "Uh oh")
```

**Two acks — wake vs command.** There are *two* acknowledgement moments, each with its own folder so they
can sound different:
- **`wake_word_ack/`** plays the **instant the wake word fires** (each *"hey jarvis"*), *as* the face
  switches to **`listening`**. These are summon responses — *"Yes?", "Hi", "Hello there"* — telling you to
  go ahead and speak (it plays before recording your command). Toggle with `ROBOT_SOUNDS_WAKE_ACK`.
- **`ack_sounds/`** plays after your **spoken command** is transcribed and accepted (past the noise
  filter) — confirmations like *"Got it", "On it"*. Toggle with `ROBOT_SOUNDS_ACK`.

A **random** clip is picked from the relevant folder each time, so put two or three in each for variety.

Why this matters here: on the Pi 5 CPU the model is slow (text up to 120 s, **vision up to 600 s**), so
without a cue a think/look is silent and the operator can't tell the robot heard them. The **greeting**
is especially useful **headless under `scripts/robot.service`** (no console) as an audible "stack is up".

An **empty folder is a silent no-op**, and audio never blocks driving — so the robot still runs if a
folder is empty, it just won't make that cue.

## Generating the spoken cues (on your PC)

Cues are made with **Piper** so they match the robot's own voice. **Do this on your PC** (WSL / macOS /
Linux), not the Pi — the Pi only ever runs `scripts/install_stack_pi5.sh` and reads `config/robot.env`.

**1. One-time: install the tools.**
```bash
./scripts/host_setup.sh        # plan -> confirm -> installs Piper (+ the en_US-lessac voice) + sox + the face tools
```
This puts Piper at `~/piper/piper/piper` (the same path and voice the Pi uses).

**2. Generate the spoken set.**
```bash
./scripts/generate_sounds.sh   # creates the folders + speaks the default cues
```
Re-running is **safe**: it only fills in cues you're **missing** and **skips ones that already exist** —
it never regenerates or duplicates them. **To change the words/voice**, edit the `say <file> "<words>"`
lines near the bottom of `scripts/generate_sounds.sh`, then re-run with **`--force`** (or delete that file
first). Add several `wake_word_ack/` and `ack_sounds/` lines — the robot plays one at random each time. To
add or replace a *single* cue by hand (the folders exist after the script), call Piper directly:
```bash
P="$HOME/piper/piper/piper -m $HOME/piper/en_US-lessac-medium.onnx"
echo "Coming right up" | $P -f sounds/ack_sounds/coming.wav
```
Piper writes **16-bit mono 22050 Hz** — fine for `aplay`. If a speaker ever complains about the rate,
normalize to 44100 with sox (from `host_setup.sh`): `sox in.wav -r 44100 -c 1 -b 16 out.wav`.

**3. Test, then commit.** Play a clip with `aplay sounds/wake_word_ack/yes.wav` (any machine with a speaker),
or run `./scripts/run_me.sh --dry`, which logs `SOUND -> <cue>` instead of playing. When they sound right:
```bash
git add sounds/ && git commit -m "Spoken earcons"
```
`sounds/**/*.wav` is git-tracked (a `.gitignore` exception), so the cues deploy to the robot with your
next `git pull`/rebase. **This is the exact process used to make the shipped set** — follow it to replace
them with your own words/voice.

## Tuning (on the Pi, via config)

All knobs live in [`config/robot.env`](../config/robot.env) (reference: [`config/README.md`](../config/README.md)):
`ROBOT_SOUNDS_ENABLE`, `ROBOT_SOUNDS_DIR`, **`ROBOT_SOUNDS_DEVICE`** (the USB *speaker* card for `aplay -D`,
separate from the mic), per-category `ROBOT_SOUNDS_{GREETING,ACK,THINKING,ERROR}`, `ROBOT_THINKING_LEAD_MS`
(stay silent on fast turns), and `ROBOT_THINKING_REPEAT_S` (seconds between thinking cues; **`0` = play it
once** — keeps a spoken "Let me think" from repeating while the model runs).

Speaker note: **any** USB speaker works — `ROBOT_SOUNDS_DEVICE` is just its `aplay -l` card name, so
swapping is config-only (no code change). A **USB-powered** model (e.g. **Naciffo 2**) avoids a separate
PSU. **Too quiet?** Max the speaker's mixer **on the Pi**: `amixer -c <id> sset PCM 100% && sudo alsactl
store` (80% is ~−6 dB; this lifts both the voice *and* the earcons — they share the speaker). If the
speaker has **no volume control** (`amixer -c <id> scontrols` is empty) its level is fixed — make the WAVs
themselves quieter on your PC instead (`sox in.wav out.wav gain -6`).

> These cues play through `aplay` under the same half-duplex `audio_lock` as Piper TTS, so a cue can never
> garble speech or be recorded into a command. In dry-run (`ROBOT_DRY_RUN=1`) nothing is played — each cue
> logs `SOUND -> <cue>` on the `robot.sound` logger instead.
