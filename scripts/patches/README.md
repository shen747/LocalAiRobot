# Hardware patches

Each script writes the right settings into `config/robot.env` for one piece of hardware, then tells you about
any follow-up step. Run them after the installer. They're safe to run again and only change their own settings.
If `config/robot.env` doesn't exist yet, they create it from the template.

The number is the order to run them in. Pick one script per number: a second ESC or camera patch overwrites
the first. The camera patch comes from your board's folder (`pi5/` or `jetson/`); everything else is in
`common/`.

## 1. ESC

```bash
./scripts/patches/common/1_esc_hb_qr_1060.sh   # HobbyWing QuicRun WP-1060 (also sets ROBOT_ESC_CHANNEL=14)
./scripts/patches/common/1_esc_uliik_60a.sh    # ULIIK 60A: safe starting values, calibrate before trusting
```

To tune the throttle values, see [calibration](../../docs/calibration.md).

## 2. Obstacle sensors

```bash
./scripts/patches/common/2_sensor_hcsr04.sh    # 3x HC-SR04P ultrasonic (the default)
./scripts/patches/common/2_sensor_vl53l1x.sh   # 3x VL53L1X laser; then set ROBOT_TOF_XSHUT_* to your wiring
```

## 3. Camera

A USB camera on either board. No boot settings and no reboot: `./scripts/run_me.sh --reload` is enough. The
optional argument is the camera's index (find it with `v4l2-ctl --list-devices`).

```bash
./scripts/patches/pi5/3_camera_usb_webcam.sh 0      # any USB webcam (Jetson: jetson/3_camera_usb_webcam.sh)
./scripts/patches/pi5/3_camera_usb_ir_1080p.sh 0    # a 1080p IR night-vision module (Jetson: jetson/...)
```

The IR patch also asks for 1920x1080 and turns autofocus off, since its lens is fixed. At night its pictures are
greyscale infrared, so enrol a few night photos of each person too. CSI ribbon cameras still work if you set
`ROBOT_CAMERA_BACKEND=picamera2` by hand, but they have no patch.

## 4. Optional sensors

```bash
./scripts/patches/common/4_imu_bno085.sh       # BNO085 compass and gyro (installs its library)
./scripts/patches/common/4_encoder_as5600.sh   # AS5600 wheel encoder (installs its library)
```

These add the readers and settings only. Clear `ROBOT_IMU_TYPE` or `ROBOT_ENCODER_TYPE` to turn them off.

## 5. Face recognition

```bash
./scripts/patches/common/5_face_id_insightface.sh   # fetch the face models again (the installer already does)
```

## 6. Motion sensor (Concierge)

```bash
./scripts/patches/common/6_motion_pir_hcsr501.sh   # hobby HC-SR501 (the defaults already match it)
./scripts/patches/common/6_motion_pir_papirs.sh    # Panasonic PaPIRs
./scripts/patches/common/6_wake_zigbee_pir.sh zigbee2mqtt/pir_hallway   # extra wireless sensor; --off undoes it
```

**A PaPIRs runs on 3.3 V, and 5 V destroys it.** Wiring, aiming and testing for both sensors:
[PIR wiring](../../docs/wiring/pir-sensor-wiring.md). Pass a BCM pin number to use a pin other than GPIO 4. The
wireless sensor adds to the wired one and needs Zigbee2MQTT first: [zigbee-pir.md](../../docs/zigbee-pir.md).

## No patch needed

Steering angles and drive speeds are bench tuning rather than a part choice: set `ROBOT_STEER_*` and
`ROBOT_DRIVE_CRAWL_SPEED` by hand ([calibration](../../docs/calibration.md)).

## Adding your own

Copy the closest script, name it `<order>_<part>_<model>.sh`, and change its `set_kv` lines. A new board gets
its own folder for board-specific patches; the `common/` ones work unchanged.
