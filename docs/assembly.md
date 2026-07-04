# Assembly & Wiring Notes

Build the LocalAiRobot RC crawler **one phase at a time** — flash the brain, bring it up on the bench,
wire each peripheral with its own check, build the chassis, then calibrate live. Pairs with the
[README](../README.md) (parts list + tuning). Capture your own photos as you go.

![Wiring at a glance: a 2S 7.4V LiPo feeds the ESC (with 7.4V BEC), which drives the motor and sends a ch0 PWM signal + V+ rail + GND to the PCA9685 (0x40); the PCA9685 drives the DS3225 steering servo on ch1 and connects to the Raspberry Pi 5 (+ Active Cooler) via 3.3V VCC, SDA (GPIO2), SCL (GPIO3) and GND; three HC-SR04P sensors (front 23/24, left 5/6, right 20/21), the Pi Camera V2 NoIR over CSI, and a 3.5" USB-C face screen are Pi-side; the Pi runs off a UPS HAT or the 27W bench PSU. The two battery positives are never bridged; all grounds are common.](images/wiring.png)

**Phase map:** **1** flash + brain · **2** power domains · **3** PCA9685 · **4** sensors · **5** camera ·
**6** face · **7** build · **8** live calibration · **9** UPS HAT. Phases 1–6 run **on the bench on the
27 W PSU — no chassis, no motor**; nothing can move until Phase 7 connects the ESC.

> 🖥️ **Where commands run.** Unless noted, every command runs in **bash on the Pi, over SSH** — not your
> laptop, not PowerShell. Flash the SD with SSH enabled (Phase 1), `ssh YOUR_USER@raspberrypi.local` in,
> and work there. Every multi-step task is a `./scripts/*.sh` helper — you never run Python by hand.

> ⚙️ **What's an ESC?** It sits between the battery and the drive motor and turns a throttle PWM into
> motor power + direction. Here that PWM comes from **PCA9685 channel 0** (Phase 3), not an RC receiver,
> and the ESC's **BEC** (its built-in supply) back-feeds the PCA9685 `V+` rail to power the steering
> servo. It stays disconnected through Phases 1–6 and is first powered in Phase 8 (where it beeps its
> arming tones). The kill switch goes on the LiPo→ESC line. **This build uses a 2–3S brushed *crawler*
> ESC with direct reverse + a program card** (a SURPASS HOBBY 60A): a crawler ESC gives instant
> forward/reverse and a drag-brake stop, and the card sets reverse force, brake and low-voltage cutoff
> with buttons — no jumper guesswork. Any 2–3S brushed crawler ESC works the same way.

> 📖 **Jargon, once:** **GPIO** the Pi's numbered pins (GPIO23) · **I2C** the 2-wire bus (SDA+SCL) to the
> PCA9685 · **PWM** the timed pulse that sets motor speed + servo angle · **PCA9685** the 16-channel board
> that makes those pulses · **ESC / BEC** the throttle/motor driver / its built-in servo supply ·
> **HC-SR04P** the 3.3 V ultrasonic sensor (×3) · **CSI** the camera-ribbon port · **HAT / UPS** a board
> that stacks on the Pi / the battery-backed one · **SSH** remote login to the Pi · **LiPo** the motor
> battery · **TTS/STT** Piper (speech out) / whisper.cpp (speech in) · **VLM** the vision model (moondream)
> · **ALSA** the Linux audio stack — why audio devices are numbered "cards" addressed as `plughw:CARD=…`,
> and what `aplay`/`arecord`/`amixer`/`~/.asoundrc` belong to (on Pi OS Lite it's the *whole* audio stack).

---

## Phase 1 — Flash & brain bring-up (bench, no peripherals)

**Do this first.** You can stand up the *entire* brain — voice → model → the exact GPIO/PWM/I2C it would
drive — before any other hardware exists. Nothing moves (no motor; dry-run drives nothing).

1. **Fit the Active Cooler FIRST**, before any HAT: thermal pad onto the SoC, press the two spring-pins
   into the Pi 5's holes, plug the fan ribbon into the dedicated **4-pin JST fan header** (between the
   40-pin header and the board edge — *not* a GPIO 5 V pin). Effectively mandatory: sustained inference
   throttles a bare board at 80–85 °C.
2. **Flash Raspberry Pi OS Lite (64-bit, Bookworm)** with **Raspberry Pi Imager** + an SD reader. In the
   Imager's OS-customisation (⚙️):
   - **Set the username you'll use for everything — this becomes `YOUR_USER`** in this doc and in
     `config/robot.env`'s whisper/piper paths. You replace it **only in `config/robot.env`** (step 5).
     `scripts/robot.service` also has `YOUR_USER` but **don't edit it** — `./scripts/run_me.sh
     --install-boot` fills in your username + repo path automatically.
   - **Enable SSH**, set **Wi-Fi** SSID/password + locale so the Pi joins headless. *(Need to switch Wi-Fi
     later without a monitor? [docs/change-pi-wifi.md](change-pi-wifi.md) sets up a phone-hotspot fallback
     + a Comitup captive portal + passwordless mobile SSH.)*

   Insert the card into the Pi.
3. **Power from the official 27 W USB-C PSU only — no UPS HAT yet.** It negotiates 5 V/5 A, so there's no
   low-power warning and no 600 mA peripheral clamp. Boot, wait ~60 s, then **SSH in** with your username +
   hostname (default `raspberrypi.local`):
   ```bash
   ssh YOUR_USER@raspberrypi.local        # macOS / Windows 10+ / most Linux via mDNS; accept the host-key prompt
   # if .local won't resolve, use the Pi's IP from your router:
   ssh YOUR_USER@192.168.1.42
   ```
   On Windows the `ssh` client ships with PowerShell/Terminal (install Apple Bonjour if `.local` won't
   resolve). **Every command from here runs in this SSH session, in bash.**
4. **Install the stack and reboot:**
   ```bash
   git clone https://github.com/YOUR_GITHUB_USER/LocalAiRobot.git ~/LocalAiRobot   # your GitHub username
   cd ~/LocalAiRobot && ./scripts/install_stack.sh
   sudo reboot
   ```
5. **Plug in the USB mic + speaker, then set up `config/robot.env`** — **three** things (leave the rest):
   - **`YOUR_USER`** → your real username, in the four whisper/piper path lines. One-liner (uses `whoami`):
     ```bash
     sed -i "s/YOUR_USER/$(whoami)/g" config/robot.env
     ```
     Check: `grep whisper config/robot.env` shows `/home/<you>/whisper.cpp/...` (no literal `YOUR_USER`).
   - **`ROBOT_DRY_RUN=1`** — bench mode: fakes motor + sensors so nothing needs wiring yet.
     ```bash
     sed -i "s|^ROBOT_DRY_RUN=.*|ROBOT_DRY_RUN=1|" config/robot.env
     ```
   - **`ROBOT_MIC`** → your USB mic as `plughw:CARD,DEVICE`. Run `arecord -l`, find your mic's line (e.g.
     `card 2: Device [USB PnP Sound Device], device 0`). **By name is more robust** (survives
     card-renumbering) — use the bracketed name (here `Device`):
     ```bash
     sed -i "s|^ROBOT_MIC=.*|ROBOT_MIC=plughw:CARD=Device,DEV=0|" config/robot.env
     ```
     (`plughw` = ALSA's wrapper that auto-resamples to the 16 kHz the app records at; plain `hw` is rejected.)

   Check: `grep -E '^ROBOT_(DRY_RUN|MIC)=' config/robot.env`. After **any** edit, reload it into your
   shell: `set -a; source config/robot.env; set +a`.

   > ⚠️ **The brain reads settings from the shell, not the file.** The installer added an auto-load line to
   > `~/.bashrc`, so **every new SSH session loads `config/robot.env`**, and `run_me.sh`/`shutdown.sh`
   > source it themselves. The one time you must reload by hand is **right after editing in a shell you keep
   > using** — re-source it (above) or open a new SSH session.
6. **Send the local LLM a prompt and watch it reply** — proof the AI brain runs **entirely on the Pi**
   (Ollama serves on `localhost`, no internet):
   ```bash
   ollama list                                        # expect gemma4:e4b-it-qat (text) AND moondream (vision)
   ollama run gemma4:e4b-it-qat "reply with one word: ready"  # first run is a slow cold load; Ctrl-D to exit
   ```
   Ask anything to be sure it's thinking (`ollama run gemma4:e4b-it-qat "name three primary colours"`).
   Proof it's offline? Turn Wi-Fi off and ask again. Then watch the temperature (it's CPU-heavy):
   ```bash
   vcgencmd get_throttled            # 0x0 = healthy (no under-volt / throttling)
   watch -n1 vcgencmd measure_temp   # stays < 80 °C with the Active Cooler; Ctrl-C to stop
   ```
   `moondream` is the vision model; missing either → re-run `./scripts/install_stack.sh`.

   > **Switch the local model later:** set `ROBOT_MODEL` (text) / `ROBOT_VISION_MODEL` (vision),
   > `ollama pull <new>`, re-run `source`, restart the brain. Reclaim the old model's disk:
   > `./scripts/cleanup_pi.sh --models <old-name>` (dry-run + confirm; everything else stays).
7. **Prove the mic, then run the brain.** Dry-run records from the *real* mic. This records 3 s with a
   **live level bar** (`-V mono`) that jumps as you talk, then plays it back:
   ```bash
   set -a; source config/robot.env; set +a    # load your config (every new shell)
   arecord -D "$ROBOT_MIC" -d 3 -f S16_LE -r 16000 -c 1 -V mono /tmp/test.wav && aplay /tmp/test.wav
   ```
   The bar should jump as you speak (a low % like `06%` = speak louder/closer). **No playback sound?**
   (e.g. `aplay: audio open error: Unknown error 524`) — `aplay` uses the *default* ALSA output and the
   robot's voice depends on it; run **`./scripts/setup_audio.sh`** to point the default at your USB speaker
   (🔊 callout below). **`arecord` errored / file silent?** That's the mic — fix `ROBOT_MIC`.

   **Test the speaker** (after `setup_audio.sh`) — `speaker-test` plays through the ALSA default, the exact
   output the robot's voice uses:
   ```bash
   speaker-test -D default -t wav -l 1     # you should hear "Front Left"; Ctrl-C to stop
   ```
   Then start the brain in dry-run:
   ```bash
   ./scripts/run_me.sh --dry
   ```
   At the prompt press **ENTER**, say *"go forward and bear left"*, and read the log — it prints exactly
   what it *would* drive, nothing moving. **q** + ENTER to quit.
   ```
   *** DRY RUN -- no hardware will be driven ***
   Heard: "go forward and bear left"
   robot.hw INFO I2C->PCA9685 ESC   ch=0 throttle=+0.140 pulse_us=1570
   robot.hw INFO I2C->PCA9685 SERVO ch=1 steer=LEFT   angle=55.0deg pulse_us=1111
   ```
   See the safety features fire (re-edit `robot.env`, re-`source`, re-launch): `ROBOT_FAKE_DISTANCE_CM=20`
   → "go forward" → the forward gate trips; `ROBOT_FAKE_VISION=sofa:left:near:0.9` → "find the sofa" → the
   whole explore loop runs, no camera.
8. **Wake-word check — the mode matters.** The wake word is **on by default**, wired to a ready model in
   `models/` — the reliable stock **`hey_jarvis.onnx`** (say *"hey jarvis"*); a custom **`hey_richard.onnx`**
   also ships but needs a stronger retrain. Live wake works on a fresh clone, nothing to download.
   - **In dry-run the wake word is *simulated* — there's no live mic.** At the `type r=simulate wake`
     prompt, **type `r` + ENTER** to fire a wake (it then records your spoken *command*). Speaking "Richard"
     out loud here does nothing — it just records "Richard" as a command (`action=SAY`). `r` proves the
     wake→record→think→act *plumbing*, not acoustic detection.
   - **To actually say "hey jarvis" and have it wake, run LIVE:** `ROBOT_DRY_RUN=0`, re-`source`,
     `./scripts/run_me.sh --live`, say **"hey jarvis"** → `wake: 'hey_jarvis' detected (score=…)`.
     *(Unreliable? Almost always **mic gain** or the threshold — see below. A missing model means live has
     no trigger and the brain exits — use `--dry`; `ROBOT_WAKEWORD_DEVICE` picks the input if not default.)*
   - **Want a custom wake word** (your phrase, your model)? A one-time job on a **PC / Google Colab — not
     the Pi** — producing a small `.onnx`. Follow **[Training your own wake word](opnewakeword/openwake-word.md)**,
     drop the `.onnx` in `models/`, point `ROBOT_WAKEWORD_MODEL` at it (or overwrite `hey_richard.onnx`).
   - **Won't wake / inconsistent?** Usually **mic capture gain** (too quiet → low scores; or clipping →
     scores ~0) or the threshold. The brain logs `best score`/`loudest` every ~15 s — set gain with
     `amixer -c <card> sset Mic 75%` (aim `loudest` mid-scale). Full procedure:
     [the wake-word guide's tuning list](opnewakeword/openwake-word.md). False-firing? Raise
     `ROBOT_WAKEWORD_TRIGGER_LEVEL` to 2 (or `ROBOT_WAKEWORD_THRESHOLD`).
   - **Wake ack:** each *"hey jarvis"* plays a random clip from `sounds/wake_word_ack/` ("Yes?/Hi") as the
     face switches to *listening*; then one from `sounds/ack_sounds/` ("Got it") once your command is
     accepted. Earcons are Piper-generated on your PC + committed (see [`sounds/README.md`](../sounds/README.md));
     the Pi just plays the WAVs. Warmup is on (`ROBOT_WARMUP=1`) so the first command isn't a cold load.

> 🔊 **Audio devices — and the card-numbering trap.** Plugging in a USB speaker **renumbers the ALSA cards**
> (they also shuffle on reboot), so address everything **by name**, not by number. **The easy way:**
> **`./scripts/setup_audio.sh`** — it finds your USB *playback* card, makes it ALSA's **`default`** (writes
> `~/.asoundrc`), sets + persists volume, and prints the `ROBOT_*` values to paste. `install_stack.sh` runs
> it once; re-run whenever you change speakers. By hand:
> ```bash
> SPK=UACDemoV10                 # speaker id from `aplay -l` (the word after card N:)
> cat > ~/.asoundrc <<EOF        # speaker = default -> the VOICE comes out it
> pcm.!default { type plug; slave.pcm "hw:CARD=$SPK,DEV=0" }
> ctl.!default { type hw; card $SPK }
> EOF
> amixer -c "$SPK" sset PCM 100% && sudo alsactl store   # volume + persist (USB audio resets on reboot)
> ```
> Then in `config/robot.env`, all **by name** so a reboot/replug never breaks them:
> ```
> ROBOT_MIC=plughw:CARD=<mic-id>,DEV=0                            # command capture
> ROBOT_WAKEWORD_DEVICE=<unique bit of the mic name, e.g. PnP>    # wake mic (name-match)
> ROBOT_SOUNDS_DEVICE=plughw:CARD=$SPK,DEV=0                      # earcons
> ```
> **Any USB speaker works** — it's just this config, no code change. A USB-powered one needs no separate
> supply (handy on the battery robot). Gotcha: tiny speakers often expose **no volume control**
> (`amixer -c "$SPK" scontrols` lists nothing) — then skip the `amixer` line. The half-duplex `audio_lock`
> stops the mic recording the speaker, so a plain mic + speaker pair is fine.

> 💬 **It also chats and remembers (dry-run too).** Ask a question or say hello → it just **answers** and
> stays put (`SAY`). Say *"this is my wife Shani"* → it **photographs** what the camera sees and stores
> **name + relationship + a VLM description** in SQLite (`~/.localairobot/`), then feeds known people/things
> back into the model each turn (a lightweight RAG). You need a speaker to *hear* replies; the console
> prints them either way.

With the brain proven, wire the peripherals one at a time (Phases 3–6) — still on the bench, ESC/motor
disconnected. **First read Phase 2** so you don't bridge the power domains.

---

## Phase 2 — Power: one 3S LiPo, two rails (read before you wire)

**One 3S LiPo feeds everything** through two branches: the **ESC** (drive motor + a BEC for the servo) and a
**ZTW UBEC step-down (5–6 V) → the Pi's UPS input** (the UPS 18650s stay as a backup buffer). The LiPo reaches
the Pi **only through the UBEC — never wire the LiPo straight to the Pi.** **Do** tie all the *grounds*
together, or the Pi's PWM signals won't register at the ESC/servo.

![One 3S 11.1V LiPo powers everything: an ESC branch (drive motor + a BEC for the servo) and a ZTW UBEC step-down (5-6V) feeding the Raspberry Pi 5's UPS HAT input (2x 18650 backup) + Active Cooler; the Pi drives the Arducam IMX519 autofocus camera over CSI, the USB-C face screen, and three 3.3V HC-SR04P sensors. The PCA9685 takes 3.3V logic from the Pi and its V+ rail from the ESC BEC. All grounds are common across Pi, PCA9685, ESC and UBEC.](images/assembly.png)

For the full picture — the shared ground, the ESC **BEC** feeding the PCA9685 V+ rail and the steering servo,
and the **UBEC → UPS → Pi** branch:

![Detailed power: one 3S 11.1V LiPo -> ESC -> drive motor, with the ESC BEC feeding the PCA9685 V+ rail + DS3225 steering servo; a second branch taps the LiPo to a ZTW UBEC step-down (5-6V) that feeds the Pi's UPS HAT input (2x 18650 backup), which powers the Raspberry Pi 5 + Active Cooler; the Pi provides 3.3V logic + I2C to the PCA9685, 3.3V + TRIG/ECHO to three HC-SR04P (front 23/24, left 5/6, right 20/21), a CSI ribbon to the Arducam IMX519 autofocus camera (via the Pi-5 22-to-15 adapter), and USB-C to the face screen; every device shares a common ground.](images/power-domains.png)

- ✅ Match the LiPo plug (XT60 / T-plug / Deans) to the ESC — pick the right one rather than solder.
- ✅ The Pi 5 **Active Cooler is mandatory** — sustained inference thermal-throttles without it.
- ✅ A **LiPo balance charger** is required for the 3S pack; never charge LiPo on a dumb charger.
- ⚡ **UBEC → Pi.** A ZTW UBEC steps the 3S LiPo down to 5–6 V (jumper) to feed the Pi's **UPS input** — set
  the jumper to the UPS's rated input (usually 5 V) and size the UBEC for the Pi 5 (~5 A). Tap it off the
  LiPo **after** the kill switch, so the switch still cuts drive (the UPS keeps the Pi up for a clean stop).
- 🔴 Put a **physical kill switch / unplug loop** on the LiPo→ESC line within reach — your last-resort stop
  if the software dies (there's no hardware watchdog). See the safety note in the appendix.
- ⚡ **BEC voltage = servo voltage.** The ESC's BEC feeds the V+ rail *and* the steering servo. This build's
  crawler ESC has a **7.4 V** BEC — fine for an **HV servo**, but a standard DS3225 is only rated to
  ~6.8 V, so **confirm your servo takes 7.4 V, or power V+ from a separate 5–6 V BEC** instead of the ESC's.

---

## Phase 3 — Wire the PWM bridge (PCA9685)

Still on the bench, **ESC/motor not connected.** The Pi 5 can't cleanly bit-bang servo signals while
running an LLM, so a PCA9685 generates them over I2C. **Wire only the Pi↔PCA9685 logic side** — four
jumpers: `VCC`→Pi **3.3V**, `SDA`→**GPIO2**, `SCL`→**GPIO3**, `GND`→Pi **GND**. The ESC + servo outputs
come in Phase 7.

> 📐 **Full pin-by-pin wiring lives in one place** — board orientation, which output column is ch0 (ESC) /
> ch1 (servo), the ESC 3-wire + servo leads, the V+ rail fed by the ESC's BEC, the exact Pi-header pins,
> and the `i2cdetect` walkthrough — see **[PWM / ESC / servo wiring](wiring/pwm-esc-servo-wiring.md)**.
> This phase is just its logic-side connection; only channels 0 and 1 are used (2–15 stay free).

The DS3225 is a hungry servo (stall ~2.5–3 A); a 2 A BEC covers normal steering but may brown out under
hard load — add a separate servo BEC on the V+ rail if so (and mind the 7.4 V caution in Phase 2).

**Check:** `i2cdetect -y 1` shows the PCA9685 at **0x40** (full grid in the wiring guide).

---

## Phase 4 — Wire the distance sensors (3× HC-SR04P, semi-hex front array)

Three **HC-SR04P** (the 3.3 V variant) in a shallow semi-hex across the front: one **forward**, one
**~45–60° left**, one **~45–60° right** (splaying cuts beam-cone overlap). The forward one drives the
FORWARD-cut safety gate; the side ones keep explore from steering into a near wall. They wire **straight
to Pi GPIO**, powered from **3.3 V** so every ECHO is Pi-safe (no level shifter). Pins (BCM): **front
23/24, left 5/6, right 20/21**.

> 📐 **Full pin-by-pin wiring** — each sensor's VCC/GND/TRIG/ECHO with the physical header pins, the
> Pi-pinout, the bare-5 V divider fallback, and the two by-design sensing limits — is the dedicated
> **[ultrasonic sensor wiring](wiring/ultrasonic-sensors-wiring.md)** guide.

Pins are set in `config/robot.env` (`ROBOT_TRIG_PIN`/`ROBOT_ECHO_PIN`, `ROBOT_LEFT_*`/`ROBOT_RIGHT_*`).
They avoid I2C (2/3), SPI0 (7–11) and UART (14/15); alternates: `16/26`, `22/27`, `19/25`. gpiozero
serialises the three pings (so they don't hear each other) at ~4–6 Hz each — the forward gate reads the
latest cached value, never blocking the control loop.

**Check:** read all three live with **`./scripts/test_sensors.sh`** — cover each beam in turn and watch
*its* number drop (proves front/left/right aren't swapped).

**✅ Phase 4 done when:**
- `./scripts/test_sensors.sh` prints **three** numbers that track real distances — not blank, not stuck at
  `400.0 cm`, no `no echo received` warnings.
- Covering each beam drops **only that sensor's** number (front/left/right not swapped), springing back
  when uncovered.
- *(Optional, once the chassis is built)* `./scripts/run_diagnostics.sh` shows ✓ for all three.

`test_sensors.sh` covers the **wiring**; the in-brain obstacle *gate* is verified in
[Phase 8](#phase-8--live-calibration--bring-up-wheels-off-the-ground) step 5 (say *"go forward"* + a hand
~20 cm ahead of the **front** beam → the motor cuts).

---

## Phase 5 — Wire the camera (Arducam **IMX519** autofocus recommended; any libcamera module works)

Visual input for the **vision model** (`moondream` by default, set via `ROBOT_VISION_MODEL`; the text brain
`gemma4:e4b-it-qat` is multimodal too and can take over if you empty that var). It's on the **CSI ribbon**,
not GPIO/I2C, so it doesn't conflict with anything.

- **Any libcamera module works — zero code change.** The build uses an **Arducam IMX519 16MP autofocus**:
  it focuses on whatever you show it, so `remember`/`find` shots of a held-up object or a face come out
  **sharp**. **Fixed-focus** modules (Pi Camera v2 **IMX219**, or **OV5647** v1) also work but can't focus
  close up. A **NoIR** variant drops the IR-cut filter (reddish daylight, but sees under IR light).
- ⚠️ **The Pi 5 needs the 22→15 cable.** Its CSI/DSI ports are the narrow **22-pin, 0.5 mm** type; a
  module's 15-pin cable **won't fit**. Get a **22→15 adapter FPC** (the Arducam kit often bundles this as
  its "Pi Zero cable") and run it from the camera to either CAM/DISP port (contacts correct way, latch closed).
- Install: `python3-picamera2` comes from **apt** (the installer does this) and the venv uses
  `--system-site-packages`. Confirm with `rpicam-hello --list-cameras`, then grab a frame with
  `rpicam-jpeg -n -o /tmp/test.jpg`. *(Brain logs `camera unavailable`? libcamera saw **no** camera —
  reseat the ribbon. The brain runs fine without it; vision just reports "no frame".)*
- **A third-party sensor (IMX519) needs a boot overlay** — `camera_auto_detect` only finds the *official*
  OV5647/IMX219/IMX708. **`install_stack.sh` sets it up for you** (`camera_auto_detect=0` +
  `dtoverlay=imx519,cam1`, backing up `config.txt`); the install's reboot brings it up. Is `rpicam-hello
  --list-cameras` empty afterwards? the camera's on the other CSI port — re-run with
  `ROBOT_CAMERA_OVERLAY=imx519,cam0 ./scripts/install_stack.sh` and reboot. Official modules need no overlay
  (install with `ROBOT_CAMERA_OVERLAY=none`).
- **A NoIR module has no IR-cut filter:** reddish/magenta in daylight, and blind in the dark without an
  **IR illuminator** (850/940 nm). A *standard* module gives natural colour but is still dark-blind.
- Tuning: `ROBOT_CAMERA_ENABLE`, `ROBOT_CAMERA_SIZE`, `ROBOT_CAMERA_WARMUP_S`, `ROBOT_CAMERA_AUTOFOCUS`
  (on by default — a one-shot focus before each capture on AF sensors; a no-op on fixed-focus). Vision is **CPU-only and
  slow** on the Pi 5 (`ROBOT_VISION_TIMEOUT_S` defaults to 600 s) — the car holds neutral during each look.

---

## Phase 6 — Wire the face panel (USB-C serial **or** DSI/HDMI — pick one)

The robot's face. `face.py` has three backends and `ROBOT_FACE_BACKEND=auto` tries them in order:

- **`turing`** — a small **USB-C "secondary screen"** (e.g. 3.5" 320×480). On Linux these are **not** a
  framebuffer; they enumerate as a **USB-serial** device, driven by `turing-smart-screen-python` (cloned to
  `~/turing-smart-screen-python` by the installer).
- **`framebuffer`** — any **DSI or HDMI** display (e.g. a Freenove 5" 800×480 DSI). `face.py` writes the
  frame straight to `/dev/fb0` (no X, no pygame).
- **`simulated`** — no panel; just logs `FACE -> <state>`. A missing face **never blocks driving**.

**USB-C serial panel (`turing`):** connect it with a **data** USB-C cable → `/dev/ttyACM0` (CH340, USB id
`1a86:5722`). The installer adds you to the **`dialout`** group — re-login. Confirm: `lsusb | grep -i 1a86`
and `ls /dev/ttyACM*`. Run the library's `simple-program.py` once (`cd ~/turing-smart-screen-python`) to see
which revision drives your panel, then set `ROBOT_FACE_REVISION=A` (Turing 320×480) or `B` (XuanFang) and
re-`source`.

**DSI/HDMI panel (`framebuffer`):** plug the DSI ribbon into a Pi 5 **CAM/DISP** port (same narrow 22-pin
connector as the camera; mind which port + contact direction). A "driver-free" DSI panel shows up as
`/dev/fb0`; some need a `dtoverlay` line in `/boot/firmware/config.txt` per the panel — check `ls /dev/fb0`
exists first. Set `ROBOT_FACE_BACKEND=framebuffer` (or leave `auto`) and re-`source`. It **auto-reads the
panel's size + colour depth**, so 800×480 and the Pi 5 32-bit framebuffer are handled (`ROBOT_FACE_WIDTH/HEIGHT`
ignored). Best on **Pi OS Lite / the console** (a desktop compositor may draw over the face). Red/blue
swapped? That's a one-line flip in `_FramebufferBackend.show`.

It's **optional and fail-safe**: no panel → `auto` ends at `simulated` and the robot runs exactly the same.
- **Custom face frames:** drop PNGs in `brain/faces/<state>/NN.png` (states
  `idle/listening/thinking/talking/exploring/happy/confused/alarmed/warmup/capturing`). **≥2 frames
  animate** on a background thread; 1 frame is static; **none** falls back to the built-in procedural face.
  Tune `ROBOT_FACE_FPS` / `ROBOT_FACE_FRAMES_DIR`; you design these on your PC (see
  [`brain/faces/README.md`](../brain/faces/README.md)). Inspect in dry-run with `ROBOT_FACE_DUMP=<dir>`.
- Mount it forward-facing as the robot's "face"; dim it with `ROBOT_FACE_BRIGHTNESS` to save power.

**Now run the brain LIVE on the bench (still no chassis/motor).** With the PCA9685 + 3 sensors + camera +
face wired and the ESC/motor *not connected*, the same `robot.hw` logs now reflect real I2C/GPIO:

```bash
i2cdetect -y 1                  # PCA9685 at 0x40
rpicam-hello --list-cameras     # camera detected (imx519 / IMX219 / OV5647)
lsusb | grep -i 1a86            # USB-C face panel (turing backend) ...
ls /dev/fb0                     # ... OR a DSI/HDMI panel (framebuffer backend), if you went that route
./scripts/test_sensors.sh       # read the 3 sensors live before trusting the gate (Ctrl-C to stop)
./scripts/run_me.sh --live      # foreground, real hardware (ESC/motor still unplugged)
```

Only after this live bench pass do you build the chassis and connect the ESC/motor (Phase 7).

---

## Phase 7 — Mechanical build (chassis + mount everything)

This is where the **ESC, motor, and LiPo finally get connected** — until now nothing could move.

1. Assemble the 1/10 roller chassis (drivetrain, portal axles, links, shocks, 1.9" wheels). Mount the
   **540 35T motor** into the transmission; mesh the pinion/spur with a sliver of slack (a strip of paper
   between the gears while you tighten sets the backlash).
2. Mount the **steering servo** to the front axle servo mount; connect the link. Leave the horn off until
   you've found the servo's center in calibration, then attach it pointing straight.
3. Build the brain stack: Pi 5 + Active Cooler (+ UPS HAT later — Phase 9) on a 3D-printed/cut deck on the
   frame rails. Keep fan airflow clear.
4. Mount the **ESC**, **PCA9685**, **UBEC**, and **LiPo**; route wiring clear of the driveshafts + steering
   links. Keep the mic away from the cooler fan. **Now make the power connections deferred in Phase 3** —
   ESC → motor, ESC 3-wire → PCA9685 **ch0**, **steering servo → ch1**, **LiPo → ESC through your kill
   switch**, and the **LiPo → UBEC → the Pi's UPS input** (tap after the kill switch) — pin-by-pin in the
   **[wiring guide](wiring/pwm-esc-servo-wiring.md)**. Keep all grounds common; feed the Pi **only via the
   UBEC**, never the LiPo direct (Phase 2).
5. Mount the **3-sensor plate** across the front (forward + ~45–60° left/right), clear of the wheels and
   roughly level. Mount the **camera** forward-facing near the sensors, and the **face panel** where you
   want the robot to "look at" you. Route the ribbons + USB cables away from the driveshafts.

---

## Phase 8 — Live calibration & bring-up (wheels OFF the ground)

Phases 1–6 verified the brain, the I2C bus, and the sensors; this phase powers the **ESC for the first
time** — keep the **wheels OFF the ground until the very end** and a **physical kill switch in hand**.
Every `ROBOT_*` below is a line in `config/robot.env`: edit it, re-run `./scripts/run_me.sh --live` (it
reloads config each launch), repeat until dialled in. If a board isn't found, the brain **logs which
device failed and runs degraded** (motors off) instead of crashing — read that line, fix the wiring, relaunch.

> 🔧 **First, verify the wiring (live self-test):** **`./scripts/run_diagnostics.sh`** checks the ESC/PWM
> board, motor, servo, 3 sensors, camera, speaker, mic and face and points to the exact fix for anything
> wrong (wheels OFF; the motor spin is gated). Reference: [docs/diagnostics.md](diagnostics.md).

> 🪑 **Test voice/vision without driving:** **`ROBOT_MOTORS_OFF=1`** (keep `ROBOT_DRY_RUN=0`) runs fully
> live — wake word, camera, sensors, face — with the **ESC/steering getting no signal** (wheels won't
> move). Ideal for desk-testing the brain on the assembled car before the throttle steps below.

1. **Program the ESC (one-time, with the card).** Plug the card onto the ESC's BEC lead and power the ESC,
   then set: **Max Reverse Force** (your taste), **Drag Brake Force** (~70–80% for a gentle hold), **Low
   Voltage Cut-off 3.1 V/cell** for LiPo, and a low **Initial Start Force**. It saves into the ESC — the
   card isn't needed afterwards. (Defaults already give direct reverse + a firm brake, so this is tuning,
   not a requirement.)
2. **ESC arm:** with the LiPo connected, the brain holds neutral for `ROBOT_ESC_ARM_SECS` at startup — the
   ESC beeps **cell-count tones, then one long "ready" beep**. No long beep = it didn't arm: power-cycle
   the ESC while the brain holds neutral and wait for it (cheap ESCs can need a couple of tries). Still
   nothing → check signal/GND to PCA9685 ch0 and the BEC on V+.
3. **Steering:** say *"center" / "left" / "right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT` so the wheels
   center true and turn fully without the servo buzzing (binding = back the angle off).
4. **Throttle:** say *"go forward"* slowly, then *"go back"*. Forward maps onto the ESC's usable range via
   `ROBOT_ESC_FWD_FLOOR/CEIL`; reverse is direct (`ROBOT_ESC_REVERSE_DOUBLETAP=0`) and bounded by
   `ROBOT_ESC_REV_FLOOR/CEIL`. Wheels spin **backward** on "go forward"? Swap the two motor wires (or set
   `ROBOT_THROTTLE_INVERT=1`).
5. **Obstacle gate:** *"go forward"* + a hand ~20 cm ahead of the forward sensor → motor cuts, robot says
   *"Obstacle ahead. Stopping."*
6. **Camera/explore (optional):** `rpicam-hello` ok, then *"look around"* — it should describe what it
   sees. Benchmark one *"find the …"* on your Pi and tune `ROBOT_EXPLORE_*` to the measured latency.
7. **On the floor:** only now, at low throttle, with the kill switch in hand.

Full calibration reference (every `ROBOT_*` knob) is in the [README](../README.md).

---

## Phase 9 — Add the UPS HAT (later, for untethered running)

Build and test on the 27 W PSU first; add the UPS once everything works.

- Use a **pogo-pin, bottom-mount** UPS HAT (Geekworm X1200/X1202, Waveshare UPS HAT (B)/(E)). These power
  the Pi via spring pins on the **underside** and **don't consume the 40-pin header**, so the PCA9685, the
  three sensors, and the camera/face all keep working.
- Stack top → bottom: **Active Cooler → Raspberry Pi 5 → UPS HAT (underneath)**.
- The fuel gauge usually sits at I2C **`0x36`** (no clash with the PCA9685 at `0x40`) — after fitting,
  `i2cdetect -y 1` shows both. Follow the HAT's documented charge/power-up order.
- ⚠️ An older **GPIO-header** HAT will occupy the header the sensors/PCA9685 need — use stacking headers or
  pick a pogo-pin model.

---

## Appendix — Command cheat-sheet & safety

### Command cheat-sheet

Every command runs in **bash on the Pi** (over SSH) — *not* your laptop, *not* PowerShell. Lines starting
with `#` are comments.

```bash
# Mic device id  →  put the plughw string in config/robot.env (ROBOT_MIC)
arecord -l

# Audio sanity check (same 16 kHz mono S16_LE the app uses); -V mono draws a live level bar as you talk
arecord -D plughw:2,0 -d 3 -f S16_LE -r 16000 -c 1 -V mono /tmp/test.wav && aplay /tmp/test.wav

# Confirm the bus + peripherals
i2cdetect -y 1                  # PCA9685 at 0x40 (and 0x36 once the UPS is fitted)
rpicam-hello --list-cameras     # camera detected (after the 22→15 adapter cable)
lsusb | grep -i 1a86            # USB-C face panel

# Read all three HC-SR04P sensors live -- cover each beam in turn and watch ITS number drop (Ctrl-C to stop)
./scripts/test_sensors.sh

# Full live wiring self-test: green/red per peripheral + fix pointers. Wheels OFF; motor spin gated.
./scripts/run_diagnostics.sh
```

### Safety note — software failsafe, not hardware

`driver.py` runs a control thread that forces the ESC to **neutral** on STOP, on error, on a forward
obstacle, and when a move's `duration_ms` deadline passes; the ESC is armed at neutral on boot. The
explore/find behaviour only ever calls this same gated path and holds the car at neutral during the slow
vision step. This replaces the old Pico hardware watchdog **in software only** — if the whole Python
process is killed, the PCA9685 keeps emitting the last pulse and nothing in hardware stops the motor. The
**physical kill switch on the LiPo is your real safety net.** Keep throttle low and durations short.

---

## Appendix — Run it day-to-day (`run_me.sh` / `shutdown.sh`)

Two wrappers in `scripts/` source `config/robot.env` for you (and skip silently if it's missing).

| Command | What it does |
|---|---|
| `./scripts/run_me.sh` | **The launcher.** Mode from `ROBOT_DRY_RUN` (override `--dry` / `--live`). **Foreground**, interactive: **`q`** = quit, **Ctrl-C** = stop (neutralises the motor). **`--live`** = the wake word is the only trigger; **`--dry`** = ENTER talks, `r` simulates a wake. `--dry` drives nothing; `--live` is real hardware. |
| `./scripts/run_me.sh --detach` | Run **live in the background** (survives SSH logout), listening for the wake word. Stop it with `shutdown.sh`. |
| `./scripts/run_me.sh --reload` | **Re-apply an edited `config/robot.env`** to a running robot (restarts the boot service if installed, else the `--detach` instance). |
| `./scripts/shutdown.sh` | **Stop** a detached brain (SIGINT → neutralises the motor). Add **`--poweroff`** to also halt the Pi. (Foreground run? just Ctrl-C. Boot service? `sudo systemctl stop robot.service`.) |
| `tail -f ~/.localairobot/robot.log` | Watch a **detached** brain's output (PID in `~/.localairobot/robot.pid`). |

> 🔴 **On real wheels:** keep the **kill switch in reach** and bench with `--dry` first. **Live is
> wake-word-only** (ENTER works only in `--dry`); with no working wake model the brain exits at startup
> (set it up in Phase 1 step 8) — true for `--live` *and* the headless modes. For first live runs,
> foreground `--live` is safest: you watch every command in the log and `q`/Ctrl-C stops instantly.

**Auto-loading the config.** `install_stack.sh` adds a line to `~/.bashrc` so every new SSH session loads
`config/robot.env` (and `run_me.sh`/`shutdown.sh` source it themselves). To re-add it by hand:
```bash
echo '[ -f "$HOME/LocalAiRobot/config/robot.env" ] && { set -a; . "$HOME/LocalAiRobot/config/robot.env"; set +a; }' >> ~/.bashrc
```

**Start the brain automatically on boot (no SSH).** A systemd service (`scripts/robot.service`) auto-starts
the brain every boot and relaunches it if it crashes. Two ways to install it:
```bash
./scripts/install_stack.sh --bootstrap   # one-shot: install the AI stack AND enable boot autostart
./scripts/run_me.sh --install-boot        # OR just the service, on an already-installed stack
sudo systemctl start robot.service        # (optional) start it now without rebooting
journalctl -u robot.service -f            # watch its log live (the unit logs to journal; Ctrl-C to exit)
./scripts/run_me.sh --disable-boot        # stop auto-starting on boot
```
The unit uses **absolute paths** and runs the brain through the **venv Python** with `config/robot.env`
sourced first (exactly like `run_me.sh`), and is ordered **`After=network-online.target`** so Wi-Fi/SSH
(and the [Comitup portal](change-pi-wifi.md)) come up first. `--bootstrap` also enables
`NetworkManager-wait-online.service` to make that wait real. The brain is **fully offline**, so this is
convenience, not a requirement — if booting away from known Wi-Fi feels slow,
`sudo systemctl disable NetworkManager-wait-online.service`.

Set `ROBOT_DRY_RUN=0` first — a service has no terminal, so the **wake word is the only way to talk to
it**; without a working wake model it starts, finds no trigger, and exits. The same 🔴 kill-switch note
applies. `./scripts/cleanup_pi.sh` removes the service (and that network wait) again.

---

## Appendix — Resetting the Pi (`cleanup_pi.sh`)

Want your ~3.5 GB back? `scripts/cleanup_pi.sh` **undoes `install_stack.sh`** — it removes the AI stack,
models, venv and runtime data. It's safe: **(1)** a dry run listing exactly what would go, **(2)** a
`Shall I proceed? [y/N]` prompt, **(3)** deletion **only** if you type `y`. Run it from your clone, as your
normal user (not `sudo`):

```bash
cd ~/LocalAiRobot
./scripts/cleanup_pi.sh -n        # DRY RUN: lists what exists and would go (changes nothing)
./scripts/cleanup_pi.sh           # dry run -> "Shall I proceed?" -> deletes only if you say yes
./scripts/cleanup_pi.sh --deep    # ALSO apt-remove the installer's packages + disable I2C
./scripts/cleanup_pi.sh --repo    # ALSO delete the ~/LocalAiRobot clone itself (runs last)
./scripts/cleanup_pi.sh --models gemma3:4b   # remove ONE Ollama model only (to switch the local model)
sudo reboot                       # finish up: clears /tmp and applies the I2C change
```

**Removes by default** (only what still exists — the dry run shows you first): `~/whisper.cpp`, `~/piper`,
`~/turing-smart-screen-python`, `~/openwakeword`, Ollama + all pulled models, `brain/.venv` (which also
holds the openWakeWord models), `~/.localairobot` (scene memory, taught photos + the run log/PID),
`/tmp/test.*`, an installed `robot.service`, the `config/robot.env` auto-load line in `~/.bashrc`, and the
`~/.asoundrc` written by `setup_audio.sh` (restoring any backup).

**Good to know:** `--deep` also apt-removes packages the installer added (cmake, build-essential,
i2c-tools, …) — skip it if you just want the disk space back. It leaves `git`/`curl`/`alsa-utils` and your
group memberships in place. For a guaranteed-pristine machine, re-flashing the SD card is the surest reset.
