# Earcons (non-verbal audio cues)

Drop short **16-bit PCM mono/stereo WAV** files into these folders and `brain/sounds.py` will
play them as feedback. A random file is chosen from the relevant folder each time. **Empty folders
are a silent no-op** — the feature is harmless until you add sounds, and it never blocks driving.

```
sounds/
  greeting_sounds/   # played once on boot/arm        (e.g. online.wav, systems_go.wav)
  ack_sounds/        # short blip on wake / command    (e.g. got_it.wav, on_it.wav)   < ~1 s
  thinking_sounds/   # looped tone WHILE the model runs (e.g. computing.wav, processing.wav) 1–3 s
  error_sounds/      # on obstacle / LLM error          (e.g. uh_oh.wav)   [optional]
```

Why this matters here: on the Pi 5 CPU the model is slow (text up to 120 s, **vision up to
600 s**), so without a cue a think/look is silent and the operator can't tell the robot heard them.
The **greeting** is especially useful when running **headless under `scripts/robot.service`** (no
console) as an audible "the stack is up".

Tuning is all in [`config/robot.env`](../config/robot.env): `ROBOT_SOUNDS_ENABLE`,
`ROBOT_SOUNDS_DIR`, **`ROBOT_SOUNDS_DEVICE`** (the USB *speaker* card for `aplay -D`, separate from
the mic), per-category `ROBOT_SOUNDS_{GREETING,ACK,THINKING,ERROR}`, `ROBOT_THINKING_LEAD_MS`
(stay silent on fast turns), and `ROBOT_THINKING_GAP_MS`.

Format tip: use a sample rate the USB speaker supports natively (44100 or 48000 Hz) so `aplay`
never has to resample. Keep `ack` clips well under a second; `thinking` clips short and loopable.

Speaker note: **any** USB speaker works — `ROBOT_SOUNDS_DEVICE` is just its `aplay -l` card name, so
swapping is config-only (no code change). A **USB-powered** model (e.g. **Naciffo 2**) avoids a separate
PSU. If the speaker has **no volume control** (`amixer -c <id> scontrols` is empty), its level is fixed —
make the WAVs themselves quieter (`sox in.wav out.wav gain -6`) instead.

## Generating the earcons (concise)

Target format: **16-bit PCM mono WAV**, ideally 44100 Hz. `ack` < 1 s; `thinking` 1–3 s and loopable.

1. **Spoken cues with Piper** (already installed at `~/piper/piper/piper`) — pipe text to a WAV:
   ```bash
   P="$HOME/piper/piper/piper -m $HOME/piper/en_US-lessac-medium.onnx"
   echo "Got it"         | $P -f sounds/ack_sounds/got_it.wav
   echo "Systems online" | $P -f sounds/greeting_sounds/online.wav
   echo "Working on it"  | $P -f sounds/thinking_sounds/working.wav
   echo "Uh oh"          | $P -f sounds/error_sounds/uh_oh.wav
   ```
   (Piper outputs 22050 Hz mono — fine, but run step 3 to match your speaker if `aplay` complains.)
2. **Beeps/tones with sox** (`sudo apt install sox`):
   ```bash
   sox -n sounds/ack_sounds/blip.wav       synth 0.12 sine 880 fade 0 0.12 0.02   # short ack blip
   sox -n sounds/thinking_sounds/think.wav synth 1.0  sine 440 gain -8            # loopable hum
   ```
3. **Normalize** any file to 16-bit mono 44100: `sox in.wav -r 44100 -c 1 -b 16 out.wav`
4. **Test:** `aplay sounds/ack_sounds/blip.wav`, or `./scripts/run_me.sh --dry` (logs `SOUND -> <cue>`
   instead of playing).

A random file is picked from each folder, so drop in a few variants per category for variety.

> These cues play through `aplay` under the same half-duplex `audio_lock` as Piper TTS, so a cue
> can never garble speech or be recorded into a command. In dry-run (`ROBOT_DRY_RUN=1`) nothing is
> played — each cue logs `SOUND -> <cue>` on the `robot.sound` logger instead.
