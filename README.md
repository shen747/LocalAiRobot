# LocalAiRobot

> A mecanum-wheel robot car you can **talk to** — running a local LLM, speech-to-text, and
> text-to-speech **entirely on a Raspberry Pi 5 (16GB)**. No cloud. No Wi-Fi. Fully offline.
> It drives on voice command, **stops itself when it detects an obstacle directly ahead**, and talks back.

<!-- TODO: drop a hero GIF here once filmed (see Phase 9 of the plan). -->

```
 "drive forward"  ──▶  🎙️ mic ──▶ whisper.cpp ──▶ gemma3:4b ──▶ {action,duration,speed,reply}
                                                                      │            │
                                                                      ▼            ▼
                                                            🔊 Piper TTS     ⚙️ Pico W → 4× mecanum wheels
```

**What v1 is:** voice teleoperation with a forward safety stop.
**What v1 is *not*:** self-driving / obstacle *avoidance* / wake-word. Those are [stretch goals](PROJECT_IMPLEMENTATION_PLAN.md#stretch-goals).

---

## How it works

Two boards split the work over a single USB-serial link:

| Role | Board | Responsibilities |
|------|-------|------------------|
| **Brain (master)** | Raspberry Pi 5 (16GB) | Audio capture, STT, LLM reasoning, TTS playback, command supervision |
| **Nervous system (slave)** | Pico W (on the Freenove PCB) | Real-time motor PWM, mecanum kinematics, sensor reads, **hardware watchdog auto-stop** |

The Pi runs the whole AI stack locally — **Ollama** (`gemma3:4b`), **whisper.cpp** (`base.en`), and
**Piper** (`en_US-lessac-medium`). The Pico keeps hard real-time control of the motors and stops the
car on its own within 1.5 s if the link ever drops.

Interaction is **push-to-talk**: press <kbd>ENTER</kbd> over SSH, speak during the record window, then
wait for the spoken reply. This deliberately avoids self-listening feedback loops and Whisper's
silence hallucinations. See [§1.4 of the plan](PROJECT_IMPLEMENTATION_PLAN.md#14-interaction-model-push-to-talk-half-duplex-audio).

---

## Repository layout

```
LocalAiRobot/
├── README.md                       # you are here
├── PROJECT_IMPLEMENTATION_PLAN.md  # the full end-to-end build document
├── LICENSE                         # MIT
├── .gitignore
├── firmware/
│   └── main.py                     # Pico W: serial listener + mecanum + watchdog
├── brain/
│   ├── go.mod                      # Go module
│   └── main.go                     # Pi 5 orchestrator (listen → think → speak → move)
├── config/
│   └── robot.env                   # runtime config (env vars)
├── scripts/
│   ├── install_stack.sh            # one-shot installer (Go + Ollama + whisper.cpp + Piper)
│   └── robot.service               # systemd unit
└── docs/
    └── assembly.md                 # build / wiring notes
```

---

## Shopping list (abridged)

Full bill of materials with prices and warnings is in
[§3 of the plan](PROJECT_IMPLEMENTATION_PLAN.md#3-bill-of-materials-shopping-list).

- **Freenove Mecanum Wheel Car Kit for Raspberry Pi Pico W** (chassis, 4 motors + mecanum wheels, motor-driver PCB, forward ultrasonic)
- **Raspberry Pi 5 — 16GB** + **official Active Cooler** (cooler is mandatory for sustained inference)
- **GeeekPi UPS HAT** — *verify its 5V output is rated ≥ 4A* — + **2× genuine high-drain 18650 cells**
- USB microphone + small powered speaker
- NVMe SSD + M.2 HAT (recommended) **or** a high-endurance 128GB microSD
- Short **USB-A → micro-USB** cable (Pi 5 → Pico W command link)

> ⚠️ **5V on a 3.3V pin:** a bare HC-SR04 drives ECHO at 5V and the RP2040 is **not** 5V-tolerant.
> Use the Freenove level-shifted header, a 1kΩ/2kΩ divider, or an HC-SR04P. See the
> [hardware safety note](PROJECT_IMPLEMENTATION_PLAN.md#phase-5-pico-w-firmware).

---

## Quickstart

> This assumes the car is assembled and Raspberry Pi OS Lite (64-bit) is flashed with SSH enabled.
> Full hardware/OS steps: [Phases 2–3 of the plan](PROJECT_IMPLEMENTATION_PLAN.md#phase-2-hardware-assembly).

### 1. Flash the Pico firmware

Open `firmware/main.py` in **Thonny** (interpreter: *MicroPython (Raspberry Pi Pico)*), **update the
motor/sensor pins** to match your Freenove board revision, and save it onto the Pico as `main.py` so
it auto-runs on boot. On boot it prints `LOG Pico ready`.

### 2. Install the offline AI stack on the Pi (needs internet for this step only)

```bash
git clone https://github.com/<you>/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot
chmod +x scripts/install_stack.sh
./scripts/install_stack.sh
```

This installs Go, Ollama + `gemma3:4b`, whisper.cpp + `base.en`, Piper + a voice, and builds the brain.

### 3. Configure

Edit `config/robot.env`: replace `YOUR_USER` with your Linux username and set `ROBOT_MIC` to the
device from `arecord -l` (e.g. `plughw:1,0`). Confirm the Pico port with `ls /dev/ttyACM*` and make
sure your user is in the `dialout` group (`sudo usermod -aG dialout $USER`, then re-login).

### 4. Talk to it

```bash
set -a; source config/robot.env; set +a
./brain/brain
```

```
Robot Brain online.
Push-to-talk: press ENTER, speak after the prompt, then wait.

[ENTER]=talk  q=quit > ⏎
Heard: "drive forward"
Action=FWD dur=2000ms speed=40 reply="Driving forward."
Pico: OK FWD
```

> 🛟 **Keep the wheels off the ground** until you've completed the bring-up order in
> [Phase 7](PROJECT_IMPLEMENTATION_PLAN.md#phase-7-integration-safety-and-tuning).

---

## Safety (defense in depth)

- **Pico hardware watchdog** auto-STOPs if no fresh command arrives within 1500 ms (a dropped USB link always stops the car).
- **Ultrasonic gate** refuses `FWD` under `OBSTACLE_CM` and emits `EVT OBSTACLE`, which also cancels motion on the Pi side.
- **Action whitelist** on the Pi: any unknown LLM action collapses to `STOP`; speed and duration are clamped to safe ranges.
- **No shell for TTS:** Piper is invoked directly with text via stdin, so an LLM reply can never inject shell commands.
- **Half-duplex audio lock** so the mic is never open while the speaker is talking (no self-listening loop).
- Keep a **physical power kill switch** on the car battery within reach while testing.

---

## Going offline

After the one-time downloads, disable Wi-Fi and everything still runs on-device — Ollama, whisper.cpp,
and Piper are all local. That "Wi-Fi off, still works" moment is the proof shot for the
[video plan](PROJECT_IMPLEMENTATION_PLAN.md#phase-9-youtube-video-plan).

---

## Documentation

- 📘 **[PROJECT_IMPLEMENTATION_PLAN.md](PROJECT_IMPLEMENTATION_PLAN.md)** — the complete end-to-end build: architecture, BOM, wiring, firmware, orchestrator, serial protocol, latency/power budgets, testing checklist, and troubleshooting.
- 🔧 **[docs/assembly.md](docs/assembly.md)** — build and wiring notes.

---

## Credits & license

MIT — see [LICENSE](LICENSE).

Built on the work of [Freenove](https://www.freenove.com/) (chassis/kit),
[Ollama](https://ollama.com/) + [Google Gemma 3](https://ai.google.dev/gemma) (LLM),
[`ggml-org/whisper.cpp`](https://github.com/ggerganov/whisper.cpp) (STT), and
[`rhasspy/piper`](https://github.com/rhasspy/piper) (TTS). Respect each project's license and model
usage terms when redistributing.
