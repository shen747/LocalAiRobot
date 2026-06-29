# Assembly & Wiring Notes

Build the LocalAiRobot RC crawler **one phase at a time** — flash the brain, bring it up on the bench,
wire each peripheral with its own check, build the chassis, then calibrate live. Pairs with the
[README](../README.md) (parts list + tuning). Capture your own photos as you go.

![Wiring at a glance: a 2S 7.4V LiPo feeds the ESC (Electronic Speed Controller) (with 6V BEC), which drives the motor and sends ch0 signal + V+ rail + GND to the PCA9685 PWM bridge (0x40); the PCA9685 drives the DS3225 steering servo on ch1 and connects to the Raspberry Pi 5 (+ Active Cooler) via 3.3V VCC, SDA (GPIO2), SCL (GPIO3) and GND; three HC-SR04P sensors (front TRIG23/ECHO24, left 5/6, right 20/21), the Pi Camera V2 NoIR over CSI, and the 3.5" USB-C face screen are all Pi-side; the Pi is powered by a UPS HAT or the 27W bench PSU. The two battery positives are never bridged, but all grounds are tied common.](images/wiring.png)

**Phase map:** **1** flash + brain bring-up · **2** power domains · **3** PCA9685 · **4** sensors ·
**5** camera · **6** face · **7** mechanical build · **8** live calibration · **9** UPS HAT.
Phases 1–6 run **on the bench on the 27 W PSU with no chassis and no motor** — nothing can move until
Phase 7 connects the ESC (Electronic Speed Controller).

> 🖥️ **Where these commands run.** Unless noted, every shell command in this doc runs in **bash on the
> Pi, over SSH** — not on your laptop and not in PowerShell. Flash the SD card with SSH enabled (Phase 1),
> then `ssh YOUR_USER@raspberrypi.local` in and work from there. Every multi-step task is wrapped in a
> `./scripts/*.sh` helper — you never run Python by hand.

> **What's an ESC (Electronic Speed Controller)?** The **ESC (Electronic Speed Controller) (Electronic Speed Controller)** is the standard RC component wired between
> the battery and the drive motor: it converts a throttle PWM signal into motor power (and direction).
> On this build that throttle PWM comes from **PCA9685 channel 0** (Phase 3), *not* from an RC receiver,
> and the ESC (Electronic Speed Controller)'s built-in **6 V BEC** (Battery Eliminator Circuit) back-feeds the PCA9685 `V+` rail to
> power the DS3225 steering servo. It stays disconnected through Phases 1–6 and is first powered in
> Phase 8 calibration (where it beeps its arming tones). The kill switch goes on the LiPo→ESC line.

> 📖 **Jargon, defined once.** This build is hardware-heavy — here are the abbreviations the rest of the
> doc leans on, so you don't have to look them up:
> - **GPIO** — General-Purpose Input/Output: the Raspberry Pi's numbered pins (e.g. GPIO23) you wire sensors to.
> - **I2C** — Inter-Integrated Circuit: a 2-wire bus (SDA + SCL) the Pi uses to talk to the PCA9685 board.
> - **PWM** — Pulse-Width Modulation: the timed pulse that tells the ESC how fast to spin and the servo where to point.
> - **PCA9685** — the 16-channel board that generates those PWM pulses for the Pi (the "PWM bridge").
> - **ESC / BEC** — Electronic Speed Controller, the throttle/motor driver (see above) / its built-in 6 V supply.
> - **HC-SR04P** — the 3.3 V ultrasonic distance sensor (×3: front, left, right).
> - **CSI** — Camera Serial Interface: the ribbon-cable port the Pi Camera plugs into.
> - **HAT / UPS** — Hardware Attached on Top, a board that stacks on the Pi / the Uninterruptible Power Supply one (its own battery).
> - **SSH** — Secure Shell: how you log into the Pi remotely from your laptop's terminal.
> - **LiPo** — Lithium-Polymer, the motor battery. **TTS / STT** — text-to-speech (Piper) / speech-to-text (whisper.cpp).
> - **VLM** — Vision-Language Model: the model (moondream) that reads the camera frames.
> - **ALSA** — Advanced Linux Sound Architecture: the Linux audio subsystem every program uses to reach the
>   sound hardware. It's why audio devices are numbered "cards" (`card 2`), addressed as `plughw:CARD=…`, and
>   what `aplay`/`arecord`/`amixer`/`alsactl`/`~/.asoundrc` all belong to. On Pi OS Lite (no desktop sound
>   server) it's the *whole* audio stack — apps talk straight to it.

---

## Phase 1 — Flash & brain bring-up (bench, no peripherals)

**Do this first.** You can stand up the *entire* brain — voice → model → the exact GPIO/PWM/I2C it
would drive — on the bench before any other hardware exists. Nothing can move (no motor yet; dry-run
drives no hardware at all).

1. **Fit the Active Cooler FIRST**, before any HAT: thermal pad down onto the SoC, press the two
   spring-pins into the Pi 5's mounting holes, and plug the fan ribbon into the dedicated **4-pin JST
   fan header** (between the 40-pin header and the board edge — *not* off a GPIO 5 V pin). It's
   effectively mandatory: sustained LLM/vision inference throttles a bare board at 80–85 °C.
2. **Flash Raspberry Pi OS Lite (64-bit, Bookworm) on another computer.** Use **Raspberry Pi Imager**
   and an **SD card reader**; in the Imager's OS-customisation (⚙️) settings:
   - **Set the username to the account you'll use for everything — this becomes `YOUR_USER`** in this
     doc's commands and in `config/robot.env`'s whisper/piper paths. You replace it **only in
     `config/robot.env`** (step 5) — the brain runs those *exact* paths. `scripts/robot.service` also
     contains `YOUR_USER`, but **don't edit it**: `./scripts/run_me.sh --install-boot` fills in your
     username + repo path automatically when you set up boot autostart.
   - **Enable SSH**, and set your **Wi-Fi** SSID/password + locale so the Pi joins the network headless.

   Insert the card into the Pi.
3. **Power from the official 27 W USB-C PSU only — do NOT fit the UPS HAT yet.** The 27 W supply
   negotiates 5 V/5 A, so there's no low-power warning and no 600 mA peripheral clamp. Boot it, wait
   ~60 s for the headless first boot, then **SSH in from your laptop** with the username + hostname you
   set in the Imager (default hostname `raspberrypi`, so the address is `raspberrypi.local`):
   ```bash
   ssh YOUR_USER@raspberrypi.local        # works on macOS / Windows 10+ / most Linux via mDNS;
                                          # accept the one-time host-key fingerprint prompt
   # if .local doesn't resolve, look up the Pi's IP in your router's client list and use it directly:
   ssh YOUR_USER@192.168.1.42
   ```
   On Windows the `ssh` client ships with PowerShell/Terminal; if `raspberrypi.local` won't resolve,
   use the IP (or install Apple Bonjour for mDNS). **Every command from here on — install, config,
   dry-run, calibration — runs inside this SSH session on the Pi**, in bash (not your laptop's shell).
4. **Install the stack and reboot:**
   ```bash
   git clone https://github.com/YOUR_GITHUB_USER/LocalAiRobot.git ~/LocalAiRobot   # put your GitHub username in place of YOUR_GITHUB_USER
   cd ~/LocalAiRobot && chmod +x scripts/install_stack.sh && ./scripts/install_stack.sh
   sudo reboot
   ```
5. **Plug in the USB mic + speaker, then set up `config/robot.env`** — **three** things (leave everything
   else alone for now):
   - **`YOUR_USER`** → your real Linux username, in the four whisper/piper path lines (the brain runs
     whisper/piper from those *exact* paths). Don't hand-edit four lines — run this one-liner **in the
     shell**; it swaps in your username (from `whoami`) across the whole file in one go:
     ```bash
     sed -i "s/YOUR_USER/$(whoami)/g" config/robot.env
     ```
     Check it worked: `grep whisper config/robot.env` should now show `/home/<your name>/whisper.cpp/...`
     (no literal `YOUR_USER` left).
   - **`ROBOT_DRY_RUN=1`** — bench mode: fakes the motor + sensors so nothing needs wiring yet.
     ```bash
     sed -i "s|^ROBOT_DRY_RUN=.*|ROBOT_DRY_RUN=1|" config/robot.env
     ```
   - **`ROBOT_MIC`** → your USB mic, written as `plughw:CARD,DEVICE`. Run `arecord -l` and find your mic's
     line, e.g.:
     ```
     card 2: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]
     ```
     Read its **card** and **device** numbers (here **2** and **0**), then set it with `sed` — **swap in
     YOUR numbers** (the `|` delimiter avoids clashing with the value, and `^…=.*` replaces the whole line):
     ```bash
     sed -i "s|^ROBOT_MIC=.*|ROBOT_MIC=plughw:2,0|" config/robot.env
     ```
     (`plughw` = "plug + hardware": ALSA's wrapper that auto-resamples to the 16 kHz the app records at;
     plain `hw` would be rejected.) **More robust by name** (survives card-renumbering) — use the bracketed
     name from `arecord -l` (here `Device`):
     ```bash
     sed -i "s|^ROBOT_MIC=.*|ROBOT_MIC=plughw:CARD=Device,DEV=0|" config/robot.env
     ```

   Check the edits stuck: `grep -E '^ROBOT_(DRY_RUN|MIC)=' config/robot.env`. (Prefer an editor?
   `nano config/robot.env` → Ctrl-O, ENTER, Ctrl-X.) After **any** edit, reload it into your shell:
   `set -a; source config/robot.env; set +a`.

   > ⚠️ **The brain reads its settings from the shell, not the file.** Good news: the installer added an
   > auto-load line to `~/.bashrc`, so **every new SSH session loads `config/robot.env` for you**, and
   > `scripts/run_me.sh`/`shutdown.sh` source it themselves. The one time you must reload by hand is **right
   > after editing the file in a shell you keep using** (e.g. for the mic test below) — re-source it, or
   > just open a new SSH session:
   > ```bash
   > set -a; source config/robot.env; set +a    # re-copy ROBOT_* into THIS shell after an edit
   > ```
6. **Send the local LLM a text prompt and watch it reply** (then check the Pi stays cool). This is the
   proof the AI brain runs **entirely on the Pi** — Ollama serves the model on `localhost`, no internet:
   ```bash
   ollama list                                        # expect gemma4:e4b-it-qat (text) AND moondream (vision)
   ollama run gemma4:e4b-it-qat "reply with one word: ready"  # send a text prompt -> the model prints a reply
                                                      #   (first run is a slow cold load; Ctrl-D to exit)
   ```
   You should see the model print its answer. **Ask it anything** to be sure it's really thinking:
   ```bash
   ollama run gemma4:e4b-it-qat "name three primary colours" # one-shot: prompt in quotes, reply prints, done
   ollama run gemma4:e4b-it-qat                              # interactive chat: type, read reply, /bye to quit
   ```
   Want proof it's truly offline? Turn Wi-Fi off and ask again — it still answers. Then watch the
   temperature while the model works (it's CPU-heavy):
   ```bash
   vcgencmd get_throttled            # 0x0 = healthy (no under-volt / throttling)
   watch -n1 vcgencmd measure_temp   # stays < 80 °C with the Active Cooler; Ctrl-C to stop
   ```
   `moondream` is the dedicated vision model; missing either model → re-run `./scripts/install_stack.sh`.

   > **Switching the local model later:** set `ROBOT_MODEL` (text) or `ROBOT_VISION_MODEL` (vision) in
   > `config/robot.env`, pull the replacement with `ollama pull <new>`, re-run the `source` line, then
   > restart the brain. To reclaim the old model's disk, remove just it:
   > `./scripts/cleanup_pi.sh --models <old-name>` (dry-run + confirm; Ollama and everything else stay).
7. **Prove the mic, then run the brain.** Dry-run still records from the *real* mic, so test audio first.
   This records **3 seconds** (`-d 3`) from your mic to a file while drawing a **live level bar**
   (`-V mono`) that jumps as you talk, then plays it back — so you can confirm the mic *visually* even
   with no speaker:
   ```bash
   set -a; source config/robot.env; set +a    # load your config (do this every new shell)
   # -D device · -d 3 = 3 s · -f S16_LE = 16-bit · -r 16000 = 16 kHz · -c 1 = mono · -V mono = live level bar
   arecord -D "$ROBOT_MIC" -d 3 -f S16_LE -r 16000 -c 1 -V mono /tmp/test.wav && aplay /tmp/test.wav
   ```
   The bar should jump right as you speak (a low % like `06%` = speak louder / closer). The trailing
   `/tmp/test.wav` is the output file — don't drop it, or `arecord` dumps raw audio to your screen.
   **No sound on playback?** (e.g. `aplay: audio open error: Unknown error 524`) — `aplay` uses the
   *default* ALSA output and the robot's **voice depends on that default**. Run **`./scripts/setup_audio.sh`**
   to point it at your USB speaker (it writes `~/.asoundrc`); the 🔊 callout at the end of this phase
   explains what that does and the card-renumbering trap. **`arecord` errored or the file is silent?** That's the mic — fix
   `ROBOT_MIC`. Once you hear yourself back, start the brain in dry-run:
   ```bash
   ./scripts/run_me.sh --dry
   ```
   At the prompt, press **ENTER**, say *"go forward and bear left"*, and read the log — it prints exactly
   what it *would* drive, with nothing moving. Press **q** then ENTER to quit.
   ```
   *** DRY RUN -- no hardware will be driven ***
   Heard: "go forward and bear left"
   robot.hw INFO I2C->PCA9685 ESC (Electronic Speed Controller)   ch=0 throttle=+0.140 pulse_us=1570
   robot.hw INFO I2C->PCA9685 SERVO ch=1 steer=LEFT   angle=55.0deg pulse_us=1111
   ```
   Want to see the safety features fire? Re-edit `robot.env`, re-run the `source` line, then re-launch:
   - `ROBOT_FAKE_DISTANCE_CM=20` → say "go forward" → the forward obstacle gate trips in the log.
   - `ROBOT_FAKE_VISION=sofa:left:near:0.9` → say "find the sofa" → the whole explore loop runs, no camera.
8. **Wake-word check — read this carefully; the mode matters.** The wake word is **on by default** and
   wired to a ready-made model in `models/` — currently the reliable stock **`hey_jarvis.onnx`** (say
   *"hey jarvis"*); a custom **`hey_richard.onnx`** (the robot's own name) also ships but needs a stronger
   retrain to be dependable. Either way live wake works on a fresh clone, nothing to train or download.
   *How* you test it depends on dry-run vs live:
   - **In dry-run (where step 7 left you) the wake word is *simulated* — there is no live mic for it.**
     The log even says so: `wake word: DRY RUN (no mic). ENTER=talk, 'r'=simulate 'hey jarvis', q=quit.` So at the
     `[dry-run: no live mic] type r=simulate wake …` prompt, **type the letter `r` then ENTER** to fire a
     wake (it acks, then records your spoken *command*). **Speaking "Richard" out loud here does nothing**
     — if you press ENTER and say "Richard", you've just recorded the word "Richard" as a command, which
     is why you see `action=SAY`. Typing `r` only proves the wake→record→think→act *plumbing*, not the
     acoustic detection.
   - **To actually say "hey jarvis" out loud and have it wake, run LIVE:** set `ROBOT_DRY_RUN=0`,
     re-source (`set -a; source config/robot.env; set +a`), then `./scripts/run_me.sh --live` and say
     **"hey jarvis"** — you should see `wake: 'hey_jarvis' detected (score=…)`. The model is already
     wired, so there's nothing to point at. *(Unreliable? It's almost always **mic gain** or the threshold
     — see the tuning bullet below. A missing/unloadable model means live has no trigger (the brain exits
     — use `--dry`); `ROBOT_WAKEWORD_DEVICE` picks the input if it's not the default.)*
   - **Want a *different* or fully custom wake word** (a phrase you choose, in your own model)? That's a
     separate, one-time job done on a **PC or Google Colab — not the Pi** — that produces a small `.onnx`.
     Follow the beginner-friendly guide **[Training your own wake word](opnewakeword/openwake-word.md)** —
     it explains the tools it uses (openWakeWord, the Jupyter notebook app, what "cells" are and how to run
     them) from scratch. Then drop the new `.onnx` in `models/` and point `ROBOT_WAKEWORD_MODEL` at it (or
     overwrite `hey_richard.onnx` to keep it the default).
   - Won't wake, or wakes inconsistently? Usually it's **mic capture gain** (too quiet → low scores; or so
     high it **clips** → scores collapse to ~0) or the threshold. The brain logs `best score`/`loudest`
     every ~15 s to tune by — set the gain with `amixer -c <card> sset Mic 75%` (aim `loudest` mid-scale,
     not near 32767). Full procedure: the **[wake-word guide's tuning list](opnewakeword/openwake-word.md)**.
   - False-firing? Raise `ROBOT_WAKEWORD_TRIGGER_LEVEL` to 2 (or `ROBOT_WAKEWORD_THRESHOLD`).
   - **Wake acknowledgement:** every detected *"hey jarvis"* plays a **random** spoken clip from
     `sounds/wake_word_ack/` ("Yes?/Hi") as the face switches to *listening* — an audible + visual "go
     ahead"; then a clip from `sounds/ack_sounds/` ("Got it") fires once your spoken command is accepted.
     The spoken earcons are **generated on your PC** with Piper and committed (see
     [`sounds/README.md`](../sounds/README.md)); the Pi just plays whatever WAVs are in `sounds/`.
     Model warmup is already on (`ROBOT_WARMUP=1`) so the first command isn't a cold load.

> 🔊 **Audio devices — and the card-numbering trap.** Four things route audio, and **plugging in a USB
> speaker renumbers the ALSA cards** (they also shuffle on reboot) — so address everything **by name**,
> not by card number:
> - **Voice (Piper TTS)** → the **ALSA default** output (no per-device knob in the code). To hear the voice
>   on a USB speaker, that speaker must be the **default** (`~/.asoundrc`, below).
> - **Earcons** → `ROBOT_SOUNDS_DEVICE` (empty = the default too).
> - **Command capture** → `ROBOT_MIC` (arecord).  •  **Wake-word mic** → `ROBOT_WAKEWORD_DEVICE` (PortAudio
>   — the *same* mic, addressed a second way).
>
> **The easy way — one command:** **`./scripts/setup_audio.sh`**. It finds your USB *playback* card, makes
> it ALSA's **`default`** output (writes `~/.asoundrc`), sets the volume + persists it, and prints the
> matching `ROBOT_*` values to paste below. `install_stack.sh` already runs it once; re-run it whenever you
> change speakers (pass a card id if you have more than one: `./scripts/setup_audio.sh <id>`).
> `scripts/cleanup_pi.sh` removes the `~/.asoundrc` it writes (and restores any backup).
>
> **What it does under the hood (or do it by hand).** `~/.asoundrc` is **ALSA's per-user config file**; this
> block makes the system **`default`** device your speaker — what every program (including the robot's
> `aplay`) plays to when not told otherwise, so it's how the **voice** reaches the USB speaker. Get the id
> from `aplay -l` (the word after `card N:`):
> ```bash
> SPK=UACDemoV10                 # speaker id (aplay -l);  mic id (arecord -l), e.g. 'Device'
> cat > ~/.asoundrc <<EOF        # speaker = default -> the VOICE comes out it
> pcm.!default { type plug; slave.pcm "hw:CARD=$SPK,DEV=0" }
> ctl.!default { type hw; card $SPK }
> EOF
> amixer -c "$SPK" sset PCM 100% && sudo alsactl store   # volume + persist (USB audio resets on reboot; 80% is ~-6dB -- use 100% if the voice is too quiet)
> ```
> **Any USB speaker works here** — it's purely this config, no code change, so you can swap freely. A
> **USB-powered** one (e.g. the **Naciffo 2**) needs no separate supply — handy on the battery robot, and
> it's plug-and-play with no 3.5 mm jack. One gotcha: tiny USB speakers often expose **no volume control**
> (`amixer -c "$SPK" scontrols` lists nothing, and the listing may literally say "no volume control") —
> then **skip the `amixer` line**; level is fixed in hardware (set volume at the source, or add an ALSA
> `softvol` plugin in `~/.asoundrc` if you must attenuate).
> Then in `config/robot.env`, all **by name** so a reboot/replug never breaks them:
> ```
> ROBOT_MIC=plughw:CARD=<mic-id>,DEV=0                            # command capture
> ROBOT_WAKEWORD_DEVICE=<unique bit of the mic name, e.g. PnP>    # wake mic (sounddevice name-match)
> ROBOT_SOUNDS_DEVICE=plughw:CARD=$SPK,DEV=0                      # earcons
> ```
> Test: `speaker-test -D "plughw:CARD=$SPK,DEV=0" -t wav -l 1`, then `aplay /tmp/test.wav` (via the
> default) should hit the speaker too. The half-duplex `audio_lock` already stops the mic recording the
> speaker's own output, so a plain mic + speaker pair is fine.

> 💬 **It also chats and remembers (works in dry-run too).** Beyond driving: ask a question or say
> hello → it just **answers in words** and stays put (`SAY`). Say *"this is my wife Shani"* → it
> **photographs** what the camera sees and stores **name + relationship + a VLM description** in SQLite
> (`~/.localairobot/`), then feeds known people/things back into the model each turn (a lightweight RAG).
> You need a working speaker to *hear* the replies; the console prints them either way.

With the brain proven, wire the peripherals one at a time (Phases 3–6) — all still on the bench with the
ESC (Electronic Speed Controller)/motor disconnected. **First read Phase 2** so you don't bridge the power domains.

---

## Phase 2 — The two power domains (read before you wire)

**Never bridge the two batteries.** But **do** tie all the *grounds* together, or the PWM signals from
the Pi won't register at the ESC (Electronic Speed Controller)/servo.

![Two isolated power domains: a 2S 7.4V LiPo powers the ESC (Electronic Speed Controller) and drive motor (motor domain), while a UPS HAT - or the official 27W PSU for bench bring-up - powers the Raspberry Pi 5 with its mandatory Active Cooler (brain domain); the Pi also drives the Pi Camera V2 NoIR over CSI, the USB-C face screen, and three 3.3V HC-SR04P sensors (front/left/right). The PCA9685 takes 3.3V logic from the Pi and its V+ rail from the ESC (Electronic Speed Controller) 6V BEC. The two battery positives are never bridged, but all grounds are tied common across Pi, PCA9685 and ESC (Electronic Speed Controller)](images/assembly.png)

For the full picture — where the two domains *legitimately* meet (shared ground, plus the ESC (Electronic Speed Controller)'s **6 V
BEC** feeding the PCA9685 V+ rail and the steering servo), and where the sensors/camera/face sit:

![Detailed power domains and wiring: 2S 7.4V LiPo -> ESC (Electronic Speed Controller) -> drive motor, with the ESC (Electronic Speed Controller) 6V/3A BEC feeding the PCA9685 V+ rail and the DS3225 steering servo (servo power stays inside the LiPo/motor domain); the UPS HAT - or the 27W PSU on the bench - powers the Raspberry Pi 5 + Active Cooler, which provides 3.3V logic + I2C to the PCA9685, 3.3V + TRIG/ECHO to three HC-SR04P sensors (front 23/24, left 5/6, right 20/21), a CSI ribbon to the Pi Camera V2 NoIR (via the Pi-5 22-to-15 adapter), and USB-C to the face screen; every device shares a common ground bus, while the two battery positives are never bridged](images/power-domains.png)

- ✅ Match the LiPo plug (XT60 / T-plug / Deans) to the ESC (Electronic Speed Controller); don't solder if you can pick the right one.
- ✅ The Pi 5 **Active Cooler is mandatory** — sustained LLM/vision inference thermal-throttles without it.
- ✅ A **LiPo balance charger** is required for the 2S pack; never charge LiPo on a dumb charger.
- 🔴 Put a **physical kill switch / unplug loop** on the LiPo→ESC line within reach. With no hardware
  watchdog (the Pico is gone), this is your last-resort stop if the software dies. See the safety note below.

---

## Phase 3 — Wire the PWM bridge (PCA9685)

Still on the bench, **ESC (Electronic Speed Controller)/motor not connected.** The Pi 5 cannot cleanly
bit-bang servo signals while running an LLM, so a PCA9685 generates them over I2C. **In this phase wire
only the Pi↔PCA9685 logic side** — four jumpers: `VCC`→Pi **3.3V**, `SDA`→**GPIO2**, `SCL`→**GPIO3**,
`GND`→Pi **GND**. The ESC and servo outputs come later (Phase 7).

> 📐 **Full pin-by-pin wiring lives in one place:** board orientation, which output column is ch0 (ESC) /
> ch1 (servo), the ESC 3-wire + servo leads, the V+ rail fed by the ESC's 6V BEC, the exact Pi-header
> pins, and the `i2cdetect` walkthrough — see **[PWM / ESC / servo wiring](wiring/pwm-esc-servo-wiring.md)**.
> This phase is just its logic-side connection; only channels 0 and 1 are used (2–15 stay free).

The DS3225 is a hungry servo (stall ~2.5–3A). The QuicRun's 6V/3A BEC is enough for normal steering; if
you see brownouts under hard steering load, add a separate servo BEC on the V+ rail.

**Check:** `i2cdetect -y 1` shows the PCA9685 at **0x40** (full grid walkthrough in the wiring guide).

---

## Phase 4 — Wire the distance sensors (3× HC-SR04P, semi-hexagonal front array)

Three **HC-SR04P** (the 3.3 V variant) in a shallow semi-hex across the front: one facing **forward**,
one angled **~45–60° left**, one **~45–60° right**. Splaying them reduces beam-cone overlap. The forward
one drives the FORWARD-cut safety gate; the side ones keep the explore behaviour from steering into a
near wall. They wire **straight to the Pi's GPIO** — powered from **3.3 V** so every ECHO is Pi-safe
(no level shifter). Pins (BCM): **front TRIG/ECHO 23/24, left 5/6, right 20/21**.

> 📐 **Full pin-by-pin wiring** — every sensor's VCC/GND/TRIG/ECHO with the **physical** header pins, the
> Pi-pinout diagram, the bare-5 V-HC-SR04 divider fallback, and the two by-design sensing limits — is the
> dedicated guide **[ultrasonic sensor wiring](wiring/ultrasonic-sensors-wiring.md)**.

Pins are set in `config/robot.env` (`ROBOT_TRIG_PIN`/`ROBOT_ECHO_PIN` and `ROBOT_LEFT_*`/`ROBOT_RIGHT_*`).
These BCM pins avoid I2C (2/3), SPI0 (7–11) and UART (14/15); alternates if you need them: `16/26`,
`22/27`, `19/25`. gpiozero serialises the three pings internally (so they don't hear each other), giving
~4–6 Hz per sensor — the forward gate reads the latest cached value, never blocking the control loop.

**Check:** read all three live with **`./scripts/test_sensors.sh`** — cover each beam in turn and watch
*its* number drop. This is how you prove front/left/right aren't swapped; the brain itself only logs the
**front** beam (on a gate trip).

**✅ Phase 4 is done when:**
- `./scripts/test_sensors.sh` prints **three** numbers that track the real distances (roughly your hand /
  wall positions) — not blank, not stuck at `400.0 cm`, and with **no** `no echo received` warnings.
- Covering each beam in turn drops **only that sensor's** number (so front/left/right are **not swapped**),
  and it springs back when you uncover it.
- *(Optional, once the chassis is built in Phase 7)* `./scripts/run_diagnostics.sh` shows ✓ for all three.

`test_sensors.sh` covers the **wiring** (all three read + correct mapping) — that's all of Phase 4. It does
**not** exercise the in-brain obstacle *gate*; that's verified later in
[Phase 8](#phase-8--live-calibration--bring-up-wheels-off-the-ground) step 4 (say *"go forward"* + a hand
~20 cm ahead of the **front** beam → the motor cuts and it says *"Obstacle ahead. Stopping."*).

---

## Phase 5 — Wire the camera (IMX219: Pi Camera V2 NoIR **or** Arducam IMX219 8MP)

Visual input for the **vision model** (`moondream` by default, set via `ROBOT_VISION_MODEL`; the text
brain `gemma4:e4b-it-qat` is multimodal too and can take over if you empty that var). It is on the **CSI
ribbon**, not GPIO/I2C, so it doesn't conflict with anything else.

- **Any IMX219 module works — pick whichever you have.** A **Pi Camera V2 NoIR** and an **Arducam IMX219
  8MP** are the *same IMX219 sensor*; libcamera/picamera2 drive them identically, so swapping is **zero
  code change**. NoIR = no IR-cut filter (reddish daylight, but sees with IR light); a standard module
  gives natural daylight colour.
- ⚠️ **The Pi 5 needs the 22→15 cable.** The Pi 5 CSI/DSI ports are the narrow **22-pin, 0.5 mm** type; a
  module's **15-pin, 1 mm** cable **will not fit** directly. Get a **22-way-to-15-way adapter FPC cable**
  (the **Arducam kit often bundles this as its "Pi Zero cable"** — same 22→15 wiring) and run it from the
  camera to either CAM/DISP port (contacts the correct way, latch closed).
- Install: `python3-picamera2` comes from **apt** (the installer does this) and the venv is created with
  `--system-site-packages` so it can import it. Confirm the camera with `rpicam-hello --list-cameras`,
  then grab a test frame headless with `rpicam-jpeg -n -o /tmp/test.jpg`. *(If the brain logs `camera
  unavailable (list index out of range)`, libcamera saw **no** camera — reseat the ribbon and re-check
  `rpicam-hello --list-cameras`. The brain runs fine without it; vision just reports "no frame".)* If
  `rpicam-hello` itself sees nothing and auto-detect isn't catching the module, add an explicit overlay
  for the port it's on — `dtoverlay=imx219,cam0` (or `cam1`) in `/boot/firmware/config.txt` — and reboot.
- **A NoIR module has no IR-cut filter:** images look reddish/magenta in daylight, and it sees nothing in
  the dark without an **IR illuminator** (850/940 nm). Fine for lit rooms; add IR LEDs for night use. A
  *standard* (non-NoIR) module skips this — natural colour, but still blind in the dark.
- Tuning: `ROBOT_CAMERA_ENABLE`, `ROBOT_CAMERA_SIZE` (square letterbox before the LLM), `ROBOT_CAMERA_WARMUP_S`.
  Vision is **CPU-only and slow** on the Pi 5 (`ROBOT_VISION_TIMEOUT_S` defaults to 600 s) — the car is
  held at neutral during each look.

---

## Phase 6 — Wire the face panel (USB-C serial **or** DSI/HDMI — pick one)

The robot's face. `face.py` has three backends and `ROBOT_FACE_BACKEND=auto` tries them in order, so it
adapts to whatever panel you fit:

- **`turing`** — a small **USB-C "secondary screen"** (e.g. the 3.5" TURZX/"NO AIDA64" 320×480). On Linux
  these are **not** a framebuffer; they enumerate as a **USB-serial** device, driven by the
  `turing-smart-screen-python` library (cloned to `~/turing-smart-screen-python` by the installer).
- **`framebuffer`** — any **DSI or HDMI** display, e.g. a **Freenove 5" 800×480 DSI touchscreen**. `face.py`
  writes the frame straight to `/dev/fb0` (PIL → the framebuffer; no X, no pygame).
- **`simulated`** — no panel; just logs `FACE -> <state>`. A missing face **never blocks driving**.

**If you're using the USB-C serial panel (`turing`):**
- Connect it to a Pi USB port with a **data** USB-C cable; it appears as `/dev/ttyACM0` (CH340, USB id
  `1a86:5722`). The installer adds you to the **`dialout`** group — re-login after.
- Confirm it enumerated: `lsusb | grep -i 1a86` and `ls /dev/ttyACM*` (expect `/dev/ttyACM0`). Run the
  library's `simple-program.py` once (`cd ~/turing-smart-screen-python`) to see which revision drives your
  panel cleanly, then set `config/robot.env` `ROBOT_FACE_REVISION=A` (Turing/UsbPCMonitor 320×480) or `B`
  (XuanFang) and re-`source` the file.

**If you're using a DSI/HDMI panel (`framebuffer`) — e.g. the Freenove 5" 800×480 DSI touchscreen:**
- Plug the panel's **DSI ribbon** into a Pi 5 **CAM/DISP** port (same narrow 22-pin connector as the
  camera; mind which port and the contact direction). A "driver-free" DSI panel shows up after boot as
  `/dev/fb0`; some need a `dtoverlay` line in `/boot/firmware/config.txt` per the panel's instructions —
  check `ls /dev/fb0` exists before expecting a face.
- Set `ROBOT_FACE_BACKEND=framebuffer` (or leave `auto` — it falls through to it when no USB-C panel is
  found) and re-`source`. The backend **auto-reads the panel's size and colour depth** from the kernel, so
  800×480 and the Pi 5 KMS **32-bit** framebuffer are handled automatically — `ROBOT_FACE_WIDTH/HEIGHT` are
  ignored here. The **capacitive touch layer is unused** (the face is display-only; harmless).
- Best on **Pi OS Lite / the console**: with a full desktop running, the compositor owns the screen and
  may draw over the face. If red/blue ever look swapped, that's the one panel-specific quirk — tell me and
  it's a one-line flip in `_FramebufferBackend.show`.

It's **fully optional and fail-safe**: if no panel is found, `auto` ends at `simulated` and the robot runs
exactly the same, just logging expressions.
- **Custom face frames:** drop PNG frames in `brain/faces/<state>/NN.png` (states
  `idle/listening/thinking/talking/exploring/happy/confused/alarmed/warmup/capturing`). A state with
  **≥2 frames animates** on a background thread (`talking` is fast with a random-frame mouth); 1 frame
  is a static still; **none** falls back to the built-in procedural face — so with no `faces/` dir the
  face behaves exactly as before. Tune `ROBOT_FACE_FPS` / `ROBOT_FACE_FRAMES_DIR`; the USB-C panel's
  serial link caps the effective frame rate. Inspect frames in dry-run with `ROBOT_FACE_DUMP=<dir>`.
  You design these (and the spoken earcons) **on your PC** — full process in
  [`brain/faces/README.md`](../brain/faces/README.md) and [`sounds/README.md`](../sounds/README.md).
- Mount it forward-facing as the robot's "face"; dim it with `ROBOT_FACE_BRIGHTNESS` to save power.

**Now run the brain LIVE on the bench (still no chassis/motor).** With the PCA9685 + 3 sensors + camera +
face wired and the ESC (Electronic Speed Controller)/motor *not connected*, the same `robot.hw` logs now reflect real I2C/GPIO:

```bash
i2cdetect -y 1                  # PCA9685 at 0x40
rpicam-hello --list-cameras     # IMX219 detected (Pi Camera V2 NoIR or Arducam IMX219)
lsusb | grep -i 1a86            # USB-C face panel (turing backend) ...
ls /dev/fb0                     # ... OR a DSI/HDMI panel (framebuffer backend), if you went that route
./scripts/test_sensors.sh       # read the 3 sensors live before trusting the gate (Ctrl-C to stop)
./scripts/run_me.sh --live      # foreground, real hardware (ESC/motor still unplugged)
```

Only after this live bench pass do you build the chassis and connect the ESC (Electronic Speed Controller)/motor (Phase 7).

---

## Phase 7 — Mechanical build (chassis + mount everything)

This is where the **ESC (Electronic Speed Controller), motor, and LiPo finally get connected** — until now nothing could move.

1. Assemble the 1/10 roller chassis (or start from your built RTR): drivetrain, portal axles, links,
   shocks, 1.9" wheels. Mount the **540 35T motor** into the transmission; mesh the pinion/spur with a
   sliver of slack (a strip of paper between the gears while you tighten sets the backlash).
2. Mount the **steering servo** to the front axle servo mount; connect the steering link. Leave the
   horn off until you've found the servo's center in calibration, then attach it pointing straight.
3. Build the brain stack: Pi 5 + active cooler (+ UPS HAT later — Phase 9). Mount on a
   3D-printed/cut deck on the frame rails (the SCX10 313 mm form factor has many free community trays).
   Keep fan airflow clear.
4. Mount the **ESC (Electronic Speed Controller)**, **PCA9685**, and **LiPo** on the chassis; route wiring clear of the driveshafts
   and steering links. Keep the mic away from the cooler fan to reduce recorded noise. **Now make the
   motor-domain connections you deferred in Phase 3** — ESC → motor, ESC 3-wire → PCA9685 **ch0**,
   **steering servo → ch1**, and **LiPo → ESC (Electronic Speed Controller) through your physical kill
   switch** — done pin-by-pin in the **[PWM / ESC / servo wiring guide](wiring/pwm-esc-servo-wiring.md)**.
   Keep all grounds common (Pi ⟷ PCA9685 ⟷ ESC (Electronic Speed Controller)); never bridge the battery
   positives (Phase 2).
5. Mount the **3-sensor plate** across the front (forward + ~45–60° left/right), clear of the wheels and
   roughly level. Mount the **camera** forward-facing near the sensors, and the **face panel** where you
   want the robot to "look at" you. Route the CSI ribbon and USB cables away from the driveshafts.

---

## Phase 8 — Live calibration & bring-up (wheels OFF the ground)

Phases 1–6 already verified the brain, the I2C bus, and the three sensors; this phase powers the **ESC (Electronic Speed Controller)
for the first time**, so keep the **wheels OFF the ground until the very end** and a **physical kill
switch in hand**. Every `ROBOT_*` value below is a line in `config/robot.env`: edit it, then re-run
`./scripts/run_me.sh --live` (foreground; Ctrl-C to stop between edits — it reloads the config each
launch) — repeat until it's dialled in. If a board isn't found, the brain now **logs which device failed
and runs degraded** (motors off) instead of crashing — read that line, fix the wiring, and relaunch.

> 🔧 **First, verify the wiring (live self-test).** Before the throttle steps below, run
> **`./scripts/run_diagnostics.sh`** — it checks the ESC/PWM board, motor, servo, 3 sensors, camera,
> speaker, mic and face and points to the exact fix for anything wrong (wheels OFF the ground; the motor
> spin is gated). Full reference: [docs/diagnostics.md](diagnostics.md).

> 🪑 **Test voice/vision without driving.** Set **`ROBOT_MOTORS_OFF=1`** (keep `ROBOT_DRY_RUN=0`) to run
> fully live — wake word, camera, sensors, face — with the **ESC/steering getting no signal** (wheels
> won't move). Ideal for desk-testing the brain on the assembled car before the throttle steps below.

1. **ESC (Electronic Speed Controller) arm:** with the LiPo connected, the brain holds neutral for `ROBOT_ESC_ARM_SECS` on start — the
   ESC (Electronic Speed Controller) should beep its arming tones. No arm = check signal/GND to PCA9685 ch0 and the BEC on V+.
2. **Steering:** run the brain, say *"center" / "left" / "right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT`
   so the wheels center true and turn fully without the servo buzzing (binding = back the angle off).
3. **Throttle direction:** say *"go forward"* slowly. Backwards? Set `ROBOT_THROTTLE_INVERT=1` (or swap
   the motor wires). Reverse may need the ESC (Electronic Speed Controller) put in a forward/reverse mode (see the ESC (Electronic Speed Controller) manual).
4. **Obstacle gate:** *"go forward"* + hand ~20 cm ahead of the forward sensor → motor cuts, robot says
   *"Obstacle ahead. Stopping."*
5. **Camera/explore (optional):** `rpicam-hello` ok, then say *"look around"* — it should describe what
   it sees. Benchmark one *"find the …"* on your Pi and tune `ROBOT_EXPLORE_*` to the measured latency.
6. **On the floor:** only now, at low `ROBOT_MAX_THROTTLE`, with the kill switch in hand.

Full calibration reference (every `ROBOT_*` knob) is in the [README](../README.md).

---

## Phase 9 — Add the UPS HAT (later, for untethered running)

Build and test on the 27 W PSU first; add the UPS once everything works.

- Use a **pogo-pin, bottom-mount** UPS HAT (e.g. Geekworm X1200/X1202, Waveshare UPS HAT (B)/(E)). These
  power the Pi via spring pins on the **underside** and **do not consume the 40-pin header**, so the
  PCA9685, the three sensors, and the camera/face all keep working.
- Stack, top → bottom: **Active Cooler (on the SoC) → Raspberry Pi 5 → UPS HAT (underneath)**.
- The UPS fuel gauge usually sits at I2C **`0x36`**, which doesn't clash with the PCA9685 at `0x40` —
  after fitting it, `i2cdetect -y 1` should show both. Follow the HAT's documented charge/power-up order.
- ⚠️ If your HAT is the older **GPIO-header** style (not pogo-pin), it will occupy the header the
  sensors/PCA9685 need — use stacking headers or pick a pogo-pin model.

---

## Appendix — Command cheat-sheet & safety

### Command cheat-sheet

**How to run these.** Every command below runs in a **bash shell on the Pi** (over SSH) — *not* on your
laptop and *not* in PowerShell. Paste each line at the Pi's `$` prompt; lines starting with `#` are
comments, so you don't type them.

```bash
# Mic device id  →  put the plughw string in config/robot.env (ROBOT_MIC)
arecord -l

# Audio sanity check (same 16 kHz mono S16_LE the app uses); -V mono draws a live level bar as you talk
arecord -D plughw:2,0 -d 3 -f S16_LE -r 16000 -c 1 -V mono /tmp/test.wav && aplay /tmp/test.wav

# Confirm the bus + peripherals
i2cdetect -y 1                  # PCA9685 at 0x40 (and 0x36 once the UPS is fitted)
rpicam-hello --list-cameras     # IMX219 NoIR (after the 22→15 adapter cable)
lsusb | grep -i 1a86            # USB-C face panel

# Read all three HC-SR04P sensors live -- cover each beam in turn and watch ITS number drop
# (confirms front/left/right aren't swapped). Uses the pins from config/robot.env; Ctrl-C to stop.
./scripts/test_sensors.sh

# Full live wiring self-test: green/red per peripheral + fix pointers (run before the first drive).
# Wheels OFF the ground -- the motor spin is gated behind a confirmation. See docs/diagnostics.md.
./scripts/run_diagnostics.sh
```

### Safety note — software failsafe, not hardware

`driver.py` runs a control thread that forces the ESC (Electronic Speed Controller) to **neutral** on STOP, on error, on a forward
obstacle, and when a move's `duration_ms` deadline passes; the ESC (Electronic Speed Controller) is armed at neutral on boot. The
explore/find behaviour only ever calls into this same gated path (it never drives PWM directly), and it
holds the car at neutral during the slow vision step. This replaces the old Pico hardware watchdog **in
software only** — if the whole Python process is killed, the PCA9685 keeps emitting the last pulse and
nothing in hardware stops the motor. The **physical kill switch on the LiPo is your real safety net.**
Keep `ROBOT_MAX_THROTTLE` low and durations short.

---

## Appendix — Run it day-to-day (`run_me.sh` / `shutdown.sh`)

Two wrappers in `scripts/` save you the `set -a; source config/robot.env; set +a` dance (they source it
for you, and silently skip if it's missing). Make them executable once:

```bash
chmod +x scripts/*.sh
```

| Command | What it does |
|---|---|
| `./scripts/run_me.sh` | **The launcher.** Mode from `ROBOT_DRY_RUN` (override with `--dry` / `--live`). Runs in the **foreground**, interactive: **`q` = quit**, **Ctrl-C = stop** (neutralises the motor). In **`--live`** the **wake word is the only trigger**; in **`--dry`** ENTER = talk and `r` = simulate a wake (bench testing, no mic). `--dry` drives nothing; `--live` uses real hardware. |
| `./scripts/run_me.sh --detach` | Run **live in the background** (survives SSH logout), listening for the wake word. For running untethered *without* the boot service. Stop it with `shutdown.sh`. |
| `./scripts/run_me.sh --reload` | **Re-apply an edited `config/robot.env`** to a running robot — restarts the boot service if installed, else the `--detach` instance. |
| `./scripts/shutdown.sh` | **Stop** a detached brain (SIGINT first → neutralises the motor). The Pi stays on; add **`--poweroff`** to also halt the Pi (your SSH session drops). (Foreground run? just Ctrl-C. Boot service? `sudo systemctl stop robot.service`.) |
| `tail -f ~/.localairobot/robot.log` | Watch a **detached** brain's output (PID in `~/.localairobot/robot.pid`). |

> 🔴 **On real wheels:** keep the **physical kill switch in reach** and bench everything with `--dry`
> first. **Live is wake-word-only** (ENTER works only in `--dry`); with no working wake model the brain
> exits at startup (set the wake word up in Phase 1 step 8) — this is true for foreground `--live` *and*
> the headless modes (`--detach` / the boot service), which have no terminal anyway. For first live runs,
> foreground `--live` is safest: you watch every command in the log and `q`/Ctrl-C stops instantly.

**Auto-loading the config.** `install_stack.sh` already adds a line to `~/.bashrc` so every new SSH
session loads `config/robot.env` for you (and `run_me.sh`/`shutdown.sh` source it themselves). To re-add
it by hand if needed:

```bash
echo '[ -f "$HOME/LocalAiRobot/config/robot.env" ] && { set -a; . "$HOME/LocalAiRobot/config/robot.env"; set +a; }' >> ~/.bashrc
```

**Start the brain automatically on boot (no SSH at all).** When you're ready to run untethered, install
the systemd service — it auto-starts the brain on every boot and relaunches it if it crashes:

```bash
./scripts/run_me.sh --install-boot     # fills in your user + repo path, installs + enables robot.service
sudo systemctl start robot.service     # (optional) start it now without rebooting
journalctl -u robot.service -f         # watch its log
./scripts/run_me.sh --disable-boot     # stop auto-starting on boot
```

Set `ROBOT_DRY_RUN=0` in `config/robot.env` first — a service has no terminal, so the **wake word is the
only way to talk to it**; without a working wake model the service starts, finds no way to be triggered,
and exits (set the wake word up in Phase 1 step 8). The same 🔴 safety note applies: kill switch in reach.

---

## Appendix — Resetting the Pi (`cleanup_pi.sh`)

Decided this build isn't for you, or just want your ~3.5 GB back? Your clone already includes
`scripts/cleanup_pi.sh`, which **undoes `install_stack.sh`** — it removes the downloaded AI stack,
models, venv and runtime data, returning the Pi toward its pre-install state.

It's built to be safe: it runs in three steps — **(1)** a *dry run* that lists exactly what exists and
would be removed, **(2)** a `Shall I proceed? [y/N]` prompt, **(3)** deletion **only** if you type
`y`/`yes`. Run it from inside your clone, as your normal user (not with `sudo`):

```bash
cd ~/LocalAiRobot
./scripts/cleanup_pi.sh -n        # DRY RUN: just lists what exists and would go (changes nothing)
./scripts/cleanup_pi.sh           # dry run -> "Shall I proceed?" -> deletes only if you say yes
./scripts/cleanup_pi.sh --deep    # ALSO apt-remove the installer's packages + disable I2C
./scripts/cleanup_pi.sh --repo    # ALSO delete the ~/LocalAiRobot clone itself (runs last)
./scripts/cleanup_pi.sh --models gemma3:4b   # remove ONE Ollama model only (to switch the local model)
sudo reboot                       # finish up: clears /tmp and applies the I2C change
```

**Removes by default** (only the items that still exist — the dry run shows you first): `~/whisper.cpp`,
`~/piper`, `~/turing-smart-screen-python`, `~/openwakeword`, Ollama + all pulled models (binary, libs and
model store), `brain/.venv` (which also holds the openWakeWord models), `~/.localairobot` (scene memory,
taught photos + the run log/PID), `/tmp/test.*`, an installed `robot.service`, the `config/robot.env`
auto-load line the installer added to `~/.bashrc`, and the `~/.asoundrc` written by `setup_audio.sh`
(restoring any `~/.asoundrc.bak` it had backed up).

**Good to know:** `--deep` also apt-removes packages the installer added (cmake, build-essential,
i2c-tools, …); a few may have shipped with your OS image, so it can take slightly more than the install
added — skip `--deep` if you just want the disk space back. It leaves `git`/`curl`/`alsa-utils` and your
group memberships in place. For a guaranteed-pristine machine, re-flashing the SD card is the surest reset.
