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
  common/   1_esc_*   2_sensor_*   4_imu_bno085   4_encoder_as5600   5_face_id_insightface
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
```bash
./scripts/patches/common/6_motion_pir_hcsr501.sh   # classic PIR (HC-SR501/Adafruit) -- THE DEFAULT
./scripts/patches/common/6_motion_vcnl4200.sh      # Vishay VCNL4200 active-IR proximity (I2C) instead
```
The PIR patch is **config only** — a PIR hands the Pi a digital HIGH/LOW on `ROBOT_PIR_PIN`, which is all
`gpiozero.MotionSensor` in `brain/pir.py` ever reads. Pass a BCM pin to move it (`... 17`).

The VCNL4200 patch **also switches the backend** (`ROBOT_WAKE_SENSOR=proximity` → `brain/proximity.py`),
because that sensor is I²C: four wires, not the PIR's pin, and it must be configured over the bus before it
measures anything. Its `NEAR`/`FAR` arguments are **raw reflected-IR counts, not centimetres** — measure them
in place with `./scripts/proximity_probe.sh`, in dark clothing as well as light.

### Choosing between them — field notes

**A PIR is passive**: it fires when a warm body *crosses* between its Fresnel zones. That makes head-on
approach its worst case. Measured on the reference build with sensitivity at MAX and the delay pot at MIN,
**12 deliberate walk-straight-at-it approaches gave 1–3 detections; the same sensor gave 12/12 when the
person crossed its field instead.** A heated room and winter clothing make it worse again (both cut the
thermal contrast it depends on), and the HC-SR501 has a hardware-fixed ~2.5 s blocking time after each
trigger. **Aim a PIR across the traffic, never down the approach lane.**

**An active IR proximity sensor** emits its own beam and measures the reflection, so head-on approach is its
*best* case and thermal contrast stops mattering. The trade is range (~50–100 cm useful) and reflectivity
(a black coat returns far less than a white shirt). Good for a greeter people walk *up to*.

**24 GHz mmWave presence radar (HLK-LD2410C) was tried and rejected here.** It produced **zero** false wakes
against the PIR's 465/hour — but only **4 usable wakes in 25 minutes**, because it reports *presence*, sees
through plasterboard, and in a room its owner occupies "is anyone present?" is permanently true. With almost
no rising edges there is nothing to trigger on. It would suit a **doorway or porch** — somewhere presence
genuinely changes — and would need range-gating over its UART to stop it announcing people the camera can't
yet see. The patch for it was removed once the hardware went back; `git log --diff-filter=D -- scripts/patches`
will find it if you ever want it again.

Two things to know before switching: 24 GHz **passes through plasterboard**, so expect to range-gate it over
its UART; and it reports *presence*, not *movement*, so it sits permanently HIGH in a room you occupy all day
and stops producing wake edges — great for a door, wrong for a desk. Both scripts spell this out when run.

---

**Idempotent:** each key is rewritten in place (ESC patches add a `# [<esc>]` provenance tag). **Steering
servo + drive motor** have no patch — they are continuous bench tuning, not a discrete config: set
`ROBOT_STEER_CENTER/LEFT/RIGHT` (servo) and `ROBOT_DRIVE_CRAWL_SPEED` / `ROBOT_MIN_FORWARD_SPEED` (motor) by hand
(see [config/README.md](../../config/README.md)). **Adding a new board?** add a `scripts/patches/<board>/`
folder and put its board-specific patches there (camera etc.); the `common/` ones apply unchanged. **Adding
hardware?** copy the closest script, rename it (`<order>_<axis>_<model>.sh`), and change the `set_kv` values.
