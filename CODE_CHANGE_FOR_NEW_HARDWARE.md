# CODE_CHANGE_FOR_NEW_HARDWARE.md — make LocalAiRobot hardware-agnostic

**Purpose.** Let anyone build this RC-crawler AI robot from scratch on a **Raspberry Pi 5** *or* a **Jetson
Orin Nano 8GB**, using **either the original reference parts or the newer AliExpress-wishlist alternatives**,
and still get the same result. Every hardware axis below lists the **old part and the new alternative(s) side
by side**, and says exactly how the software supports both:

- **CODE** — the code is made generic so it works with either part (best; no user action).
- **CONFIG** — the code can't tell the parts apart, so the choice moves to `config/robot.env` (documented,
  with a copy-paste **`sed`** command). *Last resort, per the design rule below.*
- **DOC** — pure mechanical/electrical part; no software impact, only the build docs mention it.

> **STATUS: PLAN — awaiting the owner's "Go ahead".** Nothing in this file has been applied. Once approved,
> an LLM (or a person) can execute Parts 1→N **in order**, top to bottom, without further design decisions.

---

## Part 0 — Instructions for the applying agent (READ FIRST)

1. **Work Parts 1 → N in order.** Each part is self-contained: it states the exact files, the exact edits,
   the new `config/robot.env` keys (added with a comment **and** to `config/README.md`), the `sed` command(s)
   a user runs to switch parts, the doc updates, and a verification step.
2. **Board parity is mandatory.** Every change must keep BOTH boards working: Raspberry Pi 5
   (`brain/boards/pi5.py`) and Jetson Orin Nano (`brain/boards/jetson.py`). Never add a Pi-only or Jetson-only
   import to a core module (`driver.py`, `main.py`, `camera.py`, `vision.py`, `face.py`, `diagnostics.py`);
   board-specific code lives under `brain/boards/` (or a dedicated backend module).
3. **The genericity ladder (obey in this order):** (a) make the code detect/handle both parts automatically;
   (b) if it can't, add ONE `config/robot.env` key that selects the behaviour, defaulting to the **current
   reference part** so existing builds are unchanged; (c) document the key + a `sed` one-liner. Never hard-code
   a second part's values inline.
4. **Preserve the safety invariants** (unchanged from PLAN.md Part 0): the ~20 Hz `driver.py` control thread
   stays the sole normal-path PWM writer and converges the ESC to neutral on every abnormal path; the forward
   obstacle gate cuts FORWARD within one debounced cycle; a **dead/missing sensor fails CLOSED** (treated as
   "blocked/unknown", never "clear").
5. **Preserve the test modes:** `ROBOT_DRY_RUN=1` (all hardware faked) and `ROBOT_MOTORS_OFF=1` (PWM faked,
   sensors live) must keep working after every change.
6. **New env keys** get a default in code (so an omitted key falls back), a commented line in
   `config/robot.env`, and a row in `config/README.md`. Default = the **existing reference hardware**.
7. **After each part:** `python3 -m py_compile <changed .py>` (or `bash -n` for shell). Where a bench is
   available, `ROBOT_DRY_RUN=1 ./scripts/run_me.sh --dry` must start clean.
8. **Commit style (owner's standing rule):** a **single-line** commit message, **no body, no
   `Co-Authored-By` trailer**. One concise line per part is fine, or one line for the whole change — the
   owner will tell you which. Do not auto-push.

---

## Hardware axis index

| # | Axis | Reference part (current) | New wishlist alternative(s) | How supported |
|---|------|--------------------------|-----------------------------|---------------|
| 1 | Distance sensing | 3× HC-SR04P ultrasonic (GPIO) | **VL53L1X** ToF (I²C, 400 cm) | **CODE** — `ROBOT_SENSOR_TYPE` dispatch + VL53L1X backend |
| 2 | Camera | Arducam IMX519 16MP AF (CSI) | **5MP OV5647 IR-CUT wide/night-vision**, **Camera Module 3 (IMX708)**, USB webcam, Jetson IMX219 | **CONFIG** — overlay/backend/source/AF keys |
| 3 | ESC | SURPASS/WP-1060 brushed | **HobbyWing QuicRun WP-1080-G2** | **CONFIG** — new patch script + calibration keys |
| 4 | Drive motor | 540 35T brushed | **540 80T brushed (Surpass)** | **CONFIG** — throttle-tuning keys only (no code) |
| 5 | Steering servo | DS3225 25 kg | **20/25/30/35 kg metal-gear digital** | **CONFIG** — steer angle/pulse keys (already exist) |
| 6 | Microphone | cheap USB "PnP" mic | **UM02 / UM10 USB conference mic** | **CODE** (done) — `setup_audio.sh` name-based; pretty-name fix landed |
| 7 | Speaker | UACDemo USB speaker | **Mini USB soundbar** | **CODE** (done) — `setup_audio.sh` auto-detect |
| 8 | Face panel | 3.5" USB-C IPS 320×480 (Turing) | **same 3.5" IPS 320×480 USB-C** | **CODE** (done) — turing backend + `ROBOT_FACE_ORIENTATION` |
| 9 | PWM driver | PCA9685 | **PCA9685** (same) | none |
| 10 | Compute | Raspberry Pi 5 | **Jetson Orin Nano 8GB** | **CODE** (done) — board HAL |
| 11 | Storage | microSD | **NVMe SSD + Pi5 PCIe M.2 adapter** | **DOC** — memory.db/model paths note |
| 12 | IMU (new capability) | *(none)* | **BNO085 9DOF IMU** | **CODE (optional, gated)** — heading-hold feature |
| 13 | Rotary encoder (new) | *(none)* | **AS5600 12-bit magnetic encoder** | **CODE (optional, gated)** — odometry/steer feedback |
| 14 | Chassis / power / wiring | SCX10-class frame, ESC BEC | **313 mm SCX10 frame, fuse holder, IP67 box, ZEEE Lipo, T-plug harness, carbon sheet, Dupont, brackets, CM3 mount** | **DOC** — BOM only |

Parts 6, 7, 8 and 10 are **already done** in the current codebase (noted for completeness; the applying agent
only VERIFIES them). Parts 1–5, 11–14 are the work.

---

## Part 1 — Distance sensing: HC-SR04P ↔ VL53L1X ToF  (CODE)

**Old:** 3× HC-SR04P ultrasonic on GPIO TRIG/ECHO. **New:** 3× **VL53L1X** laser ToF on the shared I²C bus
(≈50 Hz, 400 cm, no TRIG/ECHO pins, immune to acoustic crosstalk, 3.3 V native).

This is the **only axis that needs real new code**, and it is **already fully specified** as **HW-SWAP-03 in
`PLAN.md` (Part 11)** — a new `brain/vl53l1x_sensor.py` backend implementing the `DistanceSensor` protocol,
a `ROBOT_SENSOR_TYPE` dispatch seam in `driver.py` `_build_sensor_objs`, XSHUT-based I²C address assignment,
and a board-neutral `Board.make_digital_output(pin)` factory. **Do not re-derive it — execute HW-SWAP-03
exactly**, with these additions for the "either part" goal:

1. **`ROBOT_SENSOR_TYPE` defaults to `hcsr04`** (the reference part) so existing builds are untouched; set
   `vl53l1x` to switch. Add to `config/robot.env` (new "OBSTACLE SENSORS" sub-block) and `config/README.md`.
2. Keep the six `ROBOT_*_TRIG/ECHO_PIN` keys meaningful only for `hcsr04`; add `ROBOT_TOF_ADDR_FRONT/LEFT/RIGHT`
   (`0x2A/0x2B/0x29`) and `ROBOT_TOF_XSHUT_FRONT/LEFT/RIGHT=<pins>` for `vl53l1x` (comment that each set is
   ignored by the other type).
3. `brain/diagnostics.py` and `scripts/test_sensors.sh` route through the same dispatch (HW-SWAP-03 step 5).
4. Fail-closed semantics (SEN-04/05) are preserved by the new backend (HW-SWAP-03 step 1b).

**Switch to ToF (sed):**
```bash
sed -i 's/^ROBOT_SENSOR_TYPE=.*/ROBOT_SENSOR_TYPE=vl53l1x/' config/robot.env
# then set the XSHUT pins for your board's numbering, e.g. on a Pi (BCM):
sed -i 's/^ROBOT_TOF_XSHUT_FRONT=.*/ROBOT_TOF_XSHUT_FRONT=17/' config/robot.env
```
**Dependency:** add `adafruit-circuitpython-vl53l1x` to **both** `brain/requirements.txt` and
`brain/requirements-jetson.txt` (or to `brain/requirements-common.txt` **if** the EXT-09 shared-requirements
split has been applied — that file does not exist yet). **Verify:** `ROBOT_SENSOR_TYPE=vl53l1x ROBOT_DRY_RUN=0 ./scripts/test_sensors.sh`
reads all three; the forward gate still cuts FORWARD < `ROBOT_OBSTACLE_CM`; a covered sensor reads "blocked".

> The VL53L1X's cartoon HC-SR04 mounting bracket in the wishlist is mechanical only — the ToF board is smaller,
> so note in `docs/assembly.md` that a ToF build can skip the ultrasonic brackets.

---

## Part 2 — Camera variants  (CONFIG)

The picamera2 (Pi) / GStreamer / V4L2 (Jetson) pipeline is already **sensor-agnostic at the code level** — a
camera swap is boot-overlay + config only (no code). The reference build uses the **Arducam IMX519 16MP AF**;
support these alternatives purely through existing keys:

| Camera | `ROBOT_CAMERA_OVERLAY` (install) | `ROBOT_CAMERA_BACKEND` | `ROBOT_CAMERA_AUTOFOCUS` | Notes |
|--------|----------------------------------|------------------------|--------------------------|-------|
| Arducam IMX519 16MP AF (ref) | `imx519,cam0` | *(empty → picamera2)* | `1` (needs `--imx519-af`) | current default |
| **5MP OV5647 IR-CUT wide / night-vision** | `none` (auto-detects) | *(empty)* | `0` (fixed-focus sensor) | IR-CUT/IR-LED is a separate power line, not GPIO here |
| **Camera Module 3 (IMX708)** | `none` (auto-detects) | *(empty)* | `1` (first-class AF, no fork) | best AF; wishlist CM3 mount bracket fits |
| USB webcam (either board) | n/a | `v4l2` + `ROBOT_CAMERA_SOURCE=0` | n/a | |
| Jetson IMX219 (CSI) | n/a | `gstreamer` (clear SOURCE) | n/a | IMX519 needs Arducam's Jetson driver |

**Work items:**
1. **CODE (small):** OV5647/CM3 auto-detect, so they need `camera_auto_detect=1` and NO `dtoverlay=imx519` in
   the boot config. `install_stack_pi5.sh`'s `none|off|auto` overlay branch currently only PRINTS "skipped" —
   it does NOT undo a prior IMX519 install — so a builder switching FROM the reference IMX519 build is left with
   a stale `dtoverlay=imx519` + `camera_auto_detect=0` and the new sensor silently never appears. **Fix that
   branch** to actively rewrite `camera_auto_detect=1` and remove/comment any `dtoverlay=imx519` line (the
   inverse of the writes it does in the `*)` branch). Then a fresh OV5647/CM3 install with
   `ROBOT_CAMERA_OVERLAY=none` works AND a switch from IMX519 is clean. **Doc:** add the table above to
   `docs/assembly.md` (camera section) + the README parts list, side by side with the IMX519 row.
2. **Autofocus honesty:** `ROBOT_CAMERA_AUTOFOCUS` is a no-op on fixed-focus sensors (OV5647), and this ALREADY
   degrades gracefully — `brain/picamera2_backend.py` sets AF only `if "AfMode" in camera_controls` (line ~50),
   so a fixed-focus sensor just skips it (logs "camera: autofocus not enabled", no error). **No change needed;**
   default `AUTOFOCUS=1` is safe. On the Jetson (`gst_camera.py`) AF is not driven at all.
3. **Optional (only if a night-vision build wants SW IR-LED control):** add `ROBOT_CAMERA_IR_LED_PIN=` (empty =
   off) and, when set, drive it via `Board.make_digital_output` (the same factory Part 1 adds) — **gate behind
   the empty default so it's a no-op for everyone else.** Last-resort config; document the pin + `sed`.

**Switch to a fixed-focus 5MP / official CM3 (after the Part-2 installer fix above):**
```bash
sed -i 's/^ROBOT_CAMERA_AUTOFOCUS=.*/ROBOT_CAMERA_AUTOFOCUS=0/' config/robot.env   # OV5647 = fixed focus (CM3: leave =1, it has real AF)
ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack.sh    # dispatcher -> pi5 installer; the FIXED 'none' branch
                                                        # now resets camera_auto_detect=1 + drops dtoverlay=imx519
# If you have NOT applied the Part-2 installer fix yet, undo the IMX519 overlay by hand instead:
#   sudo sed -i -E 's/^camera_auto_detect=.*/camera_auto_detect=1/' /boot/firmware/config.txt
#   sudo sed -i -E '/^dtoverlay=imx519/d'                            /boot/firmware/config.txt
```
**Verify:** `rpicam-hello --list-cameras` shows the sensor; `ROBOT_DRY_RUN=0 ./scripts/run_diagnostics.sh`
captures a frame; a "what do you see?" returns a description.

---

## Part 3 — ESC: WP-1060 ↔ HobbyWing QuicRun WP-1080-G2  (CONFIG)

ESCs are driven as a PWM channel, so a swap is **calibration only** (HW-SWAP-05 in PLAN.md). The repo already
ships per-ESC patch scripts under `scripts/patches/` (e.g. `hb_qr_1060.sh`).

1. **Add `scripts/patches/hb_qr_1080_g2.sh`** — model it on `hb_qr_1060.sh` but copy **only the throttle
   `set_kv` lines** (`ROBOT_ESC_NEUTRAL`, `ROBOT_ESC_FWD_FLOOR/CEIL`, `ROBOT_ESC_REV_FLOOR/CEIL`,
   `ROBOT_ESC_REVERSE_DOUBLETAP`, matching the WP-1080-G2 program-card mode). **OMIT the
   `set_kv ROBOT_ESC_CHANNEL 14` line** — `hb_qr_1060.sh:38` still hard-codes channel 14 (that is **INF-07 in
   PLAN.md, an UNFIXED low-severity bug**); a generic 1080-G2 build has its ESC on the default ch0, so the new
   patch must leave `ROBOT_ESC_CHANNEL` alone (or expose it as an opt-in `--esc-channel N` flag). Re-verify
   `ROBOT_THROTTLE_INVERT` for the 1080-G2 rather than copying the 1060's value blindly.
2. **Doc:** list the new patch in `scripts/patches/README.md` and the README quickstart step 3, side by side
   with the 1060.
3. **No core code change** — the ESC keys (`ROBOT_ESC_*`) already parameterise everything.

**Apply the 1080-G2 profile (sed / script):**
```bash
./scripts/patches/hb_qr_1080_g2.sh          # after install, before first drive
```
**Verify:** `./scripts/run_diagnostics.sh --wheels-off` arms the ESC (long ready beep) and the throttle sweep
maps FWD/REV correctly; re-tune with the bench procedure if the neutral drifts.

---

## Part 4 — Drive motor: 540 35T ↔ 540 80T  (CONFIG, no code)

The motor is downstream of the ESC — the software never talks to it. An 80T is higher-torque/lower-Kv than a
35T, so it crawls slower at the same throttle. **No code or new key** — just note in `docs/assembly.md` that a
higher-turn motor may want a slightly higher `ROBOT_DRIVE_CRAWL_SPEED` / `ROBOT_ESC_FWD_FLOOR`, tuned on the
bench. Document the two motors side by side in the BOM.

```bash
# example: nudge the crawl floor up for a high-turn (slower) motor
sed -i 's/^ROBOT_DRIVE_CRAWL_SPEED=.*/ROBOT_DRIVE_CRAWL_SPEED=8/' config/robot.env
```

---

## Part 5 — Steering servo: DS3225 ↔ 20/25/30/35 kg digital  (CONFIG, keys exist)

Any standard PWM servo works. The travel is already parameterised by
`ROBOT_STEER_CENTER` / `ROBOT_STEER_LEFT` / `ROBOT_STEER_RIGHT` (degrees). A different servo may need a
different center/endpoints to avoid binding the steering linkage. **No code change.**

1. **Doc:** in `docs/assembly.md` (servo section) explain how to find CENTER/LEFT/RIGHT for a new servo
   (bench: set CENTER so wheels point straight, then LEFT/RIGHT to just short of mechanical bind), side by side
   with the DS3225 defaults.

**Re-center for a new servo (sed):**
```bash
sed -i 's/^ROBOT_STEER_CENTER=.*/ROBOT_STEER_CENTER=90/' config/robot.env
sed -i 's/^ROBOT_STEER_LEFT=.*/ROBOT_STEER_LEFT=130/'   config/robot.env
sed -i 's/^ROBOT_STEER_RIGHT=.*/ROBOT_STEER_RIGHT=40/'  config/robot.env
```
**Verify:** `./scripts/run_diagnostics.sh --wheels-off` sweeps the servo LEFT→CENTER→RIGHT with no buzzing/bind.

---

## Parts 6–8 — Mic, speaker, face panel (ALREADY DONE — verify only)

- **Part 6 Microphone (UM02/UM10 conference mic):** `scripts/setup_audio.sh` already writes `ROBOT_MIC` (ALSA
  id) and `ROBOT_WAKEWORD_DEVICE` (PortAudio **pretty name**, quoted) by name; the conference mics are plain
  USB audio and "just work" via HW-SWAP-02. **Verify:** run `setup_audio.sh`, confirm `wake HEALTH` dropped-%
  ≈ 0. **Doc:** list the conference mics in the BOM as recommended alternatives to the cheap PnP mic.
- **Part 7 Speaker (Mini USB soundbar):** `setup_audio.sh` auto-detects the single USB playback card and writes
  `~/.asoundrc` + `ROBOT_SOUNDS_DEVICE`. **Verify:** `speaker-test -D default -t wav -l 1`. **Doc:** BOM row.
- **Part 8 Face panel (3.5" IPS 320×480 USB-C):** supported by `brain/face.py` turing backend; the panel is now
  drawn **landscape** by default (`ROBOT_FACE_ORIENTATION=landscape`) with partial-region updates for speed.
  **Verify:** the face shows upright; if upside-down, `ROBOT_FACE_ORIENTATION=reverse_landscape`.

---

## Part 9 & 11 — PWM driver, storage  (none / DOC)

- **PCA9685** — identical part; nothing to do.
- **NVMe SSD + Pi 5 PCIe M.2 adapter (Kingston KC3000):** pure infrastructure — faster boot/model storage. **No
  code change.** **Doc:** in `docs/assembly.md`, note that if the OS/repo lives on NVMe, the model download
  paths and `ROBOT_MEMORY_DB` (`~/.localairobot/memory.db`) still resolve under `$HOME` — nothing to configure.
  Mention the M.2 adapter as an optional performance upgrade.

---

## Part 12 — IMU BNO085 (NEW optional capability — CODE, gated)  ⚠️ opt-in

Adds heading/orientation sensing (true "turn 90° left", heading-hold) the robot does not have today. **This is
new feature code, not a swap** — keep it fully **opt-in and disabled by default** so no existing build changes.

1. New `brain/imu.py`: a small daemon reader over I²C (adafruit-circuitpython-bno08x) exposing `.heading()` /
   `.yaw_rate()`, non-blocking, fail-safe (returns `None` on error — callers must treat `None` as "no IMU").
2. Config: `ROBOT_IMU_TYPE=` (empty = **none/disabled**, `bno085` = enabled), `ROBOT_IMU_ADDR=0x4A`. Default
   empty → `driver.py`/`behavior.py` behave exactly as now.
3. Integration is a **separate, later project** (closed-loop turns in the 20 Hz loop). This part only lands the
   **driver + config seam + "disabled" default**; it must not alter control flow when `ROBOT_IMU_TYPE` is empty.
4. Dependency `adafruit-circuitpython-bno08x` goes in requirements **only** — do not import it unless enabled.

**Enable (sed):** `sed -i 's/^ROBOT_IMU_TYPE=.*/ROBOT_IMU_TYPE=bno085/' config/robot.env`
**Verify:** disabled by default → no behaviour change; enabled → `imu.py` self-test prints a live heading.

---

## Part 13 — AS5600 magnetic encoder (NEW optional — CODE, gated)  ⚠️ opt-in

A 12-bit magnetic angle sensor — useful for steering-angle feedback or wheel odometry (neither exists today).
Same discipline as Part 12: **opt-in, disabled by default.**

1. New `brain/encoder.py` (I²C, adafruit-circuitpython-as5600 or raw `Adafruit_Extended_Bus`), non-blocking,
   fail-safe.
2. Config: `ROBOT_ENCODER_TYPE=` (empty = none, `as5600` = enabled), `ROBOT_ENCODER_ADDR=0x36`. **NB:** 0x36
   collides with the UPS-HAT fuel gauge on the Pi reference build — document the conflict and that a second
   AS5600 needs the `DIR`-pin address trick or a separate bus.
3. Lands the driver + seam only; consumption (odometry) is a later project. Empty default = no change.

**Enable (sed):** `sed -i 's/^ROBOT_ENCODER_TYPE=.*/ROBOT_ENCODER_TYPE=as5600/' config/robot.env`

---

## Part 14 — Chassis / power / wiring  (DOC only)

Pure mechanical/electrical wishlist parts — **no software impact**. Add each to the BOM table in `README.md`
and `docs/assembly.md`, side by side with the current parts, with a one-line "why":

- **313 mm SCX10 12.3" wheelbase frame** — drop-in alternative chassis (same 1/10 crawler class).
- **Waterproof auto fuse holder** — recommended inline fuse on the battery main (safety); wiring note.
- **IP67 ABS enclosure** — house the Pi/Jetson + PCA9685 for outdoor runs.
- **ZEEE 3S–6S Lipo + T-plug harness + parallel Y-splitter** — power; note the ESC BEC / UPS-HAT still powers
  the Pi, and match the Lipo S-count to the ESC/motor rating.
- **Carbon-fiber sheet, Dupont jumper kit, HC-SR04 brackets, CM3 acrylic mount** — build materials.

---

## Appendix A — All new/changed config keys (cheat-sheet)

Added by this plan (all default to the **reference hardware**, so an existing build is unchanged until you
`sed` them):

| Key | Default | Part | Selects |
|-----|---------|------|---------|
| `ROBOT_SENSOR_TYPE` | `hcsr04` | 1 | `hcsr04` \| `vl53l1x` |
| `ROBOT_TOF_ADDR_FRONT/LEFT/RIGHT` | `0x2A/0x2B/0x29` | 1 | ToF I²C addresses |
| `ROBOT_TOF_XSHUT_FRONT/LEFT/RIGHT` | *(board pins)* | 1 | ToF XSHUT pins |
| `ROBOT_CAMERA_IR_LED_PIN` | *(empty=off)* | 2 | optional IR-LED GPIO |
| `ROBOT_IMU_TYPE` / `ROBOT_IMU_ADDR` | *(empty)* / `0x4A` | 12 | `bno085` (opt-in) |
| `ROBOT_ENCODER_TYPE` / `ROBOT_ENCODER_ADDR` | *(empty)* / `0x36` | 13 | `as5600` (opt-in) |

Reused (already exist as `robot.env` keys): `ROBOT_CAMERA_BACKEND/SOURCE/AUTOFOCUS`, `ROBOT_ESC_*`,
`ROBOT_STEER_*`, `ROBOT_DRIVE_CRAWL_SPEED`, `ROBOT_MIC/WAKEWORD_DEVICE/SOUNDS_DEVICE`, `ROBOT_FACE_ORIENTATION`,
`ROBOT_BOARD`, `ROBOT_I2C_BUS`.
> **`ROBOT_CAMERA_OVERLAY` is an INSTALL-TIME env var only** (default `imx519,cam0`, read by
> `install_stack_pi5.sh`) — it is **not** a `robot.env` key, so you can't `sed` it there; pass it on the
> installer command line as shown in Part 2.

## Appendix B — Order of execution & final verification

Recommended order (safety- and dependency-first): **1 (ToF)** → **2 (camera)** → **3 (ESC)** → **5 (servo)** →
**4 (motor doc)** → **6–8 (verify)** → **9/11/14 (docs)** → **12/13 (opt-in sensors, last)**. After all parts:

1. `python3 -m py_compile brain/*.py` and `bash -n scripts/*.sh` clean.
2. `ROBOT_DRY_RUN=1 ./scripts/run_me.sh --dry` starts clean on both a Pi and a Jetson checkout.
3. With the **reference** hardware and a **stock `config/robot.env`**, behaviour is byte-identical to before
   this plan (every new key defaulted to the reference part).
4. Switch each axis via its `sed` command and re-run `./scripts/run_diagnostics.sh` — each alternative part
   works with no code edits.
