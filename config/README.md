# Configuration reference (`config/robot.env`)

Every runtime setting is an environment variable in [`robot.env`](robot.env). The brain reads them from
the **shell environment**, not the file directly, so an edit only takes effect once the file is
(re)loaded:

- `scripts/run_me.sh` / `scripts/shutdown.sh` **source it for you** on every launch.
- `install_stack.sh` adds an auto-load line to `~/.bashrc`, so **new SSH sessions** load it too.
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

## AI backend (local Ollama vs remote Gemini)

The "brain" — **text-intent and camera vision** — runs either **locally** on Ollama (default, fully
offline) or **remotely** on **Google Gemini**. The one switch flips **both**. Speech (whisper/piper),
sensors, motors, face and the wake word are **always local**. Getting a key + full setup:
**[docs/gemini-setup.md](../docs/gemini-setup.md)**.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_AI_BACKEND` | `local` | `local` = Ollama on the Pi (offline) \| `gemini` = Google Gemini (remote — needs network + a key, and camera frames leave the device). |
| `ROBOT_GEMINI_API_KEY` | *(empty)* | Google AI Studio key — **required** when `gemini`. Keep it secret (don't commit). [How to get one →](../docs/gemini-setup.md) |
| `ROBOT_GEMINI_MODEL` | `gemini-2.5-flash` | Remote **text/intent** model. |
| `ROBOT_GEMINI_VISION_MODEL` | *(empty)* | Remote **vision** model; empty → reuse `ROBOT_GEMINI_MODEL` (Gemini is multimodal). |
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
| `ROBOT_EXPLORE_MAX_ITERS` | `6` | FIND: max look→step iterations before giving up. |
| `ROBOT_EXPLORE_STEP_MS` | `900` | FIND: forward step duration toward a sighted target. |
| `ROBOT_SCAN_STEP_MS` | `700` | Arc-scan nudge when the target/scene isn't framed yet. |
| `ROBOT_EXPLORE_SPEED` | `30` | Throttle 0–100 used during explore/find (kept low). |
| `ROBOT_EXPLORE_SCANS` | `3` | EXPLORE ("look around"): number of arc-scan looks. |
| `ROBOT_FIND_CONFIDENCE` | `0.55` | Minimum vision confidence to act on a sighting. |
| `ROBOT_APPROACH_CM` | `40` | Treat the target as reached when the forward beam is closer than this. |

## Environment memory (SQLite)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_MEMORY_DB` | `~/.localairobot/memory.db` | SQLite file holding sightings + taught people/things (survives reboots). |
| `ROBOT_MEMORY_MAX_CONTEXT` | `8` | Recent distinct objects summarised back into the prompt each turn. |

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

## Wake word (openWakeWord)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_WAKEWORD_ENABLE` | `1` | `1` = always-listening wake word (required in live). `0` disables it — then **live has no trigger** and the brain exits; ENTER works only in `--dry`. |
| `ROBOT_WAKEWORD_MODEL` | `~/LocalAiRobot/models/hey_jarvis.onnx` | Path to the wake `.onnx`. Ships wired to the stock **hey_jarvis** (reliable out of the box); a custom **hey_richard** also ships in `models/` (retrain it stronger to switch). Missing/unloadable → **live has no trigger** (the brain exits; ENTER works only in `--dry`). [openwake-word.md](../docs/opnewakeword/openwake-word.md). |
| `ROBOT_WAKEWORD_THRESHOLD` | `0.4` | Minimum detection probability 0–1. Watch the `best score`/`loudest` log line: if genuine *"hey jarvis"* lands below this, lower it; **but first check `loudest` isn't near 32767** (mic clipping collapses the score — lower the mic gain instead). Raise toward `0.5`+ if it false-fires. |
| `ROBOT_WAKEWORD_TRIGGER_LEVEL` | `1` | Consecutive activations before firing (raise to `2` if it false-fires). |
| `ROBOT_WAKEWORD_COOLDOWN_S` | `2.0` | Quiet window after a wake / after TTS before re-listening (anti self-trigger). |
| `ROBOT_WAKEWORD_DEVICE` | *(empty)* | The **wake mic** (PortAudio): an index, or a unique substring of the mic's name (e.g. `PnP`) — by-name survives card renumbering. Empty = default input. |
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
| `ROBOT_SOUNDS_ACK` | `1` | "Got it/On it" after a **spoken command** is transcribed and accepted (`ack_sounds/`). |
| `ROBOT_SOUNDS_THINKING` | `1` | Looped spoken cue **while** the model runs (proof-of-life on slow vision). |
| `ROBOT_SOUNDS_ERROR` | `1` | On obstacle / LLM error (fire-and-forget; never delays the stop). |
| `ROBOT_THINKING_LEAD_MS` | `500` | Stay silent this long before the first thinking cue (fast turns). |
| `ROBOT_THINKING_REPEAT_S` | `20` | Seconds between thinking cues while the model runs. **`0` = play it once** (the face shows it's still thinking). Stops a spoken "Let me think" from repeating back-to-back over a long think. |
