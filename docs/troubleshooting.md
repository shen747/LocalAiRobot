# Troubleshooting

Find your symptom, try the fix. Start with the self-test, `./scripts/run_diagnostics.sh`: it checks every part
and prints the fix for anything red ([diagnostics.md](diagnostics.md)). The robot's log is
`journalctl -u robot.service -f` (or `~/.localairobot/robot.log` for a `--detach` run).

## Sound

- **No sound, or `aplay: audio open error`:** run `./scripts/setup_audio.sh`. It finds your USB speaker, makes
  it the default output and sets the volume. Then test with `speaker-test -D default -t wav -l 1`.
- **The microphone records nothing:** check `ROBOT_MIC` points at it. `arecord -l` lists microphones;
  `setup_audio.sh` sets this for you.
- **Devices swap after a reboot:** USB audio is numbered in the order it's found. `setup_audio.sh` picks devices
  by name and runs before every start, so this fixes itself; if not, run it by hand.
- **Too quiet:** see [volume](configuration.md#volume).

## Wake word

- **It won't wake, or only sometimes:** almost always the microphone level. The log prints `best score` and
  `loudest` every 15 s or so; set the gain so `loudest` sits mid-scale with `amixer -c <card> sset Mic 75%`.
- **It wakes by itself:** set `ROBOT_WAKEWORD_TRIGGER_LEVEL=2`, or raise `ROBOT_WAKEWORD_THRESHOLD`.
- **Saying "hey jarvis" in `--dry` does nothing:** dry run fakes the wake word; type `r` and Enter instead.
- **It exits at start in live mode:** it can't load the wake word model. Check `ROBOT_WAKEWORD_MODEL`.

More in the [wake word guide](openwakeword/openwake-word.md).

## Camera

- **No `camera:` line at start:** the camera patch never ran, or `/dev/video0` isn't your webcam. List cameras
  with `v4l2-ctl --list-devices` and pass the right index to the patch.
- **Looks are slow:** check the camera really offers fast full HD: `v4l2-ctl --list-formats-ext -d /dev/video0`.
  You want MJPG 1920x1080 at 30 fps; some cheap modules only give YUYV at 5 fps at that size.
- **Upgraded from an old CSI-camera setup and the webcam is missing:** an old boot overlay may hold
  `/dev/video0`. Check with `grep -n 'LocalAiRobot camera' /boot/firmware/config.txt`; if it finds a line, run
  `ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh` and reboot.

## PIR motion sensor

- **No `PIR motion wake` line at start:** `ROBOT_PIR_PIN` is empty, or you skipped the reboot after installing
  (it adds you to the `gpio` group).
- **Nothing happens when you walk up:** check the sensor alone with `./scripts/pir_sensor_probe.sh`. A PaPIRs
  ignores everything for 210 s after power-on. See [PIR wiring](wiring/pir-sensor-wiring.md).
- **It wakes all the time with nobody there, or logs `PIR CHATTER`:** the sensitivity knob is up too far, the
  HC-SR501's jumper isn't on H, or it's aimed at something warm (the Pi's own vent, a heater, a sunny window).

The summary line, logged hourly, tells you whether the sensor or the face recognition is at fault:

```
concierge: last 60 min -- PIR 431 wake(s) (+402 held by cooldown, +96 debounce, +0 noise)
           | 428 silent (probe), 3 looked at, 1 lost in the window, 2 greeting(s) (1 by name)
```

Many `silent (probe)` and few `looked at` means the sensor fires at nothing. Many `looked at` and few
`greeting(s)` means face recognition is failing (next section).

## Face recognition

Read what it saw with `journalctl -u robot.service | grep -E 'concierge:|face_id:' | tail -30`, and look at the
saved frames in `~/detect-captures`.

- **Every score sits around 0.13 to 0.21 and the guessed name keeps changing:** the faces aren't being aligned.
  The start-up line should say `detector=yunet, 5-point ALIGNED`. If it says `UNALIGNED`, run
  `./scripts/provision_face_id.sh`, then `bash enroll_all.sh` to rebuild everyone's enrolment. Aligned matches
  usually score 0.45 to 0.60.
- **`motion but no face`:** the camera saw nobody. If you were there, the frame was too dark, or you were too
  far away for `ROBOT_FACE_ID_MIN_FACE_PX`.
- **`no match` with a big face box:** your enrolment photos don't cover that angle or light. Enrol more shots.
- **It says hello but never your name, and the log says `too close to call`:** two enrolled people look alike
  from there. Enrol better shots of both, or lower `ROBOT_FACE_ID_MIN_MARGIN`.
- **It greets an empty room:** something in view looks like a face. Check the `*-unknown.jpg` capture, then
  raise `ROBOT_FACE_ID_YUNET_SCORE` to 0.7 to 0.9 or `ROBOT_CONCIERGE_FACE_HITS` to 3.
- **Halloween never scares anyone:** the log says why. `the camera gave no picture` means the camera is down;
  `the person detector failed` means OpenCV can't run the model. See [halloween.md](halloween.md).

## Face screen

- **A DSI or HDMI screen stays blank:** the face only draws on a console boot, not the desktop. Run
  `sudo systemctl set-default multi-user.target` and reboot. The installer does this, but needs that reboot.
- **A USB-C panel stays blank:** check `lsusb | grep -i 1a86` and `ls /dev/ttyACM*`, log out and back in (for
  the `dialout` group), and try `ROBOT_FACE_REVISION=A` or `B`.

## Power

- **Speech cuts out mid-word, USB devices drop off, or the Pi freezes:** the Pi's supply is sagging. The log
  prints a `power:` warning, and `vcgencmd get_throttled` shows `0x50005`. On battery, feed the UPS HAT from an
  adjustable buck converter set to 7.4 V, not a fixed 5 to 6 V UBEC; see
  [assembly](assembly.md#2-power).

## Scripts

- **`command not found` for a script:** it lost its executable bit (Windows checkouts do this). Run it with
  `bash ./scripts/name.sh`, or fix them all with `chmod +x scripts/*.sh`.
- **`purge_garbage.sh` reports success but deleted nothing:** you ran it with `sudo`. Run it as yourself.

## More detail in the log

Turn on full detail while you chase something, then turn it off again: it costs disk space, and keeping every
frame fills `~/detect-captures` fast.

```bash
sed -i -E -e 's|^(ROBOT_LOG_LEVEL=)[^[:space:]]*|\1DEBUG|' \
          -e 's|^(ROBOT_SENSOR_VERBOSE=)[^[:space:]]*|\11|' \
          -e 's|^(ROBOT_CONCIERGE_SUMMARY_S=)[^[:space:]]*|\1300|' \
          -e 's|^(ROBOT_DETECT_CAPTURE_MODE=)[^[:space:]]*|\1all|' config/robot.env
./scripts/run_me.sh --reload
```

That logs every PIR edge, writes the summary every 5 minutes and keeps every frame. To turn it off, run the same
command with `INFO`, `0`, `3600` and `greeted`, reload, then `./scripts/purge_detect_captures.sh`.

To copy the log to your PC (it gets big; the second form keeps only events):

```bash
ssh <user>@<robot>.local 'journalctl -u robot.service --no-pager' > robot-all.log
ssh <user>@<robot>.local "journalctl -u robot.service --no-pager | grep -aE 'camera:|PIR|concierge:|face_id:'"
```

## Recovery

- **Lost Wi-Fi or SSH:** [change-pi-wifi.md](change-pi-wifi.md) sets up a phone-hotspot fallback and a portal
  for joining new networks without a screen.
- **Read the disk from a PC when the Pi won't boot:** put the drive in a USB enclosure, then in PowerShell as
  Administrator run `wsl --mount \\.\PHYSICALDRIVE1 --partition 2 --type ext4 --options ro` (find the number
  with `Get-Disk`). Copy out `home/<user>/LocalAiRobot/config/robot.env` and `home/<user>/.localairobot/memory.db`,
  then `wsl --unmount \\.\PHYSICALDRIVE1`.
- **Is the drive dead?** With it on a PC: `sudo nvme smart-log /dev/sdX` and `sudo badblocks -sv -b 4096 /dev/sdX`.
  Errors mean replace it. Clean results point at the Pi: its power supply, the NVMe ribbon, or
  `dtparam=pciex1_gen=3` in `config.txt` (set 2 or remove it).
- **Make a full disk image:** run `sudo fsck -fy` first, then use Rescuezilla or `dd` on a PC, imaging the
  whole disk. The image holds your keys, Wi-Fi password and memory, so store it like a password. Restoring it to
  a second Pi? Give that one new SSH host keys:
  `sudo rm -f /etc/ssh/ssh_host_* && sudo dpkg-reconfigure openssh-server`.
- **Start over:** `./scripts/cleanup_pi.sh -n` lists what the installer added; `./scripts/cleanup_pi.sh` removes
  it after asking (`--deep` also removes its packages, `--repo` the clone). Re-flashing the SD card is the
  cleanest reset.
