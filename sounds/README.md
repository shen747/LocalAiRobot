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

> These cues play through `aplay` under the same half-duplex `audio_lock` as Piper TTS, so a cue
> can never garble speech or be recorded into a command. In dry-run (`ROBOT_DRY_RUN=1`) nothing is
> played — each cue logs `SOUND -> <cue>` on the `robot.sound` logger instead.
