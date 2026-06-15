# LocalAiRobot

> A 1/10 RC crawler you can **talk to** — local LLM, speech-to-text, text-to-speech, **and a
> camera**, running **entirely on a Raspberry Pi 5 (16GB)**. No cloud, no Wi-Fi, fully offline.
> It drives on voice command, **stops for an obstacle directly ahead**, **looks around and drives
> toward things you name**, shows an **expressive face**, and talks back.

![LocalAiRobot voice + vision control flow: a voice command — started by a wake word (openWakeWord; ENTER push-to-talk survives in --dry only) — is transcribed by whisper.cpp, passes a trigger-phrase gate (EXPLORE/FIND only on explore words, otherwise DRIVE), and the gemma4:e2b-it-qat text model (Ollama) turns it into an intent JSON using recent context from a SQLite environment memory; DRIVE/STOP go to the driver.py control loop while EXPLORE/FIND run a behaviour loop that stops the car, captures a Pi Camera V2 NoIR frame, asks the moondream VLM what is visible, records it to memory, and takes gated drive steps; a 3-sensor HC-SR04P array (front/left/right) gates FORWARD and informs steering; the PCA9685 drives the ESC (Electronic Speed Controller) / drive motor and steering servo; the text-intent and vision calls share one Ollama server behind a single model_lock (warmed at boot); replies are spoken via Piper TTS alongside short ack/greeting/thinking earcons, and a USB-C face screen reflects state (idle/listening/thinking/happy/confused/alarmed/warmup/capturing)](docs/images/basic_control_flow.png)

The Pi 5 does everything: captures voice, transcribes it (**whisper.cpp**), turns it into a small JSON
intent (**Ollama** `gemma4:e2b-it-qat`; a separate **`moondream`** VLM reads the camera frames), speaks a reply
(**Piper**), shows a face, and drives the car. A **PCA9685** over I2C generates the 50 Hz PWM for the
**ESC (Electronic Speed Controller)** (Electronic Speed Controller — the throttle/motor driver between battery and motor) and the
steering servo, jitter-free even under LLM load; a **3-sensor HC-SR04P** array (front/left/right) cuts
forward motion under a set distance and keeps steering off near walls. Steering is **Ackermann** (drive + steer,
no strafe/spin). Control is the **wake word** (openWakeWord, on by default — stock *"hey jarvis"*; it also
lets the robot run headless as a service). In `--dry` bench mode you trigger it with **ENTER** instead.

**Autonomy is opt-in.** Ordinary commands ("go forward", "turn left", "stop") are plain teleop. The
camera-driven **look-around / find-the-sofa** behaviour only runs when you actually say an explore
phrase ("look around", "find the …", "go to the …") — a deterministic trigger gate checks the raw
transcript, so a misheard command can never send the robot wandering.

**Is:** voice teleop + a forward safety stop + opt-in, bounded, vision-guided explore/find + plain-chat
replies + a teachable people/things memory + an **optional wake word**. **Is not:** self-driving, obstacle *avoidance* /
path-planning, or SLAM. Vision on the Pi 5 CPU is **slow** (tens of seconds to minutes per look), so
"find the X" is a deliberate stop-look-step loop, not real-time navigation.

> 🛟 **Safety — no hardware watchdog.** A software failsafe returns the ESC (Electronic Speed Controller) to neutral on STOP / error /
> obstacle / move-timeout and arms it at neutral on boot — **but if the process is killed, the PCA9685
> holds the last pulse.** Keep a **physical kill switch on the motor battery** in reach, and start slow.
> On startup, a missing PCA9685 or sensor is **logged by name** and the brain runs degraded (motors off)
> instead of crashing.

---

## Repository layout

```
brain/main.py            # orchestrator: listen → think → (drive | explore | find) → speak; face events
brain/driver.py          # PCA9685 ESC (Electronic Speed Controller) + steering servo, 3x HC-SR04P, software failsafe, dry-run fakes
brain/camera.py          # Pi Camera V2 NoIR capture → preprocessed JPEG (picamera2)
brain/vision.py          # send a frame to the vision VLM (moondream) via Ollama → structured scene JSON
brain/memory.py          # SQLite environment memory: record sightings, summarise back to the prompt
brain/behavior.py        # bounded autonomous explore / find-and-drive-to-object loop
brain/face.py            # expression face: USB-C panel | DSI/HDMI framebuffer | simulated (+ PNG animation)
brain/inference.py       # one keep-alive + model_lock gateway to Ollama (text & vision share it; warmup)
brain/sounds.py          # ack / greeting / thinking earcons (aplay, half-duplex); dry-run logs only
brain/wakeword.py        # the wake word (openWakeWord); the ENTER trigger survives only in --dry
brain/requirements.txt   # Pi 5 deps (ServoKit, gpiozero, lgpio, Pillow, numpy, pyserial; + openwakeword)
config/robot.env         # runtime config (env vars)
config/README.md         # every ROBOT_* setting explained (default + what each does)
sounds/                  # spoken earcon WAVs (greeting/ack/thinking/error) — generate with Piper, see sounds/README.md
brain/faces/             # per-state PNG face frames (none = built-in procedural) — see brain/faces/README.md
scripts/install_stack.sh # one-shot Pi installer (Ollama + whisper.cpp + Piper + picamera2 + venv)
scripts/run_me.sh        # run the brain: --dry/--live (foreground), --detach (bg), --reload, --install-boot
scripts/shutdown.sh      # stop the brain gracefully; --poweroff also halts the Pi
scripts/setup_audio.sh   # point ALSA's default output at your USB speaker (writes ~/.asoundrc)
scripts/test_sensors.sh  # read the 3 HC-SR04P sensors live (bench check before trusting the gate)
scripts/host_setup.sh    # install the asset-authoring toolkit on YOUR PC (WSL/macOS/Linux) for sounds/faces
scripts/generate_faces.sh  # render procedural face PNG templates to customize (run on your PC)
scripts/rag.sh           # inspect/edit the SQLite memory store (taught people/things, sightings)
scripts/cleanup_pi.sh    # uninstall: dry-run + confirm, then remove downloads/models/venv/data
scripts/robot.service    # systemd unit for boot autostart (install: ./scripts/run_me.sh --install-boot)
tools/wakeword-training/ # PC workspace to train a custom wake word — see its README + docs/opnewakeword/
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
| USB speaker (**USB-powered**) | `mini USB powered speaker stereo` | TTS + earcon output. A **USB-powered** speaker (e.g. **Naciffo 2**) needs no separate PSU — ideal on a battery robot; plug-and-play, no 3.5 mm jack. **Any** USB speaker works — it's only a config value (`ROBOT_SOUNDS_DEVICE` + `~/.asoundrc`), no code change. |

**Vision & display:**

| Part | Search term | Notes |
|---|---|---|
| IMX219 camera | `Raspberry Pi Camera V2 NoIR IMX219` **or** `Arducam IMX219 8MP Raspberry Pi 5` | visual input for the VLM. **Same IMX219 sensor** either way — interchangeable, no code change. **NoIR** = no IR-cut (reddish daylight, sees with IR light); a standard module = natural daylight colour |
| **Pi 5 camera adapter cable** ⚠️ | `Raspberry Pi 5 camera cable 22-pin 0.5mm to 15-pin 1mm` | the Pi 5 CSI port is the narrow 22-way type; a bare V2's 15-way cable **won't fit** without this. **Some Arducam kits bundle it** (their "Pi Zero cable" is 22→15) — check before buying separately |
| IR illuminator (optional) | `850nm IR LED illuminator board` | a NoIR module sees nothing in the dark without IR light; skip for lit rooms / a standard module |
| Face screen — pick one | **USB-C:** `3.5 inch USB Type-C secondary screen 320x480 IPS` · **DSI:** `Freenove 5 inch DSI touchscreen 800x480 Raspberry Pi` | the robot's face. **USB-C** = the TURZX/"NO AIDA64" panel (USB-serial, `turing` backend). **DSI** = a ribbon panel like the Freenove 5" 800×480 (`framebuffer` backend, `/dev/fb0`). Either works — the code auto-detects; touch (if any) is unused. See [assembly](docs/assembly.md) |

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
git clone https://github.com/YOUR_GITHUB_USER/LocalAiRobot.git ~/LocalAiRobot   # put your GitHub username in place of YOUR_GITHUB_USER
cd ~/LocalAiRobot && chmod +x scripts/install_stack.sh && ./scripts/install_stack.sh
```
Installs Ollama + `gemma4:e2b-it-qat`, whisper.cpp + `base.en`, Piper + voice, **picamera2** (apt) and the
**USB-C face-panel library**, enables I2C, adds you to the `i2c`/`gpio`/`dialout`/`audio`/`video`
groups, and builds the venv at `brain/.venv` **with `--system-site-packages`** (required so it can see
picamera2/libcamera).

**2. Reboot and verify.**
```bash
sudo reboot
ollama list                     # lists gemma4:e2b-it-qat (text) + moondream (vision)
i2cdetect -y 1                  # PCA9685 at 0x40
rpicam-hello --list-cameras     # the IMX219 NoIR (needs the Pi-5 22→15 adapter cable)
lsusb | grep -i 1a86            # the USB-C face panel (CH340 serial), if connected
arecord -l                      # find your mic → plughw:x,y ;  aplay -l → the USB speaker card
```

**3. Configure** [`config/robot.env`](config/robot.env): set `YOUR_USER` in the whisper/piper paths,
`ROBOT_MIC`, and the sensor pins. Camera/face/explore/memory plus the wake-word/audio-cue/warmup
knobs have working defaults (the wake word is **on** by default). (If playback is silent, make the
USB sound card the default ALSA output.)

**4. 🧪 Bench bring-up FIRST (no chassis, no motor) — watch the model→hardware mapping.**
```bash
./scripts/run_me.sh --dry        # foreground, interactive; nothing drives (it loads robot.env for you)
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
./scripts/run_me.sh --live       # foreground, real hardware
```
```
[ENTER]=talk  q=quit > ⏎
Heard: "find the sofa"
action=FIND target='sofa'
robot.behavior INFO FIND 'sofa' SUCCESS at iter 2 (front=38cm)
```
Only put the car on the floor after step 5 passes, at low `ROBOT_MAX_THROTTLE`, kill switch in hand.

**Day-to-day, one launcher** (it sources `config/robot.env` for you): `./scripts/run_me.sh` runs the brain
in the **foreground** — `--dry` (nothing drives) or `--live` (real hardware), Ctrl-C to stop. For
untethered running use `--detach` (background; stop with `./scripts/shutdown.sh`) or `--install-boot`
(auto-start on every boot); `--reload` re-applies an edited `config/robot.env`. See
[docs/assembly.md](docs/assembly.md) → "Run it day-to-day".

---

## Voice command model

The LLM returns one JSON object; the Pi validates and clamps it before acting:

| Key | Values | Notes |
|---|---|---|
| `action` | `DRIVE` / `EXPLORE` / `FIND` / `STOP` / `SAY` / `REMEMBER` | default `DRIVE`. **`EXPLORE`/`FIND` only run if an explore phrase is in the transcript** (else → `DRIVE`). `SAY` = answer in words, no motion. `REMEMBER` = photograph + store a named person/thing |
| `target` | object name (`FIND`) or a person/thing **name** (`REMEMBER`, e.g. `"Shani"`) | required by `FIND` and `REMEMBER` |
| `note` | relationship / description for `REMEMBER` (e.g. `"my wife"`) | optional |
| `drive` | `FORWARD` / `REVERSE` / `STOP` | used by `DRIVE`; unknown → `STOP` (fail-safe) |
| `steer` | `LEFT` / `CENTER` / `RIGHT` | unknown → `CENTER`; to turn, the car must be moving |
| `speed` | `0`–`100` | scaled by `ROBOT_MAX_THROTTLE` |
| `duration_ms` | `300`–`5000` | run time before the failsafe neutralizes the move |
| `reply` | short text | spoken via Piper (stdin, no shell → no injection) |

**Vision & explore.** On *"look around"* the robot stops, captures a frame, asks the dedicated `moondream`
VLM what's in view, records it, and says what it sees. On *"find the sofa"* it runs a **bounded** loop —
stop → look → step toward the sighting (or arc-scan if not seen) — gated by the forward sensor and
capped by `ROBOT_EXPLORE_MAX_ITERS`. Vision is slow on the Pi 5 CPU; the car is held at neutral during
each look. The fixed camera + no-spin steering means "look around" is a coarse arc-scan.

**Memory & teaching it people/things.** Every sighting is saved to SQLite (`ROBOT_MEMORY_DB`). Say
*"this is my wife Shani"* (a **`REMEMBER`** command) and it photographs what its camera sees, has the
VLM describe the shot, and stores **name + relationship + description + the JPEG** in a `facts` table.
Each turn it injects the known people/things *and* recently seen objects into gemma's prompt — a
lightweight RAG (prompt-injected, no vector DB; fine for a handful of entries) — so context carries
across commands and reboots. **`SAY`** handles plain chat/questions: it answers in words and stays put.

**Face.** A small display shows `idle / listening / thinking / talking / exploring / happy / confused /
alarmed / warmup / capturing`. Each state renders the built-in procedural face **or**, if you drop PNG
frames in `brain/faces/<state>/NN.png`, an animation (≥2 frames; `talking` is fast with a random-frame
mouth). It auto-selects a backend (USB-C panel → DSI/HDMI framebuffer → simulated-logs) and is **fully
optional** — a missing panel never blocks driving. To design your own face frames (and the spoken
earcons) **on your PC** — `./scripts/host_setup.sh` installs the tools — and ship them to the robot, see
[`brain/faces/README.md`](brain/faces/README.md) and [`sounds/README.md`](sounds/README.md).

**Wake word, audio cues & warmup.** Control is the **wake word** (openWakeWord, **on by default** —
stock *"hey jarvis"*, or **train your own on your PC**: [`tools/wakeword-training/`](tools/wakeword-training/README.md)):
the robot listens continuously and wakes on it, then records a command. In
**live this is the only trigger** (if it can't load, the brain exits — `q` over SSH still quits); the
**ENTER trigger remains only in `--dry`** bench mode. Continuous listening is also what lets the brain
run **headless under `scripts/robot.service`** (no stdin needed). The wake word only *starts* capture; the explore/find autonomy gate is separate and
still required. Because the model is **slow** on the Pi CPU, the robot plays short **earcons**
([`sounds/`](sounds/README.md)) — an ack when it hears you, a *thinking* loop while it works, and a boot greeting — and
**warms the model at boot** (`ROBOT_WARMUP`) with `keep-alive` so the first command isn't a multi-minute
cold load. A single `model_lock` serialises the text-intent and vision calls so two generations never run at once
on the Pi's CPU.

**Tuning.** Every setting lives in [`config/robot.env`](config/robot.env) — the AI stack, motion,
sensors, camera/vision, face, explore/find, wake word, audio cues, memory, and the bench dry-run. Each
key is documented (its default + what it does) in **[`config/README.md`](config/README.md)**.

After the one-time downloads, disable Wi-Fi — everything runs on-device.

---

## Credits & license

MIT — see [LICENSE](LICENSE). Built on [Ollama](https://ollama.com/) +
[Gemma](https://ai.google.dev/gemma) + [moondream](https://moondream.ai/), [whisper.cpp](https://github.com/ggerganov/whisper.cpp),
[Piper](https://github.com/rhasspy/piper), [picamera2](https://github.com/raspberrypi/picamera2),
[gpiozero](https://gpiozero.readthedocs.io/),
[Adafruit ServoKit / PCA9685](https://github.com/adafruit/Adafruit_CircuitPython_ServoKit),
[openWakeWord](https://github.com/dscripka/openWakeWord), and
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python).
