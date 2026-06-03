# LocalAiRobot

> A 1/10 RC crawler you can **talk to** — local LLM, speech-to-text, and text-to-speech running
> **entirely on a Raspberry Pi 5 (16GB)**. No cloud, no Wi-Fi, fully offline. It drives on voice
> command, **stops for an obstacle directly ahead**, and talks back.

```
 "go forward"  ─▶ 🎙️ mic ─▶ whisper.cpp ─▶ gemma3:4b ─▶ {drive,steer,speed,duration,reply}
                                                              │            │
                                                              ▼            ▼
                                                    🔊 Piper TTS    PCA9685 (I2C PWM)
                                                                     ├─▶ ESC ─▶ drive motor
                                                                     └─▶ steering servo
```

The Pi 5 does everything: captures voice, transcribes it (**whisper.cpp**), turns it into a small JSON
command (**Ollama** `gemma3:4b`), speaks a reply (**Piper**), and drives the car. A **PCA9685** over I2C
generates the 50 Hz ESC/steering PWM (jitter-free even under LLM load); a forward **HC-SR04P** refuses
forward motion under a set distance. Steering is **Ackermann** (drive + steer, no strafe/spin). Control
is **push-to-talk** (press ENTER over SSH).

**Is:** voice teleop with a forward safety stop. **Is not:** self-driving, obstacle *avoidance*, or wake-word.

> 🛟 **Safety — no hardware watchdog.** A software failsafe returns the ESC to neutral on STOP / error /
> obstacle / move-timeout and arms it at neutral on boot — **but if the process is killed, the PCA9685
> holds the last pulse.** Keep a **physical kill switch on the motor battery** in reach, and start slow.

---

## Repository layout

```
brain/main.py            # orchestrator: listen → think → speak → command the car
brain/driver.py          # PCA9685 ESC + steering servo, HC-SR04 gate, software failsafe
brain/requirements.txt   # Pi 5 hardware deps (ServoKit, gpiozero, lgpio)
config/robot.env         # runtime config (env vars)
scripts/install_stack.sh # one-shot installer (Ollama + whisper.cpp + Piper + venv)
scripts/robot.service    # optional systemd unit
docs/assembly.md         # wiring + build reference
```

---

## Parts list (with search terms)

Search terms are generic — use your preferred retailer.

**Compute, power & audio (Pi side):**

| Part | Search term | Notes |
|---|---|---|
| Raspberry Pi 5, 16GB | `Raspberry Pi 5 16GB` | the brain |
| Active cooler | `Raspberry Pi 5 active cooler` | mandatory under sustained LLM load |
| UPS HAT | `Raspberry Pi 5 UPS HAT 18650` | on-board Pi power (e.g. GeeekPi Gen 6); confirm it sustains the Pi 5 under load |
| 2× 18650 cells | `18650 flat-top high-drain 3000mAh` | genuine (Samsung/Sony); if not bundled with the UPS |
| Official PSU, 27 W | `Raspberry Pi 5 27W USB-C power supply` | bench power while building |
| microSD (or NVMe) | `high-endurance microSD 64GB` | OS storage (or NVMe SSD + M.2 HAT) |
| USB microphone | `mini USB microphone` | voice input |
| USB speaker | `USB sound card speaker Raspberry Pi` | TTS output |

**Vehicle & control electronics:**

| Part | Search term | Notes |
|---|---|---|
| Chassis | `1/10 SCX10 frame all-metal chassis 313mm` | roller, wheels included; you assemble it |
| Motor | `SURPASS 540 brushed motor 35T` | high-turn = torque; **not** a low-turn 13T |
| Pinion | `32P pinion gear 3.175mm 12T` | **32DP / M0.8**, 12–13T, 3.175 mm bore |
| ESC | `HobbyWing QuicRun 1060 brushed ESC` | 60 A; its 6 V BEC powers the servo |
| Steering servo | `DSSERVO DS3225 digital servo 180` | 25 kg, **180°** (not 270°) |
| Battery | `2S 7.4V 5200mAh LiPo Deans` | match the plug to the ESC |
| Charger | `IMAX B6 lipo balance charger` | balance-charges the 2S pack |
| Plug adapter | `T plug to Tamiya adapter` | only if the ESC plug ≠ battery plug |
| PWM driver | `PCA9685 16 channel servo driver I2C` | the PWM bridge to ESC + servo |
| Distance sensor | `HC-SR04P ultrasonic sensor` | the **P** version is 3.3 V — no level shifter |
| Jumper wires | `dupont jumper wire kit female female` | PCA9685↔Pi and sensor↔Pi |

Tool: a **soldering iron** (PCA9685 headers, motor/ESC bullets).

> ⚠️ **Confirm with the chassis seller:** which of the two gearboxes you want (asked after you order),
> and the **spur gear pitch** — it must match the pinion (SCX10 standard is **32P / Mod 0.8**).

---

## Wiring at a glance

Full build + calibration in [docs/assembly.md](docs/assembly.md).

```
 2S LiPo ─▶ ESC ─▶ drive motor
              └ ESC signal/BEC plug ─▶ PCA9685 ch0 signal + V+ rail (6V) + GND
 Steering servo ───────────────────▶ PCA9685 ch1 (signal / V+ / GND)
 PCA9685  VCC + SDA + SCL + GND ────▶ Pi 3.3V, GPIO2/SDA, GPIO3/SCL, GND
 HC-SR04P  VCC▶Pi 3.3V   GND▶Pi GND   TRIG▶GPIO23   ECHO▶GPIO24
 Pi 5 ◀── UPS HAT (its own battery)
```

- **Two isolated power domains** (LiPo→motor/servo, UPS→Pi): **never bridge them**, but **tie all grounds together** (Pi ⟷ PCA9685 ⟷ ESC) or the PWM won't register.
- PCA9685 **V+ rail = the ESC's 6 V BEC**, *not* the Pi. The HC-SR04**P** runs at **3.3 V**, so ECHO is Pi-safe (no divider).

---

## Setup

> Assumes the crawler is built and wired per [docs/assembly.md](docs/assembly.md), and **Raspberry Pi OS
> Lite (64-bit)** is flashed with SSH enabled.

**1. Install (needs internet once).**
```bash
git clone https://github.com/<you>/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot && chmod +x scripts/install_stack.sh && ./scripts/install_stack.sh
```
Installs Ollama + `gemma3:4b`, whisper.cpp + `base.en`, Piper + voice, enables I2C, adds you to the
`i2c`/`gpio` groups, and builds a venv in `brain/.venv`.

**2. Reboot and verify.**
```bash
sudo reboot
ollama list        # lists gemma3:4b
i2cdetect -y 1    # PCA9685 shows at 0x40
arecord -l         # find your mic → plughw:x,y ;  aplay -l → find the USB speaker card
```

**3. Configure** [`config/robot.env`](config/robot.env): set `YOUR_USER` in the whisper/piper paths,
`ROBOT_MIC`, and `ROBOT_TRIG_PIN`/`ROBOT_ECHO_PIN`. (If playback is silent, make the USB sound card the
default ALSA output.) Leave steering/throttle at defaults until calibration.

**4. Calibrate on a stand — wheels OFF the ground.** Run the brain (step 5) and:
- say *"center" / "turn left" / "turn right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT` so the wheels center true and turn fully without the servo buzzing;
- say *"go forward"* (low speed); if it reverses, set `ROBOT_THROTTLE_INVERT=1`;
- say *"go forward"* + hand ~20 cm ahead → motor cuts, robot says *"Obstacle ahead. Stopping."*

**5. Talk to it.**
```bash
set -a; source config/robot.env; set +a
brain/.venv/bin/python brain/main.py
```
```
[ENTER]=talk  q=quit > ⏎
Heard: "go forward and bear left"
drive=FORWARD steer=LEFT speed=40 dur=2000ms reply="Driving forward, easing left."
```
Only put the car on the floor after step 4 passes, at low `ROBOT_MAX_THROTTLE`, kill switch in hand.
The brain reads stdin (push-to-talk), so run it over SSH; `scripts/robot.service` is restart plumbing only.

---

## Voice command model

The LLM returns one JSON object; the Pi validates and clamps it before driving:

| Key | Values | Notes |
|---|---|---|
| `drive` | `FORWARD` / `REVERSE` / `STOP` | unknown → `STOP` (fail-safe) |
| `steer` | `LEFT` / `CENTER` / `RIGHT` | unknown → `CENTER`; to turn, the car must be moving |
| `speed` | `0`–`100` | scaled by `ROBOT_MAX_THROTTLE` |
| `duration_ms` | `300`–`5000` | run time before the failsafe neutralizes the move |
| `reply` | short text | spoken via Piper (stdin, no shell → no injection) |

**Tuning** (all in `config/robot.env`): `ROBOT_MODEL`, `ROBOT_WHISPER_MODEL`, `ROBOT_RECORD_SECS`,
`ROBOT_MAX_THROTTLE`, `ROBOT_STEER_*`, `ROBOT_OBSTACLE_CM`, `ROBOT_MAX_MOVE_MS`.

After the one-time downloads, disable Wi-Fi — everything runs on-device.

---

## Credits & license

MIT — see [LICENSE](LICENSE). Built on [Ollama](https://ollama.com/) +
[Gemma 3](https://ai.google.dev/gemma), [whisper.cpp](https://github.com/ggerganov/whisper.cpp),
[Piper](https://github.com/rhasspy/piper), [gpiozero](https://gpiozero.readthedocs.io/), and
[Adafruit ServoKit / PCA9685](https://github.com/adafruit/Adafruit_CircuitPython_ServoKit).
