# Hardware diagnostics — wiring self-test before the first run

Once the car is **built, wired, and powered** (LiPo + Pi) and you're SSH'd in, run this **live** self-test.
It's the opposite of dry-run: it drives the real hardware, checks every wired peripheral against the build
docs, prints a green ✓ / red ✗ per item, and ends with a summary that names exactly what's wrong and the
doc to fix it.

```bash
./scripts/run_diagnostics.sh
```

> 🔴 **Safety — the motor test SPINS THE WHEELS.** Keep the **wheels off the ground** (or the motor
> unplugged) and a **kill switch in hand**. The script does all the harmless checks first and only spins
> the motor *after* you confirm "wheels off the ground?". The ESC is armed only at that point.

## Prerequisites
- Wired per [PWM/ESC/servo](wiring/pwm-esc-servo-wiring.md) + [ultrasonic sensors](wiring/ultrasonic-sensors-wiring.md), camera ([Phase 5](assembly.md)) and face ([Phase 6](assembly.md)).
- The stack installed (`./scripts/install_stack_pi5.sh`) — the venv, `i2c-tools`, `alsa-utils`, libcamera apps.
- `config/robot.env` set up (pins, `ROBOT_MIC`, whisper/piper paths). The wrapper sources it and **forces
  live** (`ROBOT_DRY_RUN=0 ROBOT_MOTORS_OFF=0`) so a stray bench setting can't fake the hardware under test.

## How it decides ✓ / ✗
The Pi has **no electrical feedback** from the motor, servo, or speaker — so those are verified by **you**:
the script performs the action, then asks `did it work? [y/n]`. Everything with a real signal (I²C, sensors,
camera, mic level, panel presence) is checked automatically.

The run is split in two so it flows smoothly: **automated checks first** (I²C board, the 3 sensors, camera —
no input needed; a valid sensor *reading* alone passes, no hand-waving), then the **checks that need you**
(speaker → mic → face → the motor/servo spin last). Each confirm prompt **auto-skips after 12 s** (override
with `ROBOT_DIAG_TIMEOUT=<seconds>`), so a check never blocks — an unanswered actuation is marked
*unconfirmed* (counts as **skipped**, not failed) and the run always reaches the summary.

## Flags
| Flag | Effect |
|---|---|
| *(none)* | Interactive — performs each action and asks you to confirm. The truest wiring test. |
| `--yes` / `-y` | Auto-confirm the "did it work?" prompts (quick re-runs). **Motor spin is skipped** unless `--wheels-off` is also given. |
| `--wheels-off` | Pre-confirm the wheels are off the ground, so the motor spin can run unattended (with `--yes`). |
| `--no-motors` | Skip the motor/servo spin tests entirely; run everything else. |

## What it checks
**Automated:** ESC/PWM board (PCA9685 @ I²C `0x40`) · 3× ultrasonic (front/left/right — any valid reading
passes; only a no-echo sensor fails) · camera still + a 3 s clip. **Needs you:** speaker (Piper greeting, or
a guaranteed fallback test tone if Piper is missing) · microphone level (+ a wake-word score if the model
loads) · face panel (USB-C `turing` or DSI/HDMI `framebuffer`) · **motor forward/reverse** and **servo
right/left/center** (last, behind the wheels-off gate). Artifacts (`diag_picture.jpg`, `diag_video.h264`,
`diag_mic.wav`, `diag_speaker.wav`) are saved to `~/.localairobot/diagnostics/`.

Subsystems disabled in `config/robot.env` (`ROBOT_CAMERA_ENABLE=0`, `ROBOT_FACE_ENABLE=0`,
`ROBOT_SOUNDS_ENABLE=0`, `ROBOT_WAKEWORD_ENABLE=0`) show `[-]` (skipped), not a failure.

## Sample output
```
LocalAiRobot hardware diagnostics  (live wiring self-test -- artifacts in ~/.localairobot/diagnostics)

-- Automated checks --
[v] ESC / PWM board present (PCA9685 @ I2C 0x40)  found 0x40 on bus 1
[v] Ultrasonic sensor FRONT (TRIG 23 / ECHO 24)  reading 47 cm
[x] Ultrasonic sensor LEFT (TRIG 5 / ECHO 6)  NO ECHO received -- sensor not responding: check ECHO/TRIG pins & 3.3V wiring
[v] Ultrasonic sensor RIGHT (TRIG 20 / ECHO 21)  reading ~400 cm (max range -- aimed at open space)
[v] Camera picture (still)  41207 bytes -> ~/.localairobot/diagnostics/diag_picture.jpg
[v] Camera video clip (3 s)  1183220 bytes -> ~/.localairobot/diagnostics/diag_video.h264
[-] Camera live feed  not viewable over headless SSH; the saved clip above covers the pipeline

-- Checks that need you (listen / speak / watch) --
    >> PLAYING SOUND NOW -- listen to the speaker...
    Did you HEAR the greeting? [y/N] (auto-skip in 12s) y
[v] Speaker output (TTS greeting)
[v] Microphone input (say 'hey jarvis')  level peak 47% | wake 'hey jarvis' DETECTED (score 0.71)
[v] Face panel / monitor  USB-C serial panel (turing, port AUTO)
  SAFETY: the motor test SPINS THE WHEELS.
    Are the wheels OFF the ground (or the motor unplugged)? [y/N] y
[v] Motor FORWARD spin
[v] Motor REVERSE spin
[v] Servo steer RIGHT
[v] Servo steer LEFT
[v] Servo steer CENTER

Summary: 13 passed  1 failed  1 skipped

Fix these:
  x Ultrasonic sensor LEFT (TRIG 5 / ECHO 6): NO ECHO received -- sensor not responding: check ECHO/TRIG pins & 3.3V wiring
      -> see docs/wiring/ultrasonic-sensors-wiring.md
```

## When something fails — where to look
| Failed check | Fix it in |
|---|---|
| ESC / PWM board (0x40) | [pwm-esc-servo-wiring.md](wiring/pwm-esc-servo-wiring.md) §5 + the `i2cdetect` section; [assembly Phase 3](assembly.md) |
| Motor forward / reverse | [pwm-esc-servo-wiring.md](wiring/pwm-esc-servo-wiring.md) §1 (ESC→motor) + §2 (ch0) + §6; [assembly Phase 8](assembly.md) |
| Servo right / left / center | [pwm-esc-servo-wiring.md](wiring/pwm-esc-servo-wiring.md) §3 (ch1); `ROBOT_STEER_*` in [assembly Phase 8](assembly.md) |
| Ultrasonic front / left / right | [ultrasonic-sensors-wiring.md](wiring/ultrasonic-sensors-wiring.md); [assembly Phase 4](assembly.md) |
| Speaker | [assembly Phase 1](assembly.md) audio callout; `./scripts/setup_audio.sh` (USB speaker as ALSA default) |
| Camera picture / video | [assembly Phase 5](assembly.md); `rpicam-hello --list-cameras` (reseat the CSI ribbon / 22→15 cable) |
| Microphone / wake | [assembly Phase 1](assembly.md) steps 7–8; [wake-word guide](opnewakeword/openwake-word.md) (mic gain / threshold) |
| Face panel | [assembly Phase 6](assembly.md) (data USB-C cable, `dialout` re-login, `ROBOT_FACE_REVISION`) |

Once everything is ✓, continue to live calibration ([assembly Phase 8](assembly.md)).
