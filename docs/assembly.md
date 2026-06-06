# Assembly & Wiring Notes

Build the LocalAiRobot RC crawler **one phase at a time** — flash the brain, bring it up on the bench,
wire each peripheral with its own check, build the chassis, then calibrate live. Pairs with the
[README](../README.md) (parts list + tuning). Capture your own photos as you go.

![Wiring at a glance: a 2S 7.4V LiPo feeds the ESC (with 6V BEC), which drives the motor and sends ch0 signal + V+ rail + GND to the PCA9685 PWM bridge (0x40); the PCA9685 drives the DS3225 steering servo on ch1 and connects to the Raspberry Pi 5 (+ Active Cooler) via 3.3V VCC, SDA (GPIO2), SCL (GPIO3) and GND; three HC-SR04P sensors (front TRIG23/ECHO24, left 5/6, right 20/21), the Pi Camera V2 NoIR over CSI, and the 3.5" USB-C face screen are all Pi-side; the Pi is powered by a UPS HAT or the 27W bench PSU. The two battery positives are never bridged, but all grounds are tied common.](images/wiring.png)

**Phase map:** **1** flash + brain bring-up · **2** power domains · **3** PCA9685 · **4** sensors ·
**5** camera · **6** face · **7** mechanical build · **8** live calibration · **9** UPS HAT.
Phases 1–6 run **on the bench on the 27 W PSU with no chassis and no motor** — nothing can move until
Phase 7 connects the ESC.

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
   - **Set the username to the account you'll use for everything — this becomes `YOUR_USER`** throughout
     this doc, `config/robot.env`, and `scripts/robot.service` (they hardcode `/home/YOUR_USER/...`, so
     the name **must** match or nothing will find whisper/piper).
   - **Enable SSH**, and set your **Wi-Fi** SSID/password + locale so the Pi joins the network headless.

   Insert the card into the Pi.
3. **Power from the official 27 W USB-C PSU only — do NOT fit the UPS HAT yet.** The 27 W supply
   negotiates 5 V/5 A, so there's no low-power warning and no 600 mA peripheral clamp. Boot and SSH in.
4. **Install the stack and reboot:**
   ```bash
   git clone https://github.com/<you>/LocalAiRobot.git ~/LocalAiRobot
   cd ~/LocalAiRobot && chmod +x scripts/install_stack.sh && ./scripts/install_stack.sh
   sudo reboot
   ```
5. **Plug in the USB mic + speaker, configure `robot.env`, and prove audio works — the dry-run records
   from the *real* mic.** `ROBOT_DRY_RUN=1` only fakes the **motor/sensor** hardware; the brain still
   shells out to `arecord` on your mic and `whisper.cpp` on the clip (see `listen_and_transcribe` in
   `brain/main.py`), so you cannot speak a command until the mic captures cleanly:
   ```bash
   nano config/robot.env             # set YOUR_USER in the whisper/piper paths + ROBOT_MIC
   arecord -l                        # USB mic    -> card,device = plughw:CARD,DEV  (put in ROBOT_MIC)
   aplay -l                          # USB speaker card (TTS playback)
   # capture sanity check (same 16 kHz mono S16_LE the app uses), then play it back:
   set -a; source config/robot.env; set +a
   arecord -D "$ROBOT_MIC" -d 3 -f S16_LE -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav
   ```
   You should hear your own voice played back. If `arecord` errors or the clip is silent, fix the
   device / `ROBOT_MIC` before continuing — otherwise the next step just prints `listen: ...` and
   never hears you.
6. **Dry-run the brain — no PCA9685, no sensors, no camera, no panel required:**
   ```bash
   set -a; source config/robot.env; set +a
   ROBOT_DRY_RUN=1 brain/.venv/bin/python brain/main.py
   ```
   Press ENTER, speak a command, and read the console. `driver.py` logs exactly what it *would* send to
   the hardware, so you can confirm the model→hardware mapping is correct before anything can move:
   ```
   *** DRY RUN -- no hardware will be driven ***
   robot.hw INFO PCA9685 I2C 0x40 (SDA GPIO2 / SCL GPIO3): ESC ch=0, steer ch=1 | max_throttle=0.35
   robot.hw INFO HC-SR04P x3: front TRIG=GPIO23/ECHO=GPIO24  left TRIG=GPIO5/ECHO=GPIO6  right TRIG=GPIO20/ECHO=GPIO21
   Heard: "go forward and bear left"
   robot.hw INFO I2C->PCA9685 ESC   ch=0 throttle=+0.140 pulse_us=1570
   robot.hw INFO I2C->PCA9685 SERVO ch=1 steer=LEFT   angle=55.0deg pulse_us=1111
   ```
   - `ROBOT_FAKE_DISTANCE_CM=20` → say "go forward" → the forward gate trips in the log.
   - `ROBOT_FAKE_VISION=sofa:left:near:0.9` → say "find the sofa" → the whole explore loop runs, no camera.
7. **Confirm health under load** (run an Ollama prompt in another shell and watch):
   ```bash
   vcgencmd get_throttled     # expect 0x0
   watch -n1 vcgencmd measure_temp   # stays < 80 °C with the cooler
   ```
8. **Optional — wake word, audio cues & model warmup (all default-safe).** By default control is ENTER
   push-to-talk and these change nothing. To try them on the bench:
   - **Wake word "Richard"** — `ROBOT_WAKEWORD_ENABLE=1`. In dry-run there is no mic, so type `r` then
     ENTER to simulate the wake (or set `ROBOT_FAKE_WAKE=1` to auto-fire once). Live, it listens via
     sounddevice on the default input (`ROBOT_WAKEWORD_DEVICE` to override); point
     `ROBOT_WAKEWORD_MODEL` at any stock openWakeWord `.onnx` to prove the pipeline, then train a real
     "Richard" model later. ENTER/`q` still work over SSH. **This is what makes `scripts/robot.service`
     usable**, since systemd has no stdin for the ENTER prompt.
   - **Audio cues** — drop short `.wav` files into `sounds/{greeting,ack,thinking,error}_sounds/`. In
     dry-run they only log `SOUND -> …`; live they play via `aplay` on `ROBOT_SOUNDS_DEVICE` (the USB
     *speaker* card — separate from the mic). Empty folders are silent.
   - **Model warmup** — `ROBOT_WARMUP=1` primes the model at boot so the first command isn't a cold
     load; `ROBOT_OLLAMA_KEEPALIVE` keeps it resident between commands.

With the brain proven, wire the peripherals one at a time (Phases 3–6) — all still on the bench with the
ESC/motor disconnected. **First read Phase 2** so you don't bridge the power domains.

---

## Phase 2 — The two power domains (read before you wire)

**Never bridge the two batteries.** But **do** tie all the *grounds* together, or the PWM signals from
the Pi won't register at the ESC/servo.

![Two isolated power domains: a 2S 7.4V LiPo powers the ESC and drive motor (motor domain), while a UPS HAT - or the official 27W PSU for bench bring-up - powers the Raspberry Pi 5 with its mandatory Active Cooler (brain domain); the Pi also drives the Pi Camera V2 NoIR over CSI, the USB-C face screen, and three 3.3V HC-SR04P sensors (front/left/right). The PCA9685 takes 3.3V logic from the Pi and its V+ rail from the ESC 6V BEC. The two battery positives are never bridged, but all grounds are tied common across Pi, PCA9685 and ESC](images/assembly.png)

For the full picture — where the two domains *legitimately* meet (shared ground, plus the ESC's **6 V
BEC** feeding the PCA9685 V+ rail and the steering servo), and where the sensors/camera/face sit:

![Detailed power domains and wiring: 2S 7.4V LiPo -> ESC -> drive motor, with the ESC 6V/3A BEC feeding the PCA9685 V+ rail and the DS3225 steering servo (servo power stays inside the LiPo/motor domain); the UPS HAT - or the 27W PSU on the bench - powers the Raspberry Pi 5 + Active Cooler, which provides 3.3V logic + I2C to the PCA9685, 3.3V + TRIG/ECHO to three HC-SR04P sensors (front 23/24, left 5/6, right 20/21), a CSI ribbon to the Pi Camera V2 NoIR (via the Pi-5 22-to-15 adapter), and USB-C to the face screen; every device shares a common ground bus, while the two battery positives are never bridged](images/power-domains.png)

- ✅ Match the LiPo plug (XT60 / T-plug / Deans) to the ESC; don't solder if you can pick the right one.
- ✅ The Pi 5 **Active Cooler is mandatory** — sustained LLM/vision inference thermal-throttles without it.
- ✅ A **LiPo balance charger** is required for the 2S pack; never charge LiPo on a dumb charger.
- 🔴 Put a **physical kill switch / unplug loop** on the LiPo→ESC line within reach. With no hardware
  watchdog (the Pico is gone), this is your last-resort stop if the software dies. See the safety note below.

---

## Phase 3 — Wire the PWM bridge (PCA9685)

Still on the bench, **ESC/motor not connected.** The Pi 5 cannot cleanly bit-bang servo signals while
running an LLM, so a PCA9685 generates them over I2C. Wire it like this:

| PCA9685 pin | Connect to | Why |
|---|---|---|
| `VCC` (logic) | Pi **3.3V** | chip logic + I2C level (matches the Pi's 3.3V I2C) |
| `SDA` | Pi **GPIO2 / SDA** | I2C data |
| `SCL` | Pi **GPIO3 / SCL** | I2C clock |
| `GND` | Pi **GND** | common ground (also shared with the ESC) |
| `V+` (servo rail) | **ESC 6V BEC** (the red wire of the ESC's servo plug) | powers the steering servo — **not** from the Pi |
| Channel 0 (signal/V+/GND) | **ESC signal plug** | throttle PWM to the ESC (+ BEC feeds V+, + GND) |
| Channel 1 (signal/V+/GND) | **steering servo** | steering PWM + 6V power + GND |

Notes:
- The ESC's 3-wire plug (the one that normally goes to an RC receiver's throttle channel) carries
  **signal + 6V BEC + GND**. Signal → PCA9685 ch0 signal; BEC red → PCA9685 V+ rail; GND → PCA9685 GND.
  (You make the ESC connection itself in Phase 7 — for now just wire the Pi↔PCA9685 logic side.)
- Default I2C address is **0x40** (`i2cdetect -y 1`). If you stacked boards/changed solder jumpers,
  set `ROBOT_PCA9685_*`/the driver accordingly. Channels 2–15 stay free.
- The DS3225 is a hungry servo (stall ~2.5–3A). The QuicRun's 6V/3A BEC is enough for normal steering;
  if you see brownouts under hard steering load, add a separate servo BEC on the V+ rail.

**Check:** `i2cdetect -y 1` shows the PCA9685 at **0x40**.

---

## Phase 4 — Wire the distance sensors (3× HC-SR04P, semi-hexagonal front array)

Three **HC-SR04P** (the 3.3 V variant) in a shallow semi-hex across the front: one facing **forward**,
one angled **~45–60° left**, one **~45–60° right**. Splaying them reduces beam-cone overlap. The forward
one drives the FORWARD-cut safety gate; the side ones keep the explore behaviour from steering into a
near wall. Powered from **3.3 V**, every ECHO stays at 3.3 V and wires **straight to GPIO — no divider**.

| Sensor | `VCC` | `GND` | `TRIG` | `ECHO` |
|---|---|---|---|---|
| **front** | Pi 3.3V | Pi GND | **GPIO23** | **GPIO24** |
| **left**  | Pi 3.3V | Pi GND | **GPIO5**  | **GPIO6**  |
| **right** | Pi 3.3V | Pi GND | **GPIO20** | **GPIO21** |

Pins are set in `config/robot.env` (`ROBOT_TRIG_PIN`/`ROBOT_ECHO_PIN` and `ROBOT_LEFT_*`/`ROBOT_RIGHT_*`).
These BCM pins avoid I2C (2/3), SPI0 (7–11) and UART (14/15); alternates if you need them: `16/26`,
`22/27`, `19/25`. gpiozero serialises the three pings internally (so they don't hear each other), giving
~4–6 Hz per sensor — the forward gate reads the latest cached value, never blocking the control loop.

> ⚠️ **Bare HC-SR04 fallback.** A non-P **HC-SR04** wants **5 V** on `VCC` and then drives its **ECHO at
> 5 V** — and the Pi's GPIO is **NOT 5 V-tolerant**. If that's all you have, power `VCC` from Pi **5V** and
> add a divider on each `ECHO` only: **1 kΩ** from ECHO → the GPIO pin, **2 kΩ** from that pin → GND
> (5 V → ~3.3 V). `TRIG` driven by the Pi's 3.3 V output is fine either way.

**Check:** read all three live with the **3-sensor snippet in the Command cheat-sheet** (end of this
doc) — cover each beam in turn and watch *its* number drop. This is how you prove front/left/right
aren't swapped; the brain itself only logs the **front** beam (on a gate trip).

**Two known sensing limits (by design):**
- The forward gate **fails closed on a read error** (a sensor exception blocks FORWARD), but a *dead/
  silent* sensor that saturates at max range reads as "clear" — software can't tell that apart from a
  genuinely open path. Bench-test that covering the forward sensor stops the car before trusting it.
- **REVERSE is unsensed** — there is no rear sensor, so reverse moves are bounded only by the throttle
  cap and the move deadline. Keep `ROBOT_MAX_THROTTLE` low and reverse commands short, or add a rear
  HC-SR04P later.

---

## Phase 5 — Wire the camera (Pi Camera V2 NoIR, IMX219)

Visual input for the multimodal `gemma3:4b`. It is on the **CSI ribbon**, not GPIO/I2C, so it doesn't
conflict with anything else.

- ⚠️ **The Pi 5 needs the adapter cable.** The Pi 5 CSI/DSI ports are the narrow **22-pin, 0.5 mm**
  type; the Camera V2 ships with a **15-pin, 1 mm** cable that **will not fit**. Get a **22-way-to-15-way
  adapter FPC cable** and run it from the camera to either CAM/DISP port (contacts the correct way, latch
  closed).
- Install: `python3-picamera2` comes from **apt** (the installer does this) and the venv is created with
  `--system-site-packages` so it can import it. Confirm the camera with `rpicam-hello --list-cameras`,
  then grab a test frame headless with `rpicam-jpeg -n -o /tmp/test.jpg`.
- **NoIR has no IR-cut filter:** images look reddish/magenta in daylight, and it sees nothing in the dark
  without an **IR illuminator** (850/940 nm). Fine for lit rooms; add IR LEDs for night use.
- Tuning: `ROBOT_CAMERA_ENABLE`, `ROBOT_CAMERA_SIZE` (square letterbox before the LLM), `ROBOT_CAMERA_WARMUP_S`.
  Vision is **CPU-only and slow** on the Pi 5 (`ROBOT_VISION_TIMEOUT_S` defaults to 600 s) — the car is
  held at neutral during each look.

---

## Phase 6 — Wire the face panel (3.5" USB-C screen)

The robot's face. On Linux these "secondary screen" panels are **not** a framebuffer — they enumerate as
a **USB-serial** device and are driven by the `turing-smart-screen-python` library (cloned to
`~/turing-smart-screen-python` by the installer; `face.py` finds it via `ROBOT_FACE_LIB`).

- Connect the panel to a Pi USB port with a **data** USB-C cable; it appears as `/dev/ttyACM0`
  (CH340, USB id `1a86:5722`). The installer adds you to the **`dialout`** group — re-login after.
- Confirm: `lsusb | grep -i 1a86` and `ls /dev/ttyACM*`. Identify the hardware revision by running the
  library's `simple-program.py` once (`cd ~/turing-smart-screen-python`); set `ROBOT_FACE_REVISION=A`
  (Turing/UsbPCMonitor 320×480) or `B` (XuanFang) accordingly.
- It's **fully optional and fail-safe.** If the panel doesn't enumerate, set
  `ROBOT_FACE_BACKEND=framebuffer` to use a small **DSI/HDMI** display instead (PIL → `/dev/fb0`, no
  pygame), or `ROBOT_FACE_BACKEND=simulated` to just log expressions. A missing face never blocks driving.
- **Animated faces (optional):** drop PNG frames in `brain/faces/<state>/NN.png` (states
  `idle/listening/thinking/talking/exploring/happy/confused/alarmed/warmup/capturing`). A state with
  **≥2 frames animates** on a background thread (`talking` is fast with a random-frame mouth); 1 frame
  is a static still; **none** falls back to the built-in procedural face — so with no `faces/` dir the
  face behaves exactly as before. Tune `ROBOT_FACE_FPS` / `ROBOT_FACE_FRAMES_DIR`; the USB-C panel's
  serial link caps the effective frame rate. Inspect frames in dry-run with `ROBOT_FACE_DUMP=<dir>`.
- Mount it forward-facing as the robot's "face"; dim it with `ROBOT_FACE_BRIGHTNESS` to save power.

**Now run the brain LIVE on the bench (still no chassis/motor).** With the PCA9685 + 3 sensors + camera +
face wired and the ESC/motor *not connected*, the same `robot.hw` logs now reflect real I2C/GPIO:

```bash
i2cdetect -y 1                  # PCA9685 at 0x40
rpicam-hello --list-cameras     # IMX219 NoIR detected
lsusb | grep -i 1a86            # USB-C face panel
# read the 3 sensors live before trusting the gate (snippet in the Command cheat-sheet)
brain/.venv/bin/python brain/main.py
```

Only after this live bench pass do you build the chassis and connect the ESC/motor (Phase 7).

---

## Phase 7 — Mechanical build (chassis + mount everything)

This is where the **ESC, motor, and LiPo finally get connected** — until now nothing could move.

1. Assemble the 1/10 roller chassis (or start from your built RTR): drivetrain, portal axles, links,
   shocks, 1.9" wheels. Mount the **540 35T motor** into the transmission; mesh the pinion/spur with a
   sliver of slack (a strip of paper between the gears while you tighten sets the backlash).
2. Mount the **steering servo** to the front axle servo mount; connect the steering link. Leave the
   horn off until you've found the servo's center in calibration, then attach it pointing straight.
3. Build the brain stack: Pi 5 + active cooler (+ UPS HAT later — Phase 9). Mount on a
   3D-printed/cut deck on the frame rails (the SCX10 313 mm form factor has many free community trays).
   Keep fan airflow clear.
4. Mount the **ESC**, **PCA9685**, and **LiPo** on the chassis; route wiring clear of the driveshafts
   and steering links. Keep the mic away from the cooler fan to reduce recorded noise. **Now make the
   motor-domain connections you deferred in Phase 3:** ESC 3-wire plug → PCA9685 **ch0** (signal / V+ /
   GND, with the BEC red on the V+ rail), the **steering servo → ch1**, and the **LiPo → ESC through
   your physical kill switch**. Keep all grounds common (Pi ⟷ PCA9685 ⟷ ESC); never bridge the battery
   positives (Phase 2).
5. Mount the **3-sensor plate** across the front (forward + ~45–60° left/right), clear of the wheels and
   roughly level. Mount the **camera** forward-facing near the sensors, and the **face panel** where you
   want the robot to "look at" you. Route the CSI ribbon and USB cables away from the driveshafts.

---

## Phase 8 — Live calibration & bring-up (wheels OFF the ground)

Phases 1–6 already verified the brain, the I2C bus, and the three sensors; this phase powers the **ESC
for the first time**, so keep the **wheels OFF the ground until the very end** and a **physical kill
switch in hand**.

1. **ESC arm:** with the LiPo connected, the brain holds neutral for `ROBOT_ESC_ARM_SECS` on start — the
   ESC should beep its arming tones. No arm = check signal/GND to PCA9685 ch0 and the BEC on V+.
2. **Steering:** run the brain, say *"center" / "left" / "right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT`
   so the wheels center true and turn fully without the servo buzzing (binding = back the angle off).
3. **Throttle direction:** say *"go forward"* slowly. Backwards? Set `ROBOT_THROTTLE_INVERT=1` (or swap
   the motor wires). Reverse may need the ESC put in a forward/reverse mode (see the ESC manual).
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

```bash
# Mic device id  →  put the plughw string in config/robot.env (ROBOT_MIC)
arecord -l

# Audio sanity check (same 16 kHz mono S16_LE the app uses)
arecord -D plughw:1,0 -d 3 -f S16_LE -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav

# Confirm the bus + peripherals
i2cdetect -y 1                  # PCA9685 at 0x40 (and 0x36 once the UPS is fitted)
rpicam-hello --list-cameras     # IMX219 NoIR (after the 22→15 adapter cable)
lsusb | grep -i 1a86            # USB-C face panel

# Read all three HC-SR04P live (cover each beam in turn and watch ITS number drop -- this is
# how you confirm front/left/right aren't swapped). Uses the pins from robot.env; Ctrl-C to stop.
set -a; source config/robot.env; set +a
brain/.venv/bin/python - <<'PY'
import os, time
from gpiozero import DistanceSensor
p = lambda n: int(os.environ[n])
s = {
    "front": DistanceSensor(echo=p("ROBOT_ECHO_PIN"),       trigger=p("ROBOT_TRIG_PIN"),       max_distance=4.0),
    "left":  DistanceSensor(echo=p("ROBOT_LEFT_ECHO_PIN"),  trigger=p("ROBOT_LEFT_TRIG_PIN"),  max_distance=4.0),
    "right": DistanceSensor(echo=p("ROBOT_RIGHT_ECHO_PIN"), trigger=p("ROBOT_RIGHT_TRIG_PIN"), max_distance=4.0),
}
while True:
    print("  ".join(f"{k}={v.distance*100:5.1f}cm" for k, v in s.items()))
    time.sleep(0.3)
PY
```

### Safety note — software failsafe, not hardware

`driver.py` runs a control thread that forces the ESC to **neutral** on STOP, on error, on a forward
obstacle, and when a move's `duration_ms` deadline passes; the ESC is armed at neutral on boot. The
explore/find behaviour only ever calls into this same gated path (it never drives PWM directly), and it
holds the car at neutral during the slow vision step. This replaces the old Pico hardware watchdog **in
software only** — if the whole Python process is killed, the PCA9685 keeps emitting the last pulse and
nothing in hardware stops the motor. The **physical kill switch on the LiPo is your real safety net.**
Keep `ROBOT_MAX_THROTTLE` low and durations short.
