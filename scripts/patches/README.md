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
  pi5/      3_camera_imx519 / _ov5647 / _cm3 / _usb_webcam
  jetson/   3_camera_usb_webcam (default) / 3_camera_imx519_csi
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
```bash
# Raspberry Pi 5 (pi5/) -- these set the boot overlay + AF tuning:
./scripts/patches/pi5/3_camera_imx519.sh       # Arducam IMX519 16MP autofocus (the reference) -- ALSO installs the AF tuning
./scripts/patches/pi5/3_camera_ov5647.sh       # fixed-focus 5MP OV5647 (auto-detected)
./scripts/patches/pi5/3_camera_cm3.sh          # official Camera Module 3 / IMX708 (first-class autofocus)
./scripts/patches/pi5/3_camera_usb_webcam.sh   # USB webcam (v4l2) instead of the CSI cam -- installs OpenCV; no ribbon/overlay/AF fork

# Jetson (jetson/):
./scripts/patches/jetson/3_camera_usb_webcam.sh   # USB webcam (v4l2) -- the Jetson DEFAULT / easiest path
./scripts/patches/jetson/3_camera_imx519_csi.sh   # Arducam IMX519 (CSI/gstreamer) -- prints the separate Arducam Jetson driver step
```
The Pi's three CSI patches set `ROBOT_CAMERA_AUTOFOCUS` and print the boot-overlay reinstall command (that
step needs `sudo` + a reboot). `pi5/3_camera_imx519.sh` additionally installs Arducam's autofocus tuning
(their libcamera fork) — without it the IMX519 runs fixed-focus (`Could not set AF_MODE`).
`pi5/3_camera_usb_webcam.sh` is the exception: it leaves the CSI/picamera2 world entirely, sets
`ROBOT_CAMERA_BACKEND=v4l2` and installs OpenCV (`cv2`, which the CSI path doesn't need) — no overlay, no AF
fork; it just prints how to free `/dev/video0` if you're coming from a CSI build. The Jetson camera is
**not** a boot overlay — a USB webcam just works (`v4l2`), and the IMX519 CSI needs Arducam's separate,
JetPack-version-specific driver (the `jetson/3_camera_imx519_csi.sh` patch prints it).

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
recognition model. Then enrol people with `./scripts/enroll_face.sh "Name"` and set `ROBOT_CONCIERGE_MODE=1`.
Full flow: [docs/concierge.md](../../docs/concierge.md).

---

**Idempotent:** each key is rewritten in place (ESC patches add a `# [<esc>]` provenance tag). **Steering
servo + drive motor** have no patch — they are continuous bench tuning, not a discrete config: set
`ROBOT_STEER_CENTER/LEFT/RIGHT` (servo) and `ROBOT_DRIVE_CRAWL_SPEED` / `ROBOT_MIN_FORWARD_SPEED` (motor) by hand
(see [config/README.md](../../config/README.md)). **Adding a new board?** add a `scripts/patches/<board>/`
folder and put its board-specific patches there (camera etc.); the `common/` ones apply unchanged. **Adding
hardware?** copy the closest script, rename it (`<order>_<axis>_<model>.sh`), and change the `set_kv` values.
