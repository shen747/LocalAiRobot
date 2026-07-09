# Hardware config patches

Each script here applies one hardware choice to `config/robot.env` in **one command** (instead of hand-editing
keys or remembering `sed` lines), then prints any follow-up (a boot-overlay reinstall, a `pip install`, a
bench step). All are **idempotent** (re-runnable) and only touch the keys they own. Run the ones matching
your build **after** `install_stack_pi5.sh` / `install_stack_jetson.sh`. Full rationale + the either/or
hardware options + per-key rationale: [`config/README.md`](../../config/README.md).

## ESC (drive)
```bash
./scripts/patches/hb_qr_1060.sh       # HobbyWing QuicRun WP-1060  (F/R jumper: Mode cap OFF)  [sets ROBOT_ESC_CHANNEL=14 for one build]
./scripts/patches/hb_qr_1080_g2.sh    # HobbyWing QuicRun WP-1080-G2 (F/R; leaves ROBOT_ESC_CHANNEL at default ch0)
```

## Obstacle sensors
```bash
./scripts/patches/sensor_hcsr04.sh    # 3x HC-SR04P ultrasonic (the default)
./scripts/patches/sensor_vl53l1x.sh   # 3x VL53L1X laser ToF (I2C) -- set the ROBOT_TOF_XSHUT_* pins to your wiring
```

## Camera
```bash
./scripts/patches/camera_imx519.sh    # Arducam IMX519 16MP autofocus (the reference)
./scripts/patches/camera_ov5647.sh    # fixed-focus 5MP OV5647 (auto-detected)
./scripts/patches/camera_cm3.sh       # official Camera Module 3 / IMX708 (first-class autofocus)
```
Camera patches set `ROBOT_CAMERA_AUTOFOCUS` and print the boot-overlay reinstall command (that step needs
`sudo` + a reboot, so it isn't done automatically).

## Optional add-on sensors (off by default)
```bash
./scripts/patches/imu_bno085.sh       # enable a BNO085 9-DOF IMU (heading/yaw) -- also pip-installs its lib
./scripts/patches/encoder_as5600.sh   # enable an AS5600 magnetic angle encoder -- also pip-installs its lib
```
These land the reader + config only (see `brain/imu.py` / `brain/encoder.py`); disable by clearing
`ROBOT_IMU_TYPE` / `ROBOT_ENCODER_TYPE`.

---

**Idempotent:** each key is rewritten in place (ESC patches add a `# [<esc>]` provenance tag). **Steering
servo + drive motor** have no patch — they are continuous bench tuning, not a discrete config: set
`ROBOT_STEER_CENTER/LEFT/RIGHT` (servo) and `ROBOT_DRIVE_CRAWL_SPEED` / `ROBOT_ESC_FWD_FLOOR` (motor) by hand
(see [config/README.md](../../config/README.md)). **Adding hardware?** copy the closest script, rename it
(`<axis>_<model>.sh`), and change the `set_kv` values.
