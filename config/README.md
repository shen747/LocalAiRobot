# Configuration reference (`config/robot.env`)

Every runtime setting is an environment variable in [`robot.env`](robot.env). The brain reads them from
the **shell environment**, not the file directly, so an edit only takes effect once the file is
(re)loaded:

- `scripts/run_me.sh` / `scripts/shutdown.sh` **source it for you** on every launch.
- `install_stack_pi5.sh` adds an auto-load line to `~/.bashrc`, so **new SSH sessions** load it too.
- After editing it in a shell you keep using, re-source by hand: `set -a; source config/robot.env; set +a`.
- For a robot that's already running detached / as the boot service: `./scripts/run_me.sh --reload`.

**Paths contain `YOUR_USER`** — replace it with your Linux username once:
`sed -i "s/YOUR_USER/$(whoami)/g" config/robot.env`. Booleans are `1`/`0`. `~` expands to your home.

---

## Mode (bench dry-run)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_DRY_RUN` | `0` | `1` = no real PCA9685/ESC/servo/sensors needed: `driver.py` swaps in fakes and **logs** every intended GPIO/PWM/I2C action. Use `1` on the bench, `0` to drive real hardware. (`ROBOT_SIMULATE` is an alias.) |
| `ROBOT_MOTORS_OFF` | `0` | `1` = **stationary / tabletop** mode: runs **fully live** (wake word, voice, camera, sensors, RAG, face) but the **ESC + steering get no PWM** — the console still logs the throttle/steer commands (like dry-run) yet the **wheels never move**. For talking to the assembled car on a desk without it driving off. Needs `ROBOT_DRY_RUN=0` (dry-run already fakes everything). |
| `ROBOT_FAKE_DISTANCE_CM` | `200` | Dry-run synthetic **forward** distance fed to the gate; set `<ROBOT_OBSTACLE_CM` to make the obstacle stop trip without a sensor. |

## Robot identity

| Key | Default | What it does |
|---|---|---|
| `ROBOT_NAME` | `Jarvis` | What the robot calls **itself** — it identifies as this in speech and the LLM persona, **introduces itself out loud on first run** (and once more if you rename it), and stores *"who I am + what I can do"* as **top-priority memory** in the RAG SQLite db (injected first into every prompt). **Not** the wake word — that's a fixed openWakeWord model (`hey jarvis`). Rename: `sed -i 's/^ROBOT_NAME=.*/ROBOT_NAME=Rosie/' config/robot.env`. |

## Board (Pi 5 / Jetson)

The brain runs on more than one single-board computer via a small hardware-abstraction layer
([`brain/hal.py`](../brain/hal.py) + `brain/boards/`). The board is **auto-detected** from
`/proc/device-tree/model`; it picks the GPIO, PWM and camera backends and the pin numbering (Pi = **BCM**,
Jetson = physical **BOARD** header pins). Install with the per-board script: [`install_stack_pi5.sh`](../scripts/install_stack_pi5.sh)
on the Pi, [`install_stack_jetson.sh`](../scripts/install_stack_jetson.sh) on the Jetson.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_BOARD` | `auto` | `auto` = detect the board, else `pi5` \| `jetson`. Force it if detection is wrong (e.g. on a dev laptop it falls back to `pi5`). |
| `ROBOT_I2C_BUS` | *(empty)* | PCA9685 I²C bus number. Empty = Blinka default (**Pi = bus 1**). **Jetson: set `7`** (the 40-pin header) — needs the `adafruit-extended-bus` package. |
| `ROBOT_CAMERA_BACKEND` | *(empty)* | Empty = board default (**pi5 → picamera2**, **jetson → gstreamer**). Override: `picamera2` \| `gstreamer` \| `v4l2`. |
| `ROBOT_CAMERA_SOURCE` | *(empty)* | `v4l2`: USB webcam index (e.g. `0`). `gstreamer`: a full pipeline string. Empty = CSI sensor 0 via Argus (Jetson). |

> **Jetson pins differ.** The `ROBOT_*_TRIG_PIN`/`ROBOT_*_ECHO_PIN` values in the sensors section ship **SET** to
> Pi **BCM** numbers. On a Jetson those six lines must be **commented out** — `install_stack_jetson.sh` does this
> for you (and sets `ROBOT_BOARD`/`ROBOT_I2C_BUS`); only then do the Jetson board's own **BOARD**-pin defaults
> apply (an uncommented Pi pin always wins). Free the header pins with `sudo /opt/nvidia/jetson-io/jetson-io.py`.

## Obstacle sensors (ultrasonic ↔ laser ToF)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_SENSOR_TYPE` | `hcsr04` | Distance-sensor backend: `hcsr04` (3× HC-SR04P ultrasonic on the TRIG/ECHO pins) or `vl53l1x` (3× VL53L1X laser ToF over I²C — ~50 Hz, 400 cm, no TRIG/ECHO, immune to acoustic crosstalk). Switch with `./scripts/patches/sensor_vl53l1x.sh` (revert: `./scripts/patches/sensor_hcsr04.sh`). |
| `ROBOT_TOF_XSHUT_FRONT/LEFT/RIGHT` | `17/27/22` | **`vl53l1x` only.** Each VL53L1X's XSHUT pin, board pin-scheme numbering (Pi = BCM, Jetson = BOARD). All three boot at `0x29`, so they're raised one at a time and re-addressed — set these to your wiring. |
| `ROBOT_TOF_ADDR_FRONT/LEFT/RIGHT` | `0x2A/0x2B/0x29` | **`vl53l1x` only.** The distinct I²C address each sensor is moved to at startup (`i2cdetect` should then show all three). |

> ToF sensors are I²C, so the six `ROBOT_*_TRIG_PIN`/`ROBOT_*_ECHO_PIN` keys are **ignored** when `ROBOT_SENSOR_TYPE=vl53l1x`. The `adafruit-circuitpython-vl53l1x` driver is in both requirements files; if you add ToF after installing, `./brain/.venv/bin/pip install adafruit-circuitpython-vl53l1x`.

## Optional add-on sensors (IMU / encoder) — off by default

New capabilities, **disabled by default** (empty = the robot behaves exactly as before). Each lands only the
reader + config seam; wiring them into closed-loop turns / odometry is future work.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_IMU_TYPE` / `ROBOT_IMU_ADDR` | *(empty)* / `0x4A` | `bno085` enables a BNO085/BNO08x 9-DOF IMU (heading + yaw over I2C, `brain/imu.py`). Enable: `./scripts/patches/imu_bno085.sh`. |
| `ROBOT_ENCODER_TYPE` / `ROBOT_ENCODER_ADDR` | *(empty)* / `0x36` | `as5600` enables an AS5600 12-bit magnetic angle encoder (`brain/encoder.py`). Enable: `./scripts/patches/encoder_as5600.sh`. **`0x36` clashes with the UPS-HAT fuel gauge** — use a separate I²C bus if you fit both. |

## AI backend (local Ollama vs remote Gemini)

The "brain" — **text-intent and camera vision** — runs either **locally** on Ollama (default, fully
offline) or **remotely** on **Google Gemini**. The one switch flips **both**. Speech (whisper/piper),
sensors, motors, face and the wake word are **always local**. Getting a key + full setup:
**[docs/gemini-setup.md](../docs/gemini-setup.md)**.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_AI_BACKEND` | `local` | `local` = Ollama on the Pi (offline) \| `gemini` = Google Gemini (remote — needs network + a key, and camera frames leave the device). |
| `ROBOT_GEMINI_API_KEY` | *(empty)* | Google AI Studio key — **required** when `gemini`. Keep it secret (don't commit). [How to get one →](../docs/gemini-setup.md) |
| `ROBOT_GEMINI_MODEL` | `gemini-2.5-flash` | Remote **text/intent** model. Flash-tier is the low-latency sweet spot for command→JSON + chat; `gemini-3.1-pro` is smarter but slower. |
| `ROBOT_GEMINI_VISION_MODEL` | `gemini-robotics-er-1.6-preview` | Remote **vision** model. **Robotics-ER 1.6** is Google's embodied-reasoning VLM (spatial reasoning, object localization, agentic navigation) — best fit for describe/LOOK/explore/person-check. Empty → reuse `ROBOT_GEMINI_MODEL`. |
| `ROBOT_GEMINI_VISION_THINKING_BUDGET` | `0` | Gemini **thinking** budget for the vision path: `0` = off (fast, deterministic, no JSON truncation — recommended for Robotics-ER), `-1` = dynamic, `N` = fixed budget, `auto` = omit the field (for a model that rejects it). |
| `ROBOT_GEMINI_THINKING_BUDGET` | `0` | Thinking budget for the **text** model. `0` = off — a thinking model (e.g. `gemini-3.5-flash`) left on its default can spiral to 85–120 s on ambiguous "should I look?" prompts and trip `ROBOT_LLM_TIMEOUT_S`; `0` keeps command→JSON snappy. Raise (e.g. `512`) if intent decisions degrade; empty/`auto` = model default. |
| `ROBOT_GEMINI_URL` | `https://generativelanguage.googleapis.com/v1beta` | API base; override for a proxy/region. |

The remote path reuses the existing call timeouts (`ROBOT_LLM_TIMEOUT_S` for text, `ROBOT_VISION_TIMEOUT_S`
for vision).

## AI stack (models, binaries, mic)

`ROBOT_MODEL` / `ROBOT_OLLAMA_URL` (and **Model orchestration** below) apply when
`ROBOT_AI_BACKEND=local`; whisper/piper/mic are local regardless.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_MODEL` | `gemma4:e4b-it-qat` | Ollama **text/intent** model (4-bit QAT) that turns a transcript into the command JSON. **E4B** = more capable (better at structured fields like REMEMBER's `note` + recall) but slower; **E2B** (`gemma4:e2b-it-qat`) = faster/lighter. Vision is a separate `ROBOT_VISION_MODEL`. |
| `ROBOT_OLLAMA_URL` | `http://localhost:11434/api/generate` | Ollama generate endpoint (local; no internet). |
| `ROBOT_LLM_TIMEOUT_S` | `120` | Timeout for the text-intent call. Raise if a cold first-command load times out. |
| `ROBOT_WHISPER_BIN` | `/home/YOUR_USER/whisper.cpp/build/bin/whisper-cli` | Path to the whisper.cpp CLI (speech-to-text). |
| `ROBOT_WHISPER_MODEL` | `/home/YOUR_USER/whisper.cpp/models/ggml-base.en.bin` | Whisper GGML model file. |
| `ROBOT_PIPER_BIN` | `/home/YOUR_USER/piper/piper/piper` | Path to the Piper TTS binary. |
| `ROBOT_PIPER_VOICE` | `/home/YOUR_USER/piper/en_US-lessac-medium.onnx` | Piper voice model. |
| `ROBOT_MIC` | `plughw:1,0` | ALSA capture device for command recording (find with `arecord -l`); `plughw` auto-resamples to 16 kHz. Card **numbers shift** when you add USB audio — pin by name: `plughw:CARD=<id>,DEV=0`. |
| `ROBOT_RECORD_SECS` | `4` | Seconds of audio captured per command after a wake/ENTER. |
| `ROBOT_TMPDIR` | `/dev/shm` | Scratch dir for the per-turn WAV/JPEG (RAM-backed = fast, no SD wear). |

## Phone command endpoint (HTTP)

A tiny token-secured HTTP server runs **inside the brain** (so `run_me.sh` is unchanged) — your phone POSTs
a dictated text command to `http://<hostname>.local:PORT/speak` and it runs the **same** pipeline as the
wake word (drives/answers, **speaks the reply, returns it as JSON**). **On by default.** Setup + security:
[docs/http_setup.md](../docs/http_setup.md).

| Key | Default | What it does |
|---|---|---|
| `ROBOT_HTTP_ENABLE` | `1` | `0` = off. On by default; the server starts with every brain launch. |
| `ROBOT_HTTP_PORT` | `8080` | Port the phone POSTs to: `http://<hostname>.local:PORT/speak`. |
| `ROBOT_HTTP_TOKEN` | *(empty)* | Shared secret the phone must send (`X-Robot-Token` header or `?token=`). **Empty → auto-generated and saved here on first run** (the brain prints the ready URL + token once). |
| `ROBOT_HTTP_ALLOW_CAMERA` | `0` | `0` = block the camera actions (look around / find / remember) over HTTP, so a stolen token can't even get a one-shot photo description. `1` = allow them. |

## PCA9685 PWM channels

| Key | Default | What it does |
|---|---|---|
| `ROBOT_PCA9685_CHANNELS` | `16` | Channel count the ServoKit is created with (board has 16). |
| `ROBOT_ESC_CHANNEL` | `0` | PCA9685 channel wired to the **ESC** throttle signal. |
| `ROBOT_STEER_CHANNEL` | `1` | PCA9685 channel wired to the **steering servo**. |

## Steering servo (degrees)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_STEER_CENTER` | `90` | Angle where the wheels point dead ahead. **Calibrate first.** |
| `ROBOT_STEER_LEFT` | `55` | Full-left angle — back it off if the linkage binds/buzzes. |
| `ROBOT_STEER_RIGHT` | `125` | Full-right angle — same caution. |

## Throttle (ESC)

Deadband-compensated map — values are throttle fractions of the 1000–2000 µs range (0.00 = 1500 µs neutral,
0.60 ≈ 1800 µs, 1.00 = 2000 µs). Many brushed ESCs ignore small offsets from neutral, so `speed` 0–100 is
mapped onto floor→ceil (not 0→cap), skipping the dead zone.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_ESC_FWD_FLOOR` | `0.60` | Throttle where FORWARD first engages (~1800 µs); raise until "speed 1" just creeps. |
| `ROBOT_ESC_FWD_CEIL` | `1.00` | Max FORWARD throttle — lower = slower top speed. |
| `ROBOT_ESC_REV_FLOOR` | `0.60` | Throttle where REVERSE first engages. |
| `ROBOT_ESC_REV_CEIL` | `0.80` | Max REVERSE throttle — some ESCs only reverse in a band, so cap it here. |
| `ROBOT_ESC_REVERSE_DOUBLETAP` | `0` | `0` = direct reverse (crawler / F-R ESC). `1` = F/B/R "double-click" — the driver auto-taps reverse→neutral→reverse. |
| `ROBOT_ESC_REV_BRAKE_MS` | `400` | (double-tap only) length of the first reverse tap the ESC reads as a brake. |
| `ROBOT_ESC_REV_GAP_MS` | `300` | (double-tap only) neutral gap between the two taps. |
| `ROBOT_THROTTLE_INVERT` | `0` | `1` swaps FORWARD/REVERSE if the motor runs backwards (alternative to swapping motor wires). |
| `ROBOT_ESC_ARM_SECS` | `3` | Neutral hold at startup so the ESC arms (cell beeps, then a long "ready" beep). |

## Forward obstacle gate (front HC-SR04P)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_OBSTACLE_CM` | `30` | FORWARD motion is cut when the front beam reads closer than this. |
| `ROBOT_OBSTACLE_DEBOUNCE` | `3` | Consecutive close/failed front reads before a move **aborts** — rides out gpiozero's occasional garbage frame (even negative cm) under load. `1` = old behaviour. Each frame still holds neutral, so it never drives while unsure. |
| `ROBOT_SENSOR_MAX_DISTANCE_M` | `1.0` | HC-SR04P echo range cap (m). Software-timed on the Pi 5, so a **no-echo** ping busy-waits the full timeout (∝ this) and can starve the wake mic — keep it low (the gate only needs <30 cm). Lowered `2.0`→`1.0` to halve that wait. |
| `ROBOT_SENSOR_IDLE_QUIESCE` | `1` | **The "runs forever" fix.** While idle-listening for the wake word, CLOSE the 3 ultrasonic sensors so their software-timed ping threads stop starving the mic → no overflow/deafness. Rebuilt automatically for each drive/explore (fail-closed: a paused front gate blocks, never drives blind). `0` = keep them always on. Auto-disabled under `--verbose`. |
| `ROBOT_SENSOR_VERBOSE` | `0` | `1` (or `run_me.sh --verbose`): stream a live `SENSORS front/left/right` read-out every `ROBOT_SENSOR_LOG_PERIOD_S` s and show **every** no-echo (not collapsed to one line). Adds a little load (reads all 3 beams) — a diagnosis switch, not for continuous running. Also disables idle-quiescing so you can watch the sensors. |
| `ROBOT_SENSOR_LOG_PERIOD_S` | `2.0` | How often the `--verbose` `SENSORS` read-out prints (seconds). |
| `ROBOT_LOG_LEVEL` | `INFO` | Log verbosity: `INFO` or `DEBUG`. `run_me.sh --verbose` sets `DEBUG` for that run. |
| `ROBOT_TRIG_PIN` | `23` | Front sensor **TRIG** (BCM GPIO number). |
| `ROBOT_ECHO_PIN` | `24` | Front sensor **ECHO** (BCM; HC-SR04**P** ECHO is 3.3 V — a bare HC-SR04 needs a divider). |

## Side ultrasonic sensors (explore steering-avoid)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_LEFT_TRIG_PIN` / `ROBOT_LEFT_ECHO_PIN` | `5` / `6` | Left sensor TRIG/ECHO (BCM). |
| `ROBOT_RIGHT_TRIG_PIN` / `ROBOT_RIGHT_ECHO_PIN` | `20` / `21` | Right sensor TRIG/ECHO (BCM). |
| `ROBOT_SIDE_OBSTACLE_CM` | `25` | During explore, steer away from a side beam closer than this. |
| `ROBOT_FAKE_LEFT_CM` / `ROBOT_FAKE_RIGHT_CM` | `200` / `200` | Dry-run synthetic left/right distances. |

## Software failsafe

| Key | Default | What it does |
|---|---|---|
| `ROBOT_MAX_MOVE_MS` | `5000` | Hard ceiling on how long **one** command may keep the motor running before the failsafe neutralises it. |

## Camera & vision

| Key | Default | What it does |
|---|---|---|
| `ROBOT_CAMERA_ENABLE` | `1` | `0` disables the Pi Camera (explore/find/REMEMBER lose sight). |
| `ROBOT_CAMERA_SIZE` | `512` | Square letterbox size (px) the frame is reduced to before the VLM. |
| `ROBOT_CAMERA_WARMUP_S` | `1.0` | Settle time for auto-exposure/white-balance before each capture. |
| `ROBOT_CAMERA_AUTOFOCUS` | `1` | On AF sensors (IMX519), run a one-shot focus before each capture; ignored on fixed-focus (IMX219/OV5647). |
| `ROBOT_WAKE_PERSON_CHECK` | `auto` | On wake, snap a frame + VLM-check if a **person** is in front (runs parallel to recording, so ~free on remote) and feed it into that reply. `auto` = on for remote / off for local; `1` = always, `0` = never. |
| `ROBOT_VISION_TIMEOUT_S` | `600` | Timeout for one image call — vision is **CPU-only and slow** on the Pi 5 (tens of s to minutes). |
| `ROBOT_FAKE_VISION` | *(empty)* | Dry-run scripted sighting `target:horizontal:distance:confidence` (e.g. `sofa:left:near:0.9`) so explore/find runs with no camera. |

## Face screen

Folder layout + animation rules: [`brain/faces/README.md`](../brain/faces/README.md).

| Key | Default | What it does |
|---|---|---|
| `ROBOT_FACE_ENABLE` | `1` | `0` turns the face off entirely (never blocks driving either way). |
| `ROBOT_FACE_BACKEND` | `auto` | `auto` (tries turing→framebuffer→simulated) \| `turing` (USB-C serial panel) \| `framebuffer` (a **DSI/HDMI** screen e.g. **Freenove 5" 800×480** → `/dev/fb0`) \| `simulated` (logs only). |
| `ROBOT_FACE_PORT` | `AUTO` | **turing only.** Serial port for the USB-C panel, or `AUTO` (e.g. `/dev/ttyACM0`). |
| `ROBOT_FACE_REVISION` | `A` | **turing only.** `A` (Turing/UsbPCMonitor 320×480) or `B` (XuanFang) — confirm with the lib's `simple-program.py`. |
| `ROBOT_FACE_WIDTH` / `ROBOT_FACE_HEIGHT` | `320` / `480` | **turing only** (panel geometry, portrait). The `framebuffer` backend auto-reads size **and** colour depth from the kernel (so 800×480 + Pi 5 KMS 32bpp just work). |
| `ROBOT_FACE_BRIGHTNESS` | `80` | Panel brightness 0–100 (dim to save power). |
| `ROBOT_FACE_LIB` | `~/turing-smart-screen-python` | Path to the panel library (cloned by the installer). |
| `ROBOT_FACE_DUMP` | *(empty)* | If set to a dir, the simulated backend writes `face_<state>.png` per state — inspect the face with no panel. |
| `ROBOT_FACE_FRAMES_DIR` | *(empty → `brain/faces`)* | Where per-state PNG animation frames live. |
| `ROBOT_FACE_FPS` | `4` | Default frame rate for looping states (`talking` runs faster; the USB-C panel is capped). |

## Autonomy — explore / find

| Key | Default | What it does |
|---|---|---|
| `ROBOT_EXPLORE_TRIGGERS` | `explore,look around,have a look,…,head towards` | Comma-separated phrases that must appear in the transcript for EXPLORE/FIND to run (else demoted to DRIVE). **Quote the value.** |
| `ROBOT_LOOK_TRIGGERS` | `what is this,what do you see,look at this,…` | Phrases that force a **live camera LOOK** (capture a fresh frame + describe it) instead of answering from memory. **Quote the value.** |
| `ROBOT_EXPLORE_MAX_ITERS` | `12` | **Local** agentic EXPLORE + FIND: max look→step iterations before giving up. |
| `ROBOT_EXPLORE_MAX_ITERS_REMOTE` | `24` | **Remote** EXPLORE: 2× (Gemini/any remote is faster + smarter). |
| `ROBOT_EXPLORE_MAX_SECONDS` | `60` | **Local** EXPLORE: wall-clock cap (whichever cap hits first ends it). |
| `ROBOT_EXPLORE_MAX_SECONDS_REMOTE` | `120` | **Remote** EXPLORE: 2×. |
| `ROBOT_EXPLORE_NARRATE` | `1` | EXPLORE: speak what it notices each step (`0` = silent, just store to memory). |
| `ROBOT_EXPLORE_STEP_MS` | `900` | Forward step duration per move (EXPLORE + FIND toward a sighted target). |
| `ROBOT_SCAN_STEP_MS` | `700` | FIND: arc-scan nudge when the target isn't framed yet. |
| `ROBOT_EXPLORE_SPEED` | `15` | Throttle 0–100 used during explore/find (kept low). |
| `ROBOT_FIND_CONFIDENCE` | `0.55` | Minimum vision confidence to act on a sighting. |
| `ROBOT_APPROACH_CM` | `40` | Treat the target as reached when the forward beam is closer than this. |

## Environment memory (SQLite)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_MEMORY_DB` | `~/.localairobot/memory.db` | SQLite file holding sightings + taught people/things (survives reboots). |
| `ROBOT_MEMORY_MAX_CONTEXT` | `8` | Recent distinct objects summarised back into the prompt each turn. |
| `ROBOT_MEMORY_MAX_SCENES` | `3` | Recent scene **descriptions** (from agentic explore) summarised back into the prompt / shown by `rag.sh --scenes`. |

Inspect or edit it with **[`scripts/rag.sh`](../scripts/rag.sh)**: `./scripts/rag.sh` (taught facts),
`--seen` (sightings), `--all`, `--forget <name>`, `--clear-facts`, or `--query "<SQL>"` for anything else.

## Model orchestration

| Key | Default | What it does |
|---|---|---|
| `ROBOT_WARMUP` | `1` | Prime the model at boot so the **first** command isn't a slow cold load. |
| `ROBOT_OLLAMA_KEEPALIVE` | `30m` | Keep the model resident between commands: `30m`, `-1` = forever, `''` = Ollama default (~5 m). |
| `ROBOT_NUM_CTX` | `4096` | Caps Ollama's context window (KV cache). Gemma 4 defaults to **128 K** — far more than short commands need; smaller = faster + less RAM on the Pi. `0` = Ollama default. |

## Dedicated vision model

| Key | Default | What it does |
|---|---|---|
| `ROBOT_VISION_MODEL` | `moondream` | Separate VLM for camera frames (decouples vision latency from the text brain). Empty falls back to sharing `ROBOT_MODEL`. Switch: `ollama pull <new>`, set here, re-source. |

## Conversation follow-ups

| Key | Default | What it does |
|---|---|---|
| `ROBOT_FOLLOWUP_ENABLE` | `1` | `1` = after a wake, keep taking commands with **no** "hey jarvis" until you go quiet — a natural back-and-forth. `0` = require the wake word every single turn (old behaviour). |
| `ROBOT_FOLLOWUP_SILENCE_S` | `12` | Seconds of silence mid-conversation before it goes back to sleep (wake word needed again). Granularity is ~one record window (`ROBOT_RECORD_SECS`), so the effective wait rounds up to the next window. |

## Wake word (openWakeWord)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_WAKEWORD_ENABLE` | `1` | `1` = always-listening wake word (required in live). `0` disables it — then **live has no trigger** and the brain exits; ENTER works only in `--dry`. |
| `ROBOT_WAKEWORD_MODEL` | `~/LocalAiRobot/models/hey_jarvis.onnx` | Path to the wake `.onnx`. Ships wired to the stock **hey_jarvis** (reliable out of the box); a custom **hey_richard** also ships in `models/` (retrain it stronger to switch). Missing/unloadable → **live has no trigger** (the brain exits; ENTER works only in `--dry`). [openwake-word.md](../docs/opnewakeword/openwake-word.md). |
| `ROBOT_WAKEWORD_THRESHOLD` | `0.4` | Minimum detection probability 0–1. Watch the `best score`/`loudest` log line: if genuine *"hey jarvis"* lands below this, lower it; **but first check `loudest` isn't near 32767** (mic clipping collapses the score — lower the mic gain instead). Raise toward `0.5`+ if it false-fires. |
| `ROBOT_WAKEWORD_TRIGGER_LEVEL` | `1` | Consecutive activations before firing (raise to `2` if it false-fires). |
| `ROBOT_WAKEWORD_COOLDOWN_S` | `2.0` | Quiet window after a wake / after TTS before re-listening (anti self-trigger). |
| `ROBOT_WAKEWORD_DEVICE` | *(empty)* | The **wake mic** (PortAudio): an index, or a unique substring of the mic's name (e.g. `PnP`) — by-name survives card renumbering. Empty = default input. |
| `ROBOT_WAKEWORD_LATENCY` | `high` | Mic input **buffer**: `high` (device default — absorbs CPU stalls so a busy loop can't drop samples → no OVERFLOW/deafness), a float = seconds (e.g. `0.3`), or `low` (old tight buffer). Bigger = a touch more wake latency. |
| `ROBOT_WAKEWORD_STREAM_RESET_S` | `15` | **Self-heal.** If the mic drops chunks continuously for this many seconds (a wedged PortAudio stream), `model.reset()` alone can't clear it — so the listener tears it down and reopens a fresh one. `0` disables. |
| `ROBOT_WAKEWORD_DEGRADED_PCT` | `20` | **Health.** Logs a `wake DEGRADED` warning when ≥ this % of mic chunks are dropped in a ~15 s window — the sign the predict loop can't keep up. A `wake HEALTH:` line (dropped %) prints every ~15 s regardless. |
| `ROBOT_WAKEWORD_DROP_RESET_N` | `3` | **MIC-01.** Consecutive dropped mic chunks before the wake model is reset; a *single* drop is **bridged** with silence so a lossy USB mic (~7 % loss) doesn't destroy detection. `1` = old reset-every-drop behaviour. |
| `ROBOT_WAKEWORD_MAX_ERRORS` | `10` | **MIC-03.** Consecutive raised listen faults (USB re-enumerate / ALSA hiccup) to **retry** before giving up with a **non-zero exit** so systemd restarts the brain — never a silent exit-0 that strands it deaf. |
| `ROBOT_WAKEWORD_REBUILD_MAX_S` | `60` | **MIC-02.** If a dead mic stream can't deliver clean audio within this long, exit non-zero to restart (the only reliable recovery from a wedged USB audio stack). `0` disables the escalation (bench debugging). |
| `ROBOT_WAKEWORD_QUEUE_MAX` | `32` | **Jitter buffer.** Mic capture runs in a PortAudio **callback** that fills a queue this many 80 ms chunks deep while `predict()` consumes it — so a slow predict can no longer overflow the mic. Bigger = absorbs longer predict spikes before a chunk is dropped, at a little worst-case wake latency. Won't fix a *sustained* CPU deficit (that's what the capture rate below is for). Min 4. |
| `ROBOT_WAKEWORD_CAPTURE_RATE` | `0` | `0` = auto: prefer 16 kHz native, else a **clean ratio** (48 k = 3:1, 32 k = 2:1), else 44.1 k. `44100→16000` uses a costly ~8800-tap resample **every 80 ms chunk** that can push the predict loop over real-time and drop audio; `48000→16000` is a cheap 3:1. Force e.g. `48000` if the mic supports it but auto still lands on 44100. |
| `ROBOT_FAKE_WAKE` | `0` | Dry-run: `1` auto-fires the wake once; else type `r` at the prompt to simulate it. |

## Audio cues / earcons

WAV folder layout + format tips: [`sounds/README.md`](../sounds/README.md).

| Key | Default | What it does |
|---|---|---|
| `ROBOT_SOUNDS_ENABLE` | `1` | Master switch for greeting/wake-ack/ack/thinking/error earcons. |
| `ROBOT_SOUNDS_DIR` | *(empty → `<repo>/sounds`)* | Root holding the cue folders. |
| `ROBOT_SOUNDS_DEVICE` | *(empty)* | **Earcons only** — the **voice** (Piper) uses the ALSA *default*, so route that via `~/.asoundrc`. USB speaker by name: `plughw:CARD=<id>,DEV=0`. Empty = default. |
| `ROBOT_SOUNDS_GREETING` | `1` | Play once on boot/arm (audible "stack is up" — vital headless). |
| `ROBOT_SOUNDS_WAKE_ACK` | `1` | "Yes?/Hi" the instant the **wake word** fires (`wake_word_ack/`) — responding to being summoned, *before* it records your command. |
| `ROBOT_SOUNDS_ACK` | `1` | "Got it/On it/Okay" the instant a **spoken command** is accepted (`ack_sounds/`) — always immediate, both backends. |
| `ROBOT_SOUNDS_REMOTE_ACK` | `1` | "Got a remote command…" when a **phone/HTTP** command lands (`remote_ack_sounds/`) — the robot audibly excuses itself mid-chat. |
| `ROBOT_SOUNDS_THINKING` | `1` | The **first** processing cue while the model runs (`thinking_sounds/`, picked at random). |
| `ROBOT_SOUNDS_THINKING_STILL` | `1` | The **re-cues** after the first, each repeat interval (`thinking_still_sounds/`, the "still…" set). |
| `ROBOT_SOUNDS_SLEEP` | `1` | Spoken **goodbye** when a conversation ends on silence (`sleep_sounds/`, e.g. *"Going back to sleep — say hey jarvis to wake me"*). Plays **fully** (blocking) before the robot shows the sleeping face and returns to wake-word-only. |
| `ROBOT_SOUNDS_ERROR` | `1` | On obstacle / LLM error (fire-and-forget; never delays the stop). |
| `ROBOT_THINKING_LEAD_MS` | `500` | **Local:** stay silent this long before the first thinking cue. |
| `ROBOT_THINKING_REPEAT_S` | `10` | **Local:** seconds between re-cues (the "still…" set). **`0` = play once.** |
| `ROBOT_THINKING_LEAD_REMOTE_MS` | `4000` | **Remote:** silent this long first, so a fast Gemini reply plays **no** cue at all. |
| `ROBOT_THINKING_REPEAT_REMOTE_S` | `0` | **Remote:** `0` = if a cue fires on a slow turn, play it just **once**. |
