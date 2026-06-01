# LocalAiRobot

> A mecanum-wheel robot car you can **talk to** — running a local LLM, speech-to-text, and
> text-to-speech **entirely on a Raspberry Pi 5 (16GB)**. No cloud. No Wi-Fi. Fully offline.
> It drives on voice command, **stops itself when it detects an obstacle directly ahead**, and talks back.

<!-- TODO: drop a hero GIF here once filmed. -->

```
 "drive forward"  ──▶  🎙️ mic ──▶ whisper.cpp ──▶ gemma3:4b ──▶ {action,duration,speed,reply}
                                                                      │            │
                                                                      ▼            ▼
                                                            🔊 Piper TTS     ⚙️ Pico W → 4× mecanum wheels
```

**What v1 is:** voice teleoperation with a forward safety stop.
**What v1 is *not*:** self-driving, obstacle *avoidance*, or wake-word — those are future work.

---

## How it works

Two boards split the work over a single USB cable, which carries **both power to the Pico and the
serial command link**:

| Role | Board | Responsibilities |
|------|-------|------------------|
| **Brain (master)** | Raspberry Pi 5 (16GB) | Audio capture, speech-to-text, LLM reasoning, text-to-speech, command supervision |
| **Nervous system (slave)** | Pico W (on the Freenove PCB) | Real-time motor PWM, mecanum kinematics, sensor reads, **hardware watchdog auto-stop** |

The Pi runs the whole AI stack locally — **Ollama** (`gemma3:4b`), **whisper.cpp** (`base.en`), and
**Piper** (`en_US-lessac-medium`). The Pico keeps hard real-time control of the motors and stops the
car on its own within 1.5 s if the link ever drops.

Interaction is **push-to-talk**: press <kbd>ENTER</kbd> over SSH, speak during the record window, then
wait for the spoken reply. This deliberately avoids self-listening feedback loops and Whisper's
silence hallucinations.

---

## Two boards, two completely separate software installs

This is the part people get wrong. **Nothing is shared except the one file's protocol and the USB cable.**

| | 🧠 Raspberry Pi 5 | ⚙️ Pico W |
|---|---|---|
| **What you install** | Go, Ollama + `gemma3:4b`, whisper.cpp + `base.en`, Piper + a voice, and the compiled `brain` binary | The **MicroPython runtime** + the single file `firmware/main.py` (saved on the Pico as `main.py`) |
| **How** | `scripts/install_stack.sh` (one shot, needs internet once) | Flash MicroPython UF2, then copy the file with **Thonny** |
| **Runs** | Linux (Raspberry Pi OS Lite 64-bit) | Bare MicroPython, auto-runs `main.py` on power-up |

The Pico has **no** Python packages, no Ollama, no AI — just that one ~130-line file. The Pi 5 has the
entire AI stack. They only ever exchange short ASCII lines (`FWD`, `STOP`, `OK FWD`, `DIST 42.0`, …)
over USB serial at 115200 8N1.

---

## Repository layout

```
LocalAiRobot/
├── README.md                       # you are here
├── LICENSE                         # MIT
├── .gitignore
├── firmware/
│   └── main.py                     # → goes on the Pico W (serial listener + mecanum + watchdog)
├── brain/
│   ├── go.mod                      # Go module
│   └── main.go                     # → runs on the Pi 5 (listen → think → speak → move)
├── config/
│   └── robot.env                   # runtime config (env vars) for the Pi
├── scripts/
│   ├── install_stack.sh            # one-shot Pi installer (Go + Ollama + whisper.cpp + Piper)
│   └── robot.service               # optional systemd unit
└── docs/
    └── assembly.md                 # build / wiring reference
```

---

## Before you start

This guide assumes the **hardware is already assembled** (Freenove car built with the Pico W seated,
Pi 5 + active cooler + UPS mounted, mic/speaker plugged into the Pi, and the **Pi USB-A → Pico
micro-USB** cable connected) and that **Raspberry Pi OS Lite (64-bit)** is flashed on the Pi with
SSH enabled. Build/wiring details are in [docs/assembly.md](docs/assembly.md).

You will need, on a laptop/desktop for the Pico step:
- A USB cable to the Pico, and **VS Code** with the **[MicroPico](https://marketplace.visualstudio.com/items?itemName=paulober.pico-w-go)** extension (`paulober.pico-w-go`) installed. (Prefer a zero-setup tool instead? [Thonny](https://thonny.org/) works too — see the note at the end of Step 2.)

> ⚠️ **5V on a 3.3V pin (read before wiring the ultrasonic).** A bare HC-SR04 drives its ECHO line at
> **5V**, but the Pico's RP2040 GPIO is **not 5V-tolerant** — connecting 5V ECHO straight to a pin can
> damage the board. Use the Freenove PCB's level-shifted ultrasonic header, **or** a divider (1kΩ from
> ECHO→pin, 2kΩ from pin→GND), **or** a 3.3V HC-SR04P module.

---

## Setup — step by step

### Step 1 — Map the Pico pins (do this BEFORE flashing)

The motor and sensor pin numbers at the top of [`firmware/main.py`](firmware/main.py) are
**placeholders**. The car will not move correctly until they match **your** Freenove board revision.

1. **Get your board's pinout.** Open the tutorial/datasheet for your exact kit (Freenove publishes a
   PDF + GitHub repo per kit, e.g. *"Mecanum Wheel Car Kit for Raspberry Pi Pico"*). Find the
   pin-assignment table or schematic.
2. **Identify the GPIO (`GPxx`) numbers** — not the physical pin numbers — wired to:
   - each motor's **two** driver inputs (one drives it forward, one backward): front-left, front-right,
     rear-left, rear-right;
   - the ultrasonic **TRIG** and **ECHO**.
3. **Edit `firmware/main.py`** to match. Defaults shipped in the file:

   | Signal | Default in file | Change to |
   |---|---|---|
   | `FL = Motor(2, 3)` | GP2 / GP3 | your front-left `(fwd, bwd)` GPIO numbers |
   | `FR = Motor(4, 5)` | GP4 / GP5 | your front-right |
   | `RL = Motor(6, 7)` | GP6 / GP7 | your rear-left |
   | `RR = Motor(8, 9)` | GP8 / GP9 | your rear-right |
   | `TRIG = Pin(16, …)` | GP16 | your ultrasonic TRIG |
   | `ECHO = Pin(17, …)` | GP17 | your ultrasonic ECHO (level-shifted!) |

4. **If a motor later spins the wrong way**, swap that motor's two numbers (e.g. `Motor(2, 3)` →
   `Motor(3, 2)`). You'll confirm directions in Step 3.

### Step 2 — Put MicroPython + the firmware on the Pico W (from VS Code)

1. **Flash the MicroPython runtime** (only needed once per Pico — this part uses no IDE):
   - Hold the Pico's **BOOTSEL** button while plugging it into your computer → it mounts as a USB
     drive named `RPI-RP2`.
   - Download the **Pico W** MicroPython `.uf2` from <https://micropython.org/download/RPI_PICO_W/>.
   - Drag the `.uf2` onto the `RPI-RP2` drive. The Pico reboots running MicroPython.
2. **Open the project in VS Code** with the **MicroPico** extension installed. Open the `LocalAiRobot`
   folder, then connect: open the Command Palette (<kbd>Ctrl/Cmd</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>) and
   run **`MicroPico: Connect`** (the status bar shows the connected port). If it can't find the board,
   pick the port with **`MicroPico: Configure project`**.
3. **Upload the firmware as `main.py`.** Open your edited [`firmware/main.py`](firmware/main.py), then
   run **`MicroPico: Upload current file to Pico`** from the Command Palette. The file **must** land on
   the Pico as `main.py` (root of the device filesystem) so it auto-runs on boot, replacing Freenove's
   default firmware. Then run **`MicroPico: Soft reset`** (or tap the Pico's reset) to start it.
4. **Quick check in the MicroPico REPL** (keep wheels off the ground): run **`MicroPico: Open REPL`** —
   on reset you should see `LOG Pico ready`. The running loop owns stdin, so to test a single reading,
   stop it first (<kbd>Ctrl</kbd>+<kbd>C</kbd> in the REPL) and type `print(distance_cm())` (returns a
   number). To watch motion, instead send line commands while the loop runs — type `FWD` then `STOP`
   and watch each wheel. Fix pin order/polarity now if any direction is wrong (Step 1), and confirm
   **strafe slides** rather than rotates (if it rotates, a wheel-roller pair or a motor pair is swapped).
5. **Disconnect MicroPico** when done (**`MicroPico: Disconnect`**) — it holds the serial port, and the
   Pi needs that port next.

> **Prefer the command line?** With the official MicroPython tool `mpremote` (`pip install mpremote`)
> the whole step is two commands — no IDE at all:
> ```bash
> mpremote connect /dev/ttyACM0 cp firmware/main.py :main.py   # upload as main.py
> mpremote connect /dev/ttyACM0 repl                           # watch boot output / test
> ```
> **Prefer zero setup?** [Thonny](https://thonny.org/) also works: set *Tools ▸ Options ▸ Interpreter ▸
> MicroPython (Raspberry Pi Pico)*, open `firmware/main.py`, *Save as… ▸ Raspberry Pi Pico ▸ `main.py`*,
> and use its Shell for the same checks. Whichever tool you use, **close it afterward** so it releases
> the serial port.

### Step 3 — Install the AI stack on the Raspberry Pi 5 (needs internet for this step only)

SSH into the Pi, then:

```bash
git clone https://github.com/<you>/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot
chmod +x scripts/install_stack.sh
./scripts/install_stack.sh
```

This installs Go, Ollama + `gemma3:4b`, whisper.cpp + `base.en`, Piper + a voice, and builds the
`brain` binary into `brain/brain`. Verify the model is served:

```bash
ollama list            # should list gemma3:4b
```

### Step 4 — Configure the Pi (`config/robot.env`)

```bash
# Find your mic's ALSA device  →  e.g. "card 1, device 0" means plughw:1,0
arecord -l
# Find the Pico's serial port (usually /dev/ttyACM0)
ls /dev/ttyACM*
# Grant serial access without sudo, then LOG OUT AND BACK IN for it to apply
sudo usermod -aG dialout $USER
```

Edit [`config/robot.env`](config/robot.env):
- replace **`YOUR_USER`** (in the whisper/piper paths) with your Linux username,
- set **`ROBOT_MIC`** to the `plughw:x,y` string from `arecord -l`,
- set **`ROBOT_PICO_PORT`** if your Pico isn't on `/dev/ttyACM0`.

Optional audio sanity check (same 16 kHz mono format the app uses):

```bash
arecord -D plughw:1,0 -d 3 -f S16_LE -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav
```

### Step 5 — Bring-up on blocks (keep the wheels OFF the ground)

Verify each piece before the full loop:

```bash
# Serial link to the Pico (close Thonny first):
sudo apt install -y screen
screen /dev/ttyACM0 115200      # type PING → PONG ; FWD → wheels spin, ~1.5s later: LOG watchdog stop
                                # exit screen: Ctrl-a then k then y
```

The `LOG watchdog stop` after ~1.5 s proves the hardware safety watchdog works.

### Step 6 — Talk to it

```bash
cd ~/LocalAiRobot
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

> 🛟 **Only put the car on the floor after Step 6 succeeds on blocks.** Start with low speed and short
> durations. Keep a hand on the car battery's power switch.

> Note: the brain reads stdin (push-to-talk), so run it **interactively over SSH**. The provided
> `scripts/robot.service` is for restart plumbing only — as a boot service it idles waiting for input
> until you add a wake word or GPIO button.

---

## Safety (defense in depth)

- **Pico hardware watchdog** auto-STOPs if no fresh command arrives within 1500 ms (a dropped USB link always stops the car).
- **Ultrasonic gate** refuses `FWD` under `OBSTACLE_CM` and emits `EVT OBSTACLE`, which also cancels motion on the Pi side.
- **Action whitelist** on the Pi: any unknown LLM action collapses to `STOP`; speed and duration are clamped to safe ranges.
- **No shell for TTS:** Piper is invoked directly with text via stdin, so an LLM reply can never inject shell commands.
- **Half-duplex audio lock** so the mic is never open while the speaker is talking (no self-listening loop).
- Keep a **physical power kill switch** on the car battery within reach while testing.

---

## Tuning knobs

| Where | Knob | Effect |
|---|---|---|
| `config/robot.env` | `ROBOT_MODEL` | swap the LLM (e.g. `gemma3:1b` for faster demos, `gemma3:12b` for richer replies) |
| `config/robot.env` | `ROBOT_WHISPER_MODEL` | STT model path (`tiny.en` is faster, `base.en` more accurate) |
| `config/robot.env` | `ROBOT_RECORD_SECS` | length of the push-to-talk record window |
| `firmware/main.py` | `SPEED` | default motor duty |
| `firmware/main.py` | `OBSTACLE_CM` | forward stop distance |
| `firmware/main.py` | `MOVE_TIMEOUT_MS` | watchdog timeout |

---

## Going offline

After the one-time downloads in Step 3, disable Wi-Fi and everything still runs on-device — Ollama,
whisper.cpp, and Piper are all local. Re-run the bring-up to prove the "Wi-Fi off, still works" claim.

---

## Credits & license

MIT — see [LICENSE](LICENSE).

Built on the work of [Freenove](https://www.freenove.com/) (chassis/kit),
[Ollama](https://ollama.com/) + [Google Gemma 3](https://ai.google.dev/gemma) (LLM),
[whisper.cpp](https://github.com/ggerganov/whisper.cpp) (STT), and
[Piper](https://github.com/rhasspy/piper) (TTS). Respect each project's license and model
usage terms when redistributing.
