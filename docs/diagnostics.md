# Diagnostics

`./scripts/run_diagnostics.sh` tests every part of the built robot for real: the motor board, the sensors, the
camera, the speaker, the microphone, the face screen, the fan and finally the motor and steering. Each line gets
a tick or a cross, and the summary says what to fix and where.

```bash
./scripts/run_diagnostics.sh --wheels-off
```

**The motor test spins the wheels.** Keep them off the ground and a kill switch in reach. The motor runs last,
only after you confirm the wheels are off.

It always runs live, whatever `ROBOT_DRY_RUN` and `ROBOT_MOTORS_OFF` say, and first prints the sensor pins it's
about to use, so you can check them against your wiring.

## How it checks

Anything with a signal (the I²C board, the sensors, the camera, the microphone level, the screen) is checked
automatically. The Pi can't feel the motor, servo or speaker, so for those it acts and asks you
`did it work? [y/N]`. Each question skips itself after 12 s (`ROBOT_DIAG_TIMEOUT` changes that), so the run
always finishes. Parts you've turned off in `config/robot.env` show `[-]`, not a failure. Pictures and recordings
are saved in `~/.localairobot/diagnostics/`.

| Option | What it does |
|---|---|
| *(none)* | asks you to confirm each action |
| `--yes` | answers yes for you; skips the motor unless you add `--wheels-off` |
| `--wheels-off` | confirms in advance that the wheels are off the ground |
| `--no-motors` | skips the motor and steering tests |

## Sample output

```
-- Automated checks --
[v] ESC / PWM board present (PCA9685 @ I2C 0x40)  found 0x40 on bus 1
[v] Ultrasonic sensor FRONT (TRIG 23 / ECHO 24)  reading 47 cm
[x] Ultrasonic sensor LEFT (TRIG 5 / ECHO 6)  NO ECHO received -- check ECHO/TRIG pins & 3.3V wiring
[v] Camera picture (still)  41207 bytes -> ~/.localairobot/diagnostics/diag_picture.jpg
-- Checks that need you (listen / speak / watch) --
[v] Speaker output (TTS greeting)
[v] Microphone input (say 'hey jarvis')  level peak 47% | wake 'hey jarvis' DETECTED (score 0.71)
[v] Motor FORWARD spin
[v] Servo steer CENTER

Summary: 14 passed  1 failed  1 skipped
```

## When a check fails

| Failed check | Look at |
|---|---|
| ESC / PWM board | [PWM wiring](wiring/pwm-esc-servo-wiring.md), and `i2cdetect -y 1` should show `0x40` |
| motor or servo | [PWM wiring](wiring/pwm-esc-servo-wiring.md), then [calibration](calibration.md) |
| ultrasonic sensor | [ultrasonic wiring](wiring/ultrasonic-sensors-wiring.md) |
| speaker or microphone | [troubleshooting: sound](troubleshooting.md#sound) |
| wake word | [troubleshooting: wake word](troubleshooting.md#wake-word) |
| camera | `v4l2-ctl --list-devices`; reseat the cable or try another USB port |
| face screen | [troubleshooting: face screen](troubleshooting.md#face-screen) |
| enclosure fan | the fan wiring in [assembly](assembly.md), and `ROBOT_FAN_PIN` |

When everything passes, set the steering and throttle with [calibration.md](calibration.md).
