# LocalAiRobot

> A 1/10 RC crawler you can **talk to** — local LLM, speech-to-text, text-to-speech, **and a
> camera**, running **entirely on a Raspberry Pi 5 (16GB)**. No cloud, no Wi-Fi, fully offline.
> It drives on voice command, **stops for an obstacle directly ahead**, **looks around and drives
> toward things you name**, shows an **expressive face**, and talks back.

## Quickstart — 5 steps to a talking robot

Car built and wired ([docs/assembly.md](docs/assembly.md))? SSH into the Pi and:

1. **Clone it** — `git clone https://github.com/YOUR_GITHUB_USER/LocalAiRobot.git ~/LocalAiRobot && cd ~/LocalAiRobot`
2. **Install it** — `./scripts/install_stack.sh` grabs the offline AI stack and sets your username; then `sudo reboot` and SSH back in.
3. **Patch it** — `./scripts/patches/hb_qr_1060.sh` drops in your ESC's bench-tested defaults (pick the one for your ESC — see [scripts/patches/](scripts/patches/)).
4. **Prove it** — ESC on, **wheels off the ground**: `./scripts/run_diagnostics.sh --wheels-off`. Green ✓ down the list; fix any red ✗ with the doc pointer it prints, then chase the **ALL-OK**.
5. **Drive it** — `./scripts/run_me.sh`, say *"hey jarvis"*, and go. (Bench first? `./scripts/run_me.sh --dry` — nothing moves.)

![LocalAiRobot voice + vision control flow: a voice command — started by a wake word (openWakeWord; ENTER push-to-talk survives in --dry only) — is transcribed by whisper.cpp, passes a trigger-phrase gate (EXPLORE/FIND only on explore words, otherwise DRIVE), and the gemma4:e4b-it-qat text model (Ollama) turns it into an intent JSON using recent context from a SQLite environment memory; DRIVE/STOP go to the driver.py control loop while EXPLORE/FIND run a behaviour loop that stops the car, captures a Pi Camera V2 NoIR frame, asks the moondream VLM what is visible, records it to memory, and takes gated drive steps; a 3-sensor HC-SR04P array (front/left/right) gates FORWARD and informs steering; the PCA9685 drives the ESC (Electronic Speed Controller) / drive motor and steering servo; the text-intent and vision calls share one Ollama server behind a single model_lock (warmed at boot); replies are spoken via Piper TTS alongside short ack/greeting/thinking earcons, and a USB-C face screen reflects state (idle/listening/thinking/happy/confused/alarmed/warmup/capturing)](docs/images/basic_control_flow.png)

Everything runs on the Pi 5: **whisper.cpp** transcribes your voice, **Ollama** (`gemma4:e4b-it-qat`) turns
it into a JSON intent (a separate **`moondream`** VLM reads the camera), **Piper** speaks the reply, and
`driver.py` drives — a **PCA9685** makes jitter-free PWM for the **ESC** + steering servo (Ackermann: drive
+ steer, no spin), and 3× **HC-SR04P** cut forward motion near obstacles and keep steering off walls.
Control is the **wake word** (*"hey jarvis"*; **ENTER** in `--dry` bench mode).

**Autonomy is opt-in:** plain commands ("go forward", "stop") are teleop; the camera-driven
look-around / find-the-X only runs on an explore phrase, gated against the raw transcript so a mishear
can't send it wandering. **It is** voice teleop + a forward safety stop + bounded vision explore/find +
plain chat + a teachable people/things memory. **It isn't** self-driving, obstacle avoidance, or SLAM —
Pi-CPU vision is **slow** (tens of seconds a look), so "find the X" is a stop-look-step loop.

> 🛟 **Safety — software failsafe, no hardware watchdog.** The brain returns the ESC to neutral on
> STOP / error / obstacle / timeout, but a **killed process leaves the PCA9685 holding the last pulse** —
> keep a **physical kill switch on the motor battery** in reach and start slow. A missing board/sensor is
> logged by name and the brain runs degraded, not crashed.

---

## Parts list (with search terms)

Search terms are generic — use your preferred retailer.

> 🛒 **Ready-made cart:** the parts below are collected in this
> [**AliExpress wishlist**](https://www.aliexpress.com/p/wish-manage/share.html?spm=a2g0o.productlist.headerAcount.6.5b533bbc853DSK&smbPageCode=wishlist-amp&spreadId=EAF81BDA069358563ED6BEBC329A18554A43DABDD29B2526E03A87F05A681D99)
> if you'd rather buy the same items than search. Listings/prices change over time — confirm each against
> the search terms and notes here before ordering.

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
| ESC (Electronic Speed Controller) | `60A brushed crawler ESC program card 2-3S` | direct reverse + drag-brake stop (e.g. SURPASS HOBBY 60A). ⚠️ its **7.4 V BEC** powers the servo — see the servo note |
| Steering servo | `DSSERVO DS3225 digital servo 180` | 25 kg, **180°** (not 270°). ⚠️ use an **HV (7.4 V)** version, or feed V+ from a separate 5–6 V BEC — the 7.4 V ESC BEC can fry a 6 V servo |
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

Full build + calibration in [docs/assembly.md](docs/assembly.md); pin-by-pin wiring in
[docs/wiring/pwm-esc-servo-wiring.md](docs/wiring/pwm-esc-servo-wiring.md) (drive & steering) and
[docs/wiring/ultrasonic-sensors-wiring.md](docs/wiring/ultrasonic-sensors-wiring.md) (3× HC-SR04P sensors).

![LocalAiRobot wiring at a glance: a 2S 7.4V LiPo feeds the ESC (Electronic Speed Controller) (which carries a 7.4V BEC) and the drive motor; the ESC (Electronic Speed Controller)'s 3-wire plug goes to PCA9685 ch0 (signal + V+ 7.4V rail + GND) and the steering servo to ch1 (signal/V+/GND); the PCA9685 takes VCC 3.3V + SDA(GPIO2) + SCL(GPIO3) + GND from the Raspberry Pi 5; three HC-SR04P sensors (front GPIO23/24, left GPIO5/6, right GPIO20/21) take 3.3V + GND + TRIG/ECHO from the Pi; the Pi Camera V2 NoIR connects over the CSI ribbon and the USB-C face screen over USB; the Pi 5 is powered by the UPS HAT on its own battery. Two isolated battery domains, common ground.](docs/images/wiring.png)

- **Two isolated power domains** (LiPo→motor/servo, UPS→Pi): **never bridge them**, but **tie all grounds together** (Pi ⟷ PCA9685 ⟷ ESC (Electronic Speed Controller)) or the PWM won't register.
- PCA9685 **V+ rail = the ESC (Electronic Speed Controller)'s 7.4 V BEC**, *not* the Pi (so use an HV servo, or feed V+ from a separate 5–6 V BEC). All three HC-SR04**P** run at **3.3 V**, so every ECHO is Pi-safe (no divider).
- **Pins:** I2C on GPIO2/3 (PCA9685 @ `0x40`); forward sensor TRIG/ECHO `23/24`, left `5/6`, right `20/21`. Camera is on the CSI ribbon, the face panel on USB — neither touches the GPIO header.

---

## Running it

The **Quickstart** above is the whole happy path; the full phased build + calibration lives in
[docs/assembly.md](docs/assembly.md). A few things worth knowing:

- **Bench-test first (no chassis, no motor):** `./scripts/run_me.sh --dry` fakes all hardware and logs
  exactly what it *would* drive (voice → model → PWM), so you can verify the pipeline before anything can
  move. On the *built* car, `ROBOT_MOTORS_OFF=1` runs everything live but sends the ESC/steering no signal
  — talk to it on a desk without it driving off.
- **Day-to-day launcher:** `./scripts/run_me.sh` runs foreground (`--dry` / `--live`, Ctrl-C to stop);
  `--detach` backgrounds it, `--install-boot` (or `install_stack.sh --bootstrap`) auto-starts it on every
  boot, `--reload` re-applies an edited `config/robot.env`.
- ☁️ **Optional — remote brain (Gemini).** The LLM + vision run **locally/offline by default**. To use
  Google Gemini instead (faster, but online + an API key, and camera frames leave the device): get a key
  ([docs/gemini-setup.md](docs/gemini-setup.md)), set `ROBOT_GEMINI_API_KEY=` + `ROBOT_AI_BACKEND=gemini`,
  and restart. Flip back with `ROBOT_AI_BACKEND=local` — only the LLM/vision switch; speech, sensors,
  motors and the wake word stay local.
- 📶 **Switch Wi-Fi in the field — no monitor/keyboard.** Move the robot onto a new network, or recover
  SSH access, from just your phone (a phone-hotspot fallback + a Comitup captive portal, plus passwordless
  Ed25519 SSH): [docs/change-pi-wifi.md](docs/change-pi-wifi.md).

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
| `speed` | `0`–`100` | mapped into the ESC's usable range (`ROBOT_ESC_FWD_FLOOR/CEIL`; reverse `ROBOT_ESC_REV_FLOOR/CEIL`) |
| `duration_ms` | `300`–`5000` | run time before the failsafe neutralizes the move |
| `reply` | short text | spoken via Piper (stdin, no shell → no injection) |

**Explore & find.** On *"look around"* / *"find the X"* the car stops, grabs a frame, asks the `moondream`
VLM, and (for FIND) runs a **bounded** stop → look → step loop — gated by the forward sensor, capped by
`ROBOT_EXPLORE_MAX_ITERS`, held at neutral each look (Pi-CPU vision is slow; the fixed camera makes
"look around" a coarse arc-scan).

**Memory.** Sightings and taught people/things go to SQLite. Say *"this is my wife Shani"* (`REMEMBER`) and
it photographs, has the VLM describe it, and stores name + relationship + description + the JPEG; each turn
it injects known entities into the prompt — a lightweight RAG — so context survives commands and reboots.
`SAY` handles plain chat: it answers in words and stays put.

**Face.** A small display shows the robot's state (`idle / listening / thinking / …`) — the built-in
procedural face, or your PNG frames in `brain/faces/<state>/`. It auto-selects a backend (USB-C → DSI/HDMI
→ simulated) and is optional; a missing panel never blocks driving. Design your own on your PC
(`./scripts/host_setup.sh` installs the tools): [`brain/faces/README.md`](brain/faces/README.md), [`sounds/README.md`](sounds/README.md).

**Wake word & warmup.** Control is the wake word (*"hey jarvis"*, shipped in [`models/`](models/README.md);
or train your own: [`tools/wakeword-training/`](tools/wakeword-training/README.md)) — always-listening, the only *live* trigger
(ENTER stays in `--dry`), and what lets the brain run headless. Short earcons ([`sounds/`](sounds/README.md))
+ a boot warmup (`ROBOT_WARMUP`, keep-alive) mask the slow first load, and a `model_lock` serialises the
text-intent and vision calls.

**Tuning.** Every setting lives in [`config/robot.env`](config/robot.env), each documented (default + what
it does) in [`config/README.md`](config/README.md). After the one-time downloads, disable Wi-Fi — it all
runs on-device.

---

## Credits & license

MIT — see [LICENSE](LICENSE). Built on [Ollama](https://ollama.com/) +
[Gemma](https://ai.google.dev/gemma) + [moondream](https://moondream.ai/), [whisper.cpp](https://github.com/ggerganov/whisper.cpp),
[Piper](https://github.com/rhasspy/piper), [picamera2](https://github.com/raspberrypi/picamera2),
[gpiozero](https://gpiozero.readthedocs.io/),
[Adafruit ServoKit / PCA9685](https://github.com/adafruit/Adafruit_CircuitPython_ServoKit),
[openWakeWord](https://github.com/dscripka/openWakeWord), and
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python).
