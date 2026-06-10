# LocalAiRobot

> A 1/10 RC crawler you can **talk to** — local LLM, speech-to-text, text-to-speech, **and a
> camera**, running **entirely on a Raspberry Pi 5 (16GB)**. No cloud, no Wi-Fi, fully offline.
> It drives on voice command, **stops for an obstacle directly ahead**, **looks around and drives
> toward things you name**, shows an **expressive face**, and talks back.

![LocalAiRobot voice + vision control flow: a voice command — started by an optional "Richard" wake word (openWakeWord) or ENTER push-to-talk — is transcribed by whisper.cpp, passes a trigger-phrase gate (EXPLORE/FIND only on explore words, otherwise DRIVE), and the multimodal gemma3:4b (Ollama) turns it into an intent JSON using recent context from a SQLite environment memory; DRIVE/STOP go to the driver.py control loop while EXPLORE/FIND run a behaviour loop that stops the car, captures a Pi Camera V2 NoIR frame, asks gemma3:4b what is visible, records it to memory, and takes gated drive steps; a 3-sensor HC-SR04P array (front/left/right) gates FORWARD and informs steering; the PCA9685 drives the ESC (Electronic Speed Controller) / drive motor and steering servo; the text-intent and vision calls share one Ollama model behind a single model_lock (warmed at boot); replies are spoken via Piper TTS alongside short ack/greeting/thinking earcons, and a USB-C face screen reflects state (idle/listening/thinking/happy/confused/alarmed/warmup/capturing)](docs/images/basic_control_flow.png)

The Pi 5 does everything: captures voice, transcribes it (**whisper.cpp**), turns it into a small JSON
intent (**Ollama** `gemma3:4b` — the *same* model also reads camera frames), speaks a reply
(**Piper**), shows a face, and drives the car. A **PCA9685** over I2C generates the 50 Hz PWM for the
**ESC (Electronic Speed Controller)** (Electronic Speed Controller — the throttle/motor driver between battery and motor) and the
steering servo, jitter-free even under LLM load; a **3-sensor HC-SR04P** array (front/left/right) cuts
forward motion under a set distance and keeps steering off near walls. Steering is **Ackermann** (drive + steer,
no strafe/spin). Control is **push-to-talk** (press ENTER over SSH), with an **optional "Richard"
wake word** (openWakeWord, off by default — which also lets it run headless as a service).

**Autonomy is opt-in.** Ordinary commands ("go forward", "turn left", "stop") are plain teleop. The
camera-driven **look-around / find-the-sofa** behaviour only runs when you actually say an explore
phrase ("look around", "find the …", "go to the …") — a deterministic trigger gate checks the raw
transcript, so a misheard command can never send the robot wandering.

**Is:** voice teleop + a forward safety stop + opt-in, bounded, vision-guided explore/find with a
persistent scene memory + an **optional wake word**. **Is not:** self-driving, obstacle *avoidance* /
path-planning, or SLAM. Vision on the Pi 5 CPU is **slow** (tens of seconds to minutes per look), so
"find the X" is a deliberate stop-look-step loop, not real-time navigation.

> 🛟 **Safety — no hardware watchdog.** A software failsafe returns the ESC (Electronic Speed Controller) to neutral on STOP / error /
> obstacle / move-timeout and arms it at neutral on boot — **but if the process is killed, the PCA9685
> holds the last pulse.** Keep a **physical kill switch on the motor battery** in reach, and start slow.

---

## Repository layout

```
brain/main.py            # orchestrator: listen → think → (drive | explore | find) → speak; face events
brain/driver.py          # PCA9685 ESC (Electronic Speed Controller) + steering servo, 3x HC-SR04P, software failsafe, dry-run fakes
brain/camera.py          # Pi Camera V2 NoIR capture → preprocessed JPEG (picamera2)
brain/vision.py          # send a frame to gemma3:4b via Ollama → structured scene JSON
brain/memory.py          # SQLite environment memory: record sightings, summarise back to the prompt
brain/behavior.py        # bounded autonomous explore / find-and-drive-to-object loop
brain/face.py            # expression face: USB-C panel | DSI/HDMI framebuffer | simulated (+ PNG animation)
brain/inference.py       # one keep-alive + model_lock gateway to Ollama (text & vision share it; warmup)
brain/sounds.py          # ack / greeting / thinking earcons (aplay, half-duplex); dry-run logs only
brain/wakeword.py        # optional "Richard" wake word (openWakeWord); falls back to ENTER push-to-talk
brain/requirements.txt   # Pi 5 deps (ServoKit, gpiozero, lgpio, Pillow, numpy, pyserial; + openwakeword)
config/robot.env         # runtime config (env vars)
sounds/                  # optional earcon WAVs (greeting/ack/thinking/error); empty = silent — see sounds/README.md
brain/faces/             # optional per-state PNG animation frames (none = procedural) — see brain/faces/README.md
scripts/install_stack.sh # one-shot installer (Ollama + whisper.cpp + Piper + picamera2 + venv)
scripts/robot.service    # optional systemd unit
docs/assembly.md         # wiring + build reference (start here to build it)
docs/images/*.puml       # editable PlantUML sources for the diagrams (render in place)
```

---

## Parts list (with search terms)

Search terms are generic — use your preferred retailer.

**Compute, power & audio (Pi side):**

| Part | Search term | Notes |
|---|---|---|
| Raspberry Pi 5, 16GB | `Raspberry Pi 5 16GB` | the brain |
| Active cooler | `Raspberry Pi 5 active cooler` | **mandatory** under sustained LLM/vision load; fit it first |
| UPS HAT (pogo-pin) | `Raspberry Pi 5 UPS HAT 18650 pogo pin` | prefer the **pogo-pin, bottom-mount** type (e.g. Geekworm X1200/X1202) so it does **not** use the 40-pin header |
| 2× 18650 cells | `18650 flat-top high-drain 3000mAh` | genuine (Samsung/Sony); if not bundled with the UPS |
| Official PSU, 27 W | `Raspberry Pi 5 27W USB-C power supply` | **bench power while building (use this first, UPS later)** |
| microSD (or NVMe) | `high-endurance microSD 64GB` | OS storage (NVMe is much faster for model load) |
| USB microphone | `mini USB microphone` | voice input |
| USB speaker | `USB sound card speaker Raspberry Pi` | TTS output |

**Vision & display:**

| Part | Search term | Notes |
|---|---|---|
| Pi Camera V2 **NoIR** | `Raspberry Pi Camera V2 NoIR IMX219` | visual input for the multimodal LLM |
| **Pi 5 camera adapter cable** ⚠️ | `Raspberry Pi 5 camera cable 22-pin 0.5mm to 15-pin 1mm` | **MANDATORY** — the Pi 5 CSI port is the narrow 22-way type; the V2's 15-way cable **will not fit** without this |
| IR illuminator (optional) | `850nm IR LED illuminator board` | NoIR sees nothing in the dark without IR light; skip for lit rooms |
| 3.5" USB-C face screen | `3.5 inch USB Type-C secondary screen 320x480 IPS` | the TURZX/"NO AIDA64" panel; driven over USB-serial (see [assembly](docs/assembly.md)) |
| Small DSI/HDMI display (fallback) | `Raspberry Pi DSI 3.5 inch display` | only if the USB-C panel won't enumerate on Linux |

**Vehicle & control electronics:**

| Part | Search term | Notes |
|---|---|---|
| Chassis | `1/10 SCX10 frame all-metal chassis 313mm` | roller, wheels included; you assemble it |
| Motor | `SURPASS 540 brushed motor 35T` | high-turn = torque; **not** a low-turn 13T |
| Pinion | `32P pinion gear 3.175mm 12T` | **32DP / M0.8**, 12–13T, 3.175 mm bore |
| ESC (Electronic Speed Controller) | `HobbyWing QuicRun 1060 brushed ESC (Electronic Speed Controller)` | 60 A; its 6 V BEC powers the servo |
| Steering servo | `DSSERVO DS3225 digital servo 180` | 25 kg, **180°** (not 270°) |
| Battery | `2S 7.4V 5200mAh LiPo Deans` | match the plug to the ESC (Electronic Speed Controller) |
| Charger | `IMAX B6 lipo balance charger` | balance-charges the 2S pack |
| Plug adapter | `T plug to Tamiya adapter` | only if the ESC (Electronic Speed Controller) plug ≠ battery plug |
| PWM driver | `PCA9685 16 channel servo driver I2C` | the PWM bridge to ESC (Electronic Speed Controller) + servo |
| **3× distance sensor** | `HC-SR04P ultrasonic sensor` | **buy three** (front/left/right). The **P** version is 3.3 V — no level shifter |
| Sensor mount | `HC-SR04 triple sensor bracket` | semi-hex front plate: one ahead, one ~45–60° left, one ~45–60° right (or 3D-print) |
| Jumper wires | `dupont jumper wire kit female female` | PCA9685↔Pi and 3× sensor↔Pi |

Tool: a **soldering iron** (PCA9685 headers, motor/ESC (Electronic Speed Controller) bullets).

> ⚠️ **Confirm with the chassis seller:** which of the two gearboxes you want (asked after you order),
> and the **spur gear pitch** — it must match the pinion (SCX10 standard is **32P / Mod 0.8**).

---

## Wiring at a glance

Full build + calibration in [docs/assembly.md](docs/assembly.md).

![LocalAiRobot wiring at a glance: a 2S 7.4V LiPo feeds the ESC (Electronic Speed Controller) (which carries a 6V BEC) and the drive motor; the ESC (Electronic Speed Controller)'s 3-wire plug goes to PCA9685 ch0 (signal + V+ 6V rail + GND) and the steering servo to ch1 (signal/V+/GND); the PCA9685 takes VCC 3.3V + SDA(GPIO2) + SCL(GPIO3) + GND from the Raspberry Pi 5; three HC-SR04P sensors (front GPIO23/24, left GPIO5/6, right GPIO20/21) take 3.3V + GND + TRIG/ECHO from the Pi; the Pi Camera V2 NoIR connects over the CSI ribbon and the USB-C face screen over USB; the Pi 5 is powered by the UPS HAT on its own battery. Two isolated battery domains, common ground.](docs/images/wiring.png)

- **Two isolated power domains** (LiPo→motor/servo, UPS→Pi): **never bridge them**, but **tie all grounds together** (Pi ⟷ PCA9685 ⟷ ESC (Electronic Speed Controller)) or the PWM won't register.
- PCA9685 **V+ rail = the ESC (Electronic Speed Controller)'s 6 V BEC**, *not* the Pi. All three HC-SR04**P** run at **3.3 V**, so every ECHO is Pi-safe (no divider).
- **Pins:** I2C on GPIO2/3 (PCA9685 @ `0x40`); forward sensor TRIG/ECHO `23/24`, left `5/6`, right `20/21`. Camera is on the CSI ribbon, the face panel on USB — neither touches the GPIO header.

---

## Setup

> Assumes the crawler is built and wired per [docs/assembly.md](docs/assembly.md), and **Raspberry Pi OS
> Lite (64-bit, Bookworm)** is flashed with SSH enabled. You can do most of this **on the bench before
> the chassis exists** — see [docs/assembly.md](docs/assembly.md) "Phase 1".

**1. Install (needs internet once).**
```bash
git clone https://github.com/<you>/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot && chmod +x scripts/install_stack.sh && ./scripts/install_stack.sh
```
Installs Ollama + `gemma3:4b`, whisper.cpp + `base.en`, Piper + voice, **picamera2** (apt) and the
**USB-C face-panel library**, enables I2C, adds you to the `i2c`/`gpio`/`dialout`/`audio`/`video`
groups, and builds the venv at `brain/.venv` **with `--system-site-packages`** (required so it can see
picamera2/libcamera).

**2. Reboot and verify.**
```bash
sudo reboot
ollama list                     # lists gemma3:4b   (vision needs Ollama >= 0.6)
i2cdetect -y 1                  # PCA9685 at 0x40
rpicam-hello --list-cameras     # the IMX219 NoIR (needs the Pi-5 22→15 adapter cable)
lsusb | grep -i 1a86            # the USB-C face panel (CH340 serial), if connected
arecord -l                      # find your mic → plughw:x,y ;  aplay -l → the USB speaker card
```

**3. Configure** [`config/robot.env`](config/robot.env): set `YOUR_USER` in the whisper/piper paths,
`ROBOT_MIC`, and the sensor pins. Camera/face/explore/memory plus the wake-word/audio-cue/warmup
knobs have working defaults (the wake word is **off** by default). (If playback is silent, make the
USB sound card the default ALSA output.)

**4. 🧪 Bench bring-up FIRST (no chassis, no motor) — watch the model→hardware mapping.**
```bash
set -a; source config/robot.env; set +a
ROBOT_DRY_RUN=1 brain/.venv/bin/python brain/main.py
```
In dry-run there is **no hardware**: `driver.py` swaps in fakes and logs exactly what it *would* drive,
so you can confirm voice → model → GPIO/PWM is correct before anything can move:
```
*** DRY RUN -- no hardware will be driven ***
Heard: "go forward and bear left"
action=DRIVE drive=FORWARD steer=LEFT speed=40 dur=1500ms reply="Easing forward and left."
robot.hw INFO I2C->PCA9685 ESC (Electronic Speed Controller)   ch=0 throttle=+0.140 pulse_us=1570
robot.hw INFO I2C->PCA9685 SERVO ch=1 steer=LEFT   angle=55.0deg pulse_us=1111
```
Set `ROBOT_FAKE_DISTANCE_CM=20` to prove the forward gate trips; set `ROBOT_FAKE_VISION=sofa:left:near:0.9`
and say *"find the sofa"* to dry-run the whole explore loop with no camera.

**5. Calibrate on a stand — wheels OFF the ground** (LIVE now, ESC (Electronic Speed Controller) powered). Run the brain and:
- say *"center" / "turn left" / "turn right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT`;
- say *"go forward"* (low speed); if it reverses, set `ROBOT_THROTTLE_INVERT=1`;
- say *"go forward"* + hand ~20 cm ahead of the front sensor → motor cuts, robot says *"Obstacle ahead. Stopping."*

**6. Talk to it / drive it.**
```bash
brain/.venv/bin/python brain/main.py
```
```
[ENTER]=talk  q=quit > ⏎
Heard: "find the sofa"
action=FIND target='sofa'
robot.behavior INFO FIND 'sofa' SUCCESS at iter 2 (front=38cm)
```
Only put the car on the floor after step 5 passes, at low `ROBOT_MAX_THROTTLE`, kill switch in hand.
The brain reads stdin (push-to-talk), so run it over SSH; `scripts/robot.service` is restart plumbing only.

---

## Voice command model

The LLM returns one JSON object; the Pi validates and clamps it before acting:

| Key | Values | Notes |
|---|---|---|
| `action` | `DRIVE` / `EXPLORE` / `FIND` / `STOP` | default `DRIVE`; unknown → `STOP`. **`EXPLORE`/`FIND` only run if an explore phrase is in the transcript** (else demoted to `DRIVE`) |
| `target` | object name (e.g. `"sofa"`) | required by `FIND` |
| `drive` | `FORWARD` / `REVERSE` / `STOP` | used by `DRIVE`; unknown → `STOP` (fail-safe) |
| `steer` | `LEFT` / `CENTER` / `RIGHT` | unknown → `CENTER`; to turn, the car must be moving |
| `speed` | `0`–`100` | scaled by `ROBOT_MAX_THROTTLE` |
| `duration_ms` | `300`–`5000` | run time before the failsafe neutralizes the move |
| `reply` | short text | spoken via Piper (stdin, no shell → no injection) |

**Vision & explore.** On *"look around"* the robot stops, captures a frame, asks `gemma3:4b` what's in
view, records it, and says what it sees. On *"find the sofa"* it runs a **bounded** loop —
stop → look → step toward the sighting (or arc-scan if not seen) — gated by the forward sensor and
capped by `ROBOT_EXPLORE_MAX_ITERS`. Vision is slow on the Pi 5 CPU; the car is held at neutral during
each look. The fixed camera + no-spin steering means "look around" is a coarse arc-scan.

**Memory.** Every sighting is saved to SQLite (`ROBOT_MEMORY_DB`) and a one-line summary of recent
objects is fed back into the prompt, so the robot keeps context across commands and reboots.

**Face.** A small display shows `idle / listening / thinking / talking / exploring / happy / confused /
alarmed / warmup / capturing`. Each state renders the built-in procedural face **or**, if you drop PNG
frames in `brain/faces/<state>/NN.png`, an animation (≥2 frames; `talking` is fast with a random-frame
mouth). It auto-selects a backend (USB-C panel → DSI/HDMI framebuffer → simulated-logs) and is **fully
optional** — a missing panel never blocks driving. See
[`brain/faces/README.md`](brain/faces/README.md) for the per-state frame layout and rules.

**Wake word, audio cues & warmup.** Control is **push-to-talk** (press ENTER over SSH) by default. Set
`ROBOT_WAKEWORD_ENABLE=1` to add an **optional "Richard" wake word** (openWakeWord): the robot listens
continuously and wakes on its name, then records a command exactly as ENTER does — and ENTER still
works. This is also what lets the brain run **headless under `scripts/robot.service`** (which has no
stdin for ENTER). The wake word only *starts* capture; the explore/find autonomy gate is separate and
still required. Because the model is **slow** on the Pi CPU, the robot plays short **earcons**
([`sounds/`](sounds/README.md)) — an ack when it hears you, a *thinking* loop while it works, and a boot greeting — and
**warms the model at boot** (`ROBOT_WARMUP`) with `keep-alive` so the first command isn't a multi-minute
cold load. A single `model_lock` serialises the text-intent and vision calls so they never collide on
the one Ollama model.

**Tuning** (all in `config/robot.env`): the AI stack (`ROBOT_MODEL`, `ROBOT_WHISPER_MODEL`,
`ROBOT_RECORD_SECS`); motion (`ROBOT_MAX_THROTTLE`, `ROBOT_STEER_*`, `ROBOT_OBSTACLE_CM`,
`ROBOT_MAX_MOVE_MS`); sensors (`ROBOT_*_TRIG_PIN`/`_ECHO_PIN`, `ROBOT_SIDE_OBSTACLE_CM`); camera/vision
(`ROBOT_CAMERA_*`, `ROBOT_VISION_TIMEOUT_S`); face (`ROBOT_FACE_*`, `ROBOT_FACE_FRAMES_DIR`,
`ROBOT_FACE_FPS`); explore/find (`ROBOT_EXPLORE_*`, `ROBOT_FIND_CONFIDENCE`, `ROBOT_APPROACH_CM`,
`ROBOT_EXPLORE_TRIGGERS`); model orchestration (`ROBOT_WARMUP`, `ROBOT_OLLAMA_KEEPALIVE`,
`ROBOT_VISION_MODEL`); wake word (`ROBOT_WAKEWORD_*`); audio cues (`ROBOT_SOUNDS_*`,
`ROBOT_THINKING_*`); memory (`ROBOT_MEMORY_*`); and the bench dry-run (`ROBOT_DRY_RUN`,
`ROBOT_FAKE_*`).

After the one-time downloads, disable Wi-Fi — everything runs on-device.

---

## Credits & license

MIT — see [LICENSE](LICENSE). Built on [Ollama](https://ollama.com/) +
[Gemma 3](https://ai.google.dev/gemma), [whisper.cpp](https://github.com/ggerganov/whisper.cpp),
[Piper](https://github.com/rhasspy/piper), [picamera2](https://github.com/raspberrypi/picamera2),
[gpiozero](https://gpiozero.readthedocs.io/),
[Adafruit ServoKit / PCA9685](https://github.com/adafruit/Adafruit_CircuitPython_ServoKit),
[openWakeWord](https://github.com/dscripka/openWakeWord), and
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python).
