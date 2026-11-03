# Configuration

All settings live in one file on the robot, `config/robot.env`, one per line as `KEY=value`. This page covers
how to change them, the ones people usually change, and how to keep secrets safe. Every setting is listed in
[config/README.md](../config/README.md).

## How it works

- The installer made `config/robot.env` from the template, `config/robot.env.example`. Git ignores your copy,
  so updates never overwrite it and your keys never get committed.
- Edit it with `nano config/robot.env`, or change one line with `sed`:
  ```bash
  sed -i 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=80|' config/robot.env
  ```
  `sed` replaces the whole line, so that setting's comment goes. That's harmless.
- Apply your changes to the running robot with `./scripts/run_me.sh --reload`.
- New SSH sessions load the file automatically. In a shell you keep using, reload it with
  `set -a; source config/robot.env; set +a`.
- A setting missing from your file falls back to its default in the code. After an update,
  `./scripts/check_config.sh` lists settings that are new in the template.
- Hardware patches in `scripts/patches/` are just scripts that write the right settings for a part.

## The ones people change

| Setting | What it does |
|---|---|
| `ROBOT_NAME` | what the robot calls itself (`Jarvis`) |
| `ROBOT_CONCIERGE_MODE`, `ROBOT_HALLOWEEN_MODE` | switch modes: see [concierge](concierge.md) and [Halloween](halloween.md) |
| `ROBOT_AI_BACKEND` | `local` (default), `gemini` or `mixed`; Gemini needs `ROBOT_GEMINI_API_KEY`, see [gemini-setup.md](gemini-setup.md) |
| `ROBOT_MODEL`, `ROBOT_VISION_MODEL` | the local language and vision models (`gemma4:e2b-it-qat`, `moondream`) |
| `ROBOT_SPEAKER_VOLUME` | speaker volume, 0 to 100 (below) |
| `ROBOT_PIPER_VOICE` | the voice; audition others with `./scripts/scan_piper_voice.sh`, see [sounds](../sounds/README.md) |
| `ROBOT_TTS_PRONUNCIATIONS` | fix how names are said, for example `"Ramesh=Ruh-mesh"` |
| `ROBOT_WAKEWORD_MODEL` | the wake word model, see [wake word](openwakeword/openwake-word.md) |
| `ROBOT_FOLLOWUP_SILENCE_S` | seconds of quiet before it stops listening for follow-ups (12) |
| `ROBOT_FACE_BACKEND` | `auto`, or force `turing` (USB-C panel) or `framebuffer` (DSI/HDMI screen) |
| `ROBOT_WEATHER_LAT`, `ROBOT_WEATHER_LON` | where the forecast is for, see [weather](weather.md) |
| `ROBOT_DRY_RUN` | `1` fakes the motor and sensors, so nothing moves |
| `ROBOT_MOTORS_OFF` | `1` runs everything live except the wheels |
| `ROBOT_LOG_LEVEL` | `DEBUG` for detail while chasing a problem, see [troubleshooting](troubleshooting.md#more-detail-in-the-log) |

## Volume

Volume changes take effect at once, with no restart:

```bash
sed -i 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=100|' config/robot.env
./scripts/setup_audio.sh
```

The robot sets the volume again on every start, because USB speakers forget it when they lose power. Still too
quiet at 100? What's left is the speaker's own control:

```bash
aplay -l              # find the speaker's card number
alsamixer -c 1        # raise its control, then Esc
```

## Secrets

Three settings are secret: `ROBOT_GEMINI_API_KEY`, `ROBOT_HTTP_TOKEN` (the phone remote's password, made for you
on first start if blank) and `ROBOT_MQTT_PASS`.

- Put them only in your `config/robot.env`. Git ignores it.
- Never put them in `config/robot.env.example` or any other file in the repo.
- For the weather, two decimal places (about 1 km) are plenty. Don't commit your home's exact coordinates.
- A disk image or backup of the robot contains your keys and Wi-Fi password. Store it like a password.

## What uses the internet

Once installed, everything runs on the Pi. Only two things reach the internet: Gemini, if you turn it on, and
the weather card, which is on by default (`ROBOT_WEATHER_ENABLE=0` turns it off). The phone remote stays on
your Wi-Fi. With Gemini on, camera frames go to Google.
