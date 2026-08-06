# Hardware config patches

Each script here applies one hardware choice to `config/robot.env` in **one command** (instead of hand-editing
keys or remembering `sed` lines), then prints any follow-up (a boot-overlay reinstall, a `pip install`, a
bench step). All are **idempotent** (re-runnable) and only touch the keys they own. Full rationale + the
either/or hardware options: [`config/README.md`](../../config/README.md).

## How it's organised

Patches are sorted into three folders and **named by apply order** (`1_` first, then `2_`, `3_`, `4_`):

- **`common/`** — board-agnostic. Run these on **any** board (Pi 5 or Jetson) based on your hardware.
- **`pi5/`** — Raspberry Pi 5 only (they edit the Pi boot config / install the Pi libcamera fork).
- **`jetson/`** — NVIDIA Jetson only.

**The flow:** run the numbered patches in order for your build — the camera one (`3_…`) comes from **your
board's folder**, the rest from **`common/`**. You still pick *one* variant per number (one ESC, one sensor
type, one camera). Run them **after** `install_stack_pi5.sh` / `install_stack_jetson.sh`.

```
scripts/patches/
  common/   1_esc_*   2_sensor_*   4_imu_bno085   4_encoder_as5600   5_face_id_insightface   6_motion_pir_*
  pi5/      3_camera_usb_webcam / 3_camera_usb_ir_1080p
  jetson/   3_camera_usb_webcam / 3_camera_usb_ir_1080p
```

## 1 — ESC (drive) · `common/`
```bash
./scripts/patches/common/1_esc_hb_qr_1060.sh       # HobbyWing QuicRun WP-1060  (F/R: Mode cap OFF)  [sets ROBOT_ESC_CHANNEL=14 for one build]
./scripts/patches/common/1_esc_uliik_60a.sh        # ULIIK 60A crawler ESC (F/R) -- SAFE STARTERS ONLY; bench-calibrate before trusting
```

## 2 — Obstacle sensors · `common/`
```bash
./scripts/patches/common/2_sensor_hcsr04.sh    # 3x HC-SR04P ultrasonic (the default)
./scripts/patches/common/2_sensor_vl53l1x.sh   # 3x VL53L1X laser ToF (I2C) -- set the ROBOT_TOF_XSHUT_* pins to your wiring
```

## 3 — Camera · **your board's folder**
**The camera is USB (UVC) on both boards.** Pick exactly one patch — same two choices per board:
```bash
# Raspberry Pi 5 (pi5/):
./scripts/patches/pi5/3_camera_usb_webcam.sh   # any USB webcam (v4l2) -- installs OpenCV; no ribbon, no overlay
./scripts/patches/pi5/3_camera_usb_ir_1080p.sh # ...or a 1080p USB module with IR-CUT night vision + fixed 3.6mm lens

# Jetson (jetson/):
./scripts/patches/jetson/3_camera_usb_webcam.sh   # any USB webcam (v4l2)
./scripts/patches/jetson/3_camera_usb_ir_1080p.sh # ...or the same 1080p IR night-vision module
```
Both patches set `ROBOT_CAMERA_BACKEND=v4l2` and a `/dev/videoN` source; the IR one additionally asks for
1920x1080 capture, a 768px square target and `ROBOT_CAMERA_AUTOFOCUS=0` (its 3.6mm lens is fixed). Neither
touches the boot config: **a USB camera is not a CSI device, so no `dtoverlay` and no reboot are involved** —
`./scripts/run_me.sh --reload` is enough. This is why `install_stack_pi5.sh` now defaults to
`ROBOT_CAMERA_OVERLAY=none`, and why there is no autofocus/driver step in the install any more.

> **Night vision changes what recognition can do.** After dark the IR-CUT filter swings out and frames are
> greyscale infrared — faces still *detect*, but ArcFace embeddings from IR are a different domain to daylight
> enrolment photos, so expect the generic greeting at night until you enrol a few IR shots per person too
> (`ROBOT_FACE_ID_AGGREGATE=max` exists exactly so both sets can live under one name). The patch spells out
> the steps when you run it.

**CSI cameras are no longer supported.** The Arducam IMX519 / OV5647 / Camera Module 3 patches were removed:
a USB module gives better faces per pound for a stationary concierge, and the IMX519 path dragged in Arducam's
**libcamera fork** — a third-party system change that an `apt upgrade` could silently revert. The picamera2
backend (`brain/picamera2_backend.py`) still exists and still works if you set `ROBOT_CAMERA_BACKEND=picamera2`
for an auto-detected official module; only the patches and the installer's auto-AF step went.
`git log --diff-filter=D -- scripts/patches` will find them if you ever want one back.

## 4 — Optional add-on sensors (off by default) · `common/`
```bash
./scripts/patches/common/4_imu_bno085.sh       # enable a BNO085 9-DOF IMU (heading/yaw) -- also pip-installs its lib
./scripts/patches/common/4_encoder_as5600.sh   # enable an AS5600 magnetic angle encoder -- also pip-installs its lib
```
These land the reader + config only (see `brain/imu.py` / `brain/encoder.py`); disable by clearing
`ROBOT_IMU_TYPE` / `ROBOT_ENCODER_TYPE`.

## 5 — Concierge Mode face recognition (off by default) · `common/`
```bash
./scripts/patches/common/5_face_id_insightface.sh   # local ArcFace ONNX face-ID for ROBOT_CONCIERGE_MODE=1
```
Checks deps (reuses onnxruntime; face detection needs `cv2` from the USB-webcam patch) and installs the
recognition model. Then enrol people with `./scripts/enroll_person.sh "Name" "desc" "say-as" photo.jpg` (or `--camera`) and set `ROBOT_CONCIERGE_MODE=1`.
Full flow: [docs/concierge.md](../../docs/concierge.md).

## 6 — Concierge wake sensor (which thing tells Jarvis someone arrived) · `common/`

The wake sensor is a **PIR** — pick the one your budget allows. Both are **config only**: a PIR hands the Pi
a digital HIGH/LOW on `ROBOT_PIR_PIN`, which is all `brain/pir.py` ever reads, so the choice is wiring and
numbers and **never code**. Pass a BCM pin to move it (`... 17`).

```bash
./scripts/patches/common/6_motion_pir_hcsr501.sh   # hobby HC-SR501 / Adafruit -- THE DEFAULT, a few pounds
./scripts/patches/common/6_motion_pir_papirs.sh    # Panasonic PaPIRs (EKMB/EKMC) -- the industrial part
```

**Plus one optional EXTRA, which is additive rather than exclusive** — it OR's a wireless sensor alongside
whichever wired PIR you picked above, so either one waking the robot is enough:

```bash
./scripts/patches/common/6_wake_zigbee_pir.sh zigbee2mqtt/pir_hallway   # --off to disable
```

Needs a Zigbee coordinator, Zigbee2MQTT and an MQTT broker on the robot first; the full walkthrough
(firmware, udev, broker, pairing) is in BENCH_SCRIPTS.md under *Adding a wireless Zigbee PIR*. It touches no
`ROBOT_PIR_*` key, so the wired sensor is unaffected, and every combination works — wired only, both, or
wireless only with `ROBOT_PIR_PIN` empty. It buys **coverage, not speed**: a GPIO pin interrupts in
microseconds against 50–300 ms for a Zigbee round trip, so mount the wireless one where the wired one
*cannot see* or it will never win a race.

| | HC-SR501 (hobby) | PaPIRs EKMB1105112 |
|---|---|---|
| **VCC** | **5 V** (pin 2) — it has its own regulator | **3.3 V** (pin 1) — see the warning below |
| Detection zones | roughly a dozen | **88** across 122° (+16 more over 150°) |
| Output | push-pull, latched ~2.5 s | sources only (100 µA max), latched ~2.6 s + 1.3 s mask |
| Warm-up | ~5 s | **25 s typ, 210 s max** — output undefined meanwhile |
| Idle current | ~50 mA | **1 µA** |
| Tuning | two potentiometers | none — it is right or it is mounted wrong |

> 🔴 **A PaPIRs on 5 V is destroyed.** Its operating range is 2.3–4.0 V with a **4.5 V absolute maximum**, so
> 5 V is past the destruct limit — quite apart from putting 5 V on a Pi GPIO that is not 5 V-tolerant. If you
> are swapping from an HC-SR501, **move the VCC wire from pin 2 to pin 1.** The PaPIRs also *requires* a
> 0.1 µF decoupling capacitor at its supply pin, and its lens must protrude **≥ 6.2 mm** past your enclosure
> with **no glass or acrylic** in front of it. Its patch prints all of this when you run it.

The two patches write the **same key set** with different values, so switching in either direction is
complete — that matters, because a patch that leaves behind a key its counterpart set is how you end up
half-switched with a robot that looks broken.

### Where you point it matters more than which you buy

A PIR is **passive**: it fires when a warm body *crosses* between its detection zones, which makes head-on
approach its worst case. Measured on the reference build with sensitivity at MAX and the delay pot at MIN,
**12 deliberate walk-straight-at-it approaches gave 1–3 detections; the same sensor gave 12/12 when the
person crossed its field instead.** A heated room and winter clothing make it worse again (both cut the
thermal contrast it depends on). **Aim a PIR across the traffic, never down the approach lane** — that one
change is usually worth more than any config, and it is free.

Zone density is the other half: it is what decides whether a small angular change still crosses a boundary,
which is exactly what a head-on approach produces. That is the real reason the PaPIRs does better at it.

**Mounting is a detection problem, not a tidiness one.** On this build a PIR fitted in the enclosure's only
vent hole — watching the Pi's own warm exhaust — produced **465 false wakes an hour, all night**. Mount it
*outside* the case, in still air, away from HVAC vents and sunlit windows.

---

**Idempotent:** each key is rewritten in place (ESC patches add a `# [<esc>]` provenance tag). **Steering
servo + drive motor** have no patch — they are continuous bench tuning, not a discrete config: set
`ROBOT_STEER_CENTER/LEFT/RIGHT` (servo) and `ROBOT_DRIVE_CRAWL_SPEED` / `ROBOT_MIN_FORWARD_SPEED` (motor) by hand
(see [config/README.md](../../config/README.md)). **Adding a new board?** add a `scripts/patches/<board>/`
folder and put its board-specific patches there (camera etc.); the `common/` ones apply unchanged. **Adding
hardware?** copy the closest script, rename it (`<order>_<axis>_<model>.sh`), and change the `set_kv` values.
