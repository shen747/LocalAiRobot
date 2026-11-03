# Install

This guide takes you from a blank SD card to a robot that talks back. Most of the time goes on downloads.

You need a PC, a Raspberry Pi 5 with its active cooler, a microSD card (64 GB or more), the official 27 W
power supply, a USB microphone and a USB speaker. The motor, sensors and camera can wait until step 9.

On a Jetson Orin Nano, JetPack 6 replaces steps 1 and 2, and step 4 uses `./scripts/install_stack_jetson.sh`.
The Jetson pin and bus settings are in [config/README.md](../config/README.md#board).

## 1. Fit the cooler and flash the card

Fit the active cooler before anything else: thermal pad on the chip, the two spring pins into the board, and
its cable into the 4-pin fan header. Without it the Pi slows itself down under the language model.

On your PC, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and choose
**Raspberry Pi OS Lite (64-bit)**. In its settings:

- set a username and password (you log in with these),
- turn on SSH,
- enter your Wi-Fi name and password, and your locale.

Write the card and put it in the Pi.

## 2. Boot and log in

Power the Pi from the 27 W supply, not a battery yet. Wait a minute, then on your PC:

```bash
ssh <your-username>@raspberrypi.local
```

If `raspberrypi.local` doesn't resolve, use the Pi's IP address from your router's device list. Windows 10
and later have `ssh` built in. From here on, every command runs on the Pi, in this SSH session.

## 3. Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

## 4. Install the robot

Plug in the USB microphone and speaker first, so the installer can set them up.

```bash
git clone https://github.com/shen747/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot
./scripts/install_stack_pi5.sh
```

It installs Ollama with the Gemma and moondream models, whisper.cpp, Piper, the face-recognition models and
the Python environment, and creates your settings file, `config/robot.env`. Say yes when it offers to reboot,
then log back in and `cd ~/LocalAiRobot`. Running the installer again is safe: it skips what's already there.

## 5. Check the brain

```bash
ollama list                                            # expect gemma4:e2b-it-qat and moondream
ollama run gemma4:e2b-it-qat "reply with one word: ready"
```

The first answer is slow while the model loads. Want proof it works offline? Turn Wi-Fi off and ask again.
`vcgencmd measure_temp` should stay under 80 °C.

## 6. Check the sound

```bash
speaker-test -D default -t wav -l 1        # you should hear "Front Left"
arecord -D "$ROBOT_MIC" -d 3 -f S16_LE -r 16000 -c 1 -V mono /tmp/test.wav && aplay /tmp/test.wav
```

The level bar should jump while you talk, then you hear yourself played back. Silence? Run
`./scripts/setup_audio.sh` and see [troubleshooting](troubleshooting.md#sound).

## 7. Talk to it on the bench

```bash
./scripts/run_me.sh --dry
```

Dry run fakes the motor and sensors, so nothing can move. Press Enter, say "go forward and bear left", and the
log shows what it would drive:

```
Heard: "go forward and bear left"
robot.hw INFO I2C->PCA9685 ESC   ch=0 throttle=+0.140 pulse_us=1570
robot.hw INFO I2C->PCA9685 SERVO ch=1 steer=LEFT   angle=55.0deg pulse_us=1111
```

Ask it a question and it answers out loud. Type `r` and Enter to pretend you said the wake word, and `q` and
Enter to quit.

## 8. Wake it by voice

```bash
./scripts/run_me.sh --live
```

Say "hey jarvis", wait for it to answer "Yes?", then give a command. Live mode only listens for the wake word.
With no motor board connected it logs that and carries on without driving. Ctrl-C stops it. If it won't wake,
it's usually the microphone level: see [troubleshooting](troubleshooting.md#wake-word).

## 9. Add your hardware

Build the robot with [assembly.md](assembly.md), or wire the PIR and camera for the greeter
([concierge.md](concierge.md)). Then tell the software what you have. Each patch writes the right settings:

```bash
./scripts/patches/common/1_esc_hb_qr_1060.sh   # HobbyWing QuicRun WP-1060 ESC (or 1_esc_uliik_60a.sh)
./scripts/patches/pi5/3_camera_usb_webcam.sh   # any USB webcam (or 3_camera_usb_ir_1080p.sh)
```

Run one patch per part: a second ESC or camera patch overwrites the first. Other sensors, an IMU or a wheel
encoder each have a patch too, see the [patch list](../scripts/patches/README.md).

Now check every part, with the ESC switched on and **the wheels off the ground**:

```bash
./scripts/run_diagnostics.sh --wheels-off
```

Each part gets a green tick, or a red cross with the fix. [diagnostics.md](diagnostics.md) explains the
output. Then set the steering and throttle with [calibration.md](calibration.md).

## 10. Start on every boot

```bash
./scripts/run_me.sh --install-boot
sudo reboot
```

The robot now starts by itself and restarts if it crashes. Watch its log with `journalctl -u robot.service -f`,
and turn auto-start off with `./scripts/run_me.sh --disable-boot`.

**Keep a physical kill switch on the motor battery.** The software stops the motor on errors and obstacles,
but if the program is killed, the motor board keeps its last command.

## Everyday commands

| Command | What it does |
|---|---|
| `./scripts/run_me.sh` | Run in the foreground (add `--dry` or `--live` to choose). Ctrl-C stops it. |
| `./scripts/run_me.sh --detach` | Run in the background; `./scripts/shutdown.sh` stops it |
| `./scripts/run_me.sh --reload` | Apply an edited `config/robot.env` to the running robot |
| `./scripts/shutdown.sh --poweroff` | Stop the robot and power off the Pi |
| `ROBOT_MOTORS_OFF=1` in `config/robot.env` | Run live on the built car without the wheels moving |

## Next

- Choose what it is: [crawler](crawler.md), [concierge](concierge.md) or [Halloween](halloween.md).
- Change settings: [configuration.md](configuration.md).
- Get new code later: [updating.md](updating.md).
- Something wrong: [troubleshooting.md](troubleshooting.md).
- Moving to a new Wi-Fi network without a screen: [change-pi-wifi.md](change-pi-wifi.md).
