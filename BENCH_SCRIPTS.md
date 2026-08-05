# Bench scripts & operator runbook

Two halves:

- **[Part 1 — Bench calibration](#part-1--bench-calibration)** (§1–4): scripts that bypass the brain and talk
  **straight to the hardware** so you can sweep values and read off the numbers the robot needs.
- **[Part 2 — Operator runbook](#part-2--operator-runbook)** (§5–12): bringing a board up from a fresh OS,
  the two quick-config recipes, the update loop, enrolling faces, pulling logs, and disaster recovery.

> **Placeholders.** Part 2 uses `<angle_brackets>` for anything private — `<gemini_api_key>`, `<robot-host>`,
> `<user>`, `<hotspot_ssid>`, `<hotspot_password>`, `<github_user>`. Substitute your own; **never commit real
> keys or passwords.** Keep them in a git-ignored notes file (see §5 step 4 for how to ignore one locally).

---

## Part 1 — Bench calibration

Small scripts that bypass the brain and talk **straight to the hardware** (PCA9685 for the ESC/servo, Piper
for the voice) so you can sweep values / try spellings, judge the result on the spot, then read off the exact
numbers or respelling the robot needs. Use them **on the bench** — the drive ones before the first drive (and
after swapping the ESC, servo, or motor); the voice one whenever a name comes out wrong.

They cover the **ESC (throttle)**, the **steering servo**, and **Piper voice pronunciation** (§4). Other
subsystems (sensors, camera, audio routing) have their own tools (`scripts/run_diagnostics.sh`,
`scripts/ultrasonic_sensor_probe.sh`).

> 🔴 **WHEELS OFF THE GROUND.** The ESC must be **powered on**; hold neutral and wait for the long *"ready"*
> beep (that's the ESC **arming**). `Ctrl-C` stops any script. Run over SSH with the repo's venv python.

## Where the numbers go once you find them

| You measured… | Put it in… | Key |
|---|---|---|
| neutral — wheels **dead still** + the long ready beep | your **ESC patch** | `ROBOT_ESC_NEUTRAL` |
| slowest **forward** that just creeps | your ESC patch | `ROBOT_MIN_FORWARD_SPEED` |
| a good **forward top speed** | your ESC patch | `ROBOT_MAX_FORWARD_SPEED` |
| slowest **reverse** that just creeps | your ESC patch | `ROBOT_MIN_REVERSE_SPEED` |
| a good **reverse top speed** | your ESC patch | `ROBOT_MAX_REVERSE_SPEED` |
| wheels **dead ahead** angle | `config/robot.env` | `ROBOT_STEER_CENTER` |
| furthest **left / right** that turns fully without buzzing | `config/robot.env` | `ROBOT_STEER_LEFT` / `ROBOT_STEER_RIGHT` |

**ESC values live in the patch, not `config/robot.env`.** There are two supported ESCs — run one after
editing its values: `scripts/patches/common/1_esc_hb_qr_1060.sh` or `1_esc_uliik_60a.sh`. (Steering values
*do* go straight into `config/robot.env`.)

## How the throttle number maps to the ESC

`throttle` is `-1.0 … +1.0`, written as a pulse: **`pulse_us = 1500 + throttle × 500`**.

- `0.00` = 1500 µs (center) · `+` = forward · `−` = reverse.
- Many crawler ESCs have an **offset neutral** — the WP-1060 arms/stops at **`+0.25` (1625 µs)**, not `0.0`.
- Forward only pulls **above a dead-band** (WP-1060: ~`+0.55`/1775 µs). Reverse engages **just below the 1500 µs
  center**: `0.00`/1500 µs sits **on** the boundary and does **nothing**, but `−0.01`/1495 µs already creeps back
  (both field-confirmed). The offset neutral (1625 µs) + the forward dead-band leave a dead zone ~1500–1775 µs.
  **More negative = faster reverse.**

---

## 1. ESC — quick forward / reverse test

Arm, one forward move, stop, one reverse move, stop. Use it to confirm the ESC **arms** and that your
chosen crawl values actually move the wheels. Tweak the three values at the top and re-run.

```bash
brain/.venv/bin/python - 14 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

# ---- tweak these (throttle -1..1; 0.0 = 1500us neutral, + = forward, - = reverse) ----
NEUTRAL = 0.25     # arms + STOPS (this ESC's true neutral; WP-1060 is offset to ~+0.25 / 1625us)
FORWARD = 0.55     # slowest forward crawl (won't PULL below ~0.55 / 1775us -- the dead-band)
REVERSE = -0.01    # slowest reverse that engages -- just past the 1500us center (0.00 sits ON the boundary = no motion)
ARM_S   = 2.0      # hold NEUTRAL this long to ARM (ESC powered ON -> wait for the long 'ready' beep)
MOVE_S  = 2.0      # how long each move runs
# --------------------------------------------------------------------------------------

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 14
print(f">> PCA9685 channel = {ch}.  Wheels OFF the ground.")
esc = ServoKit(channels=16).continuous_servo[ch]
esc.set_pulse_width_range(1000, 2000)

def hold(v, label, secs):
    print(f">> {label:14s} ch={ch:2d}  throttle={v:+.2f}  ({1500 + v*500:4.0f}us)  {secs}s")
    esc.throttle = v
    time.sleep(secs)

hold(NEUTRAL, "ARM/neutral", ARM_S)    # holding neutral is what arms the ESC
hold(FORWARD, "FORWARD",     MOVE_S)
hold(NEUTRAL, "STOP",        MOVE_S)
hold(REVERSE, "REVERSE",     MOVE_S)
hold(NEUTRAL, "STOP",        1.0)
print("done -- tweak the values up top and re-run to find your crawl points.")
PYEOF
```

## 2. ESC — calibration sweep (find the crawl + top-speed points)

Sweeps the throttle in `0.05` steps so you can **see** where the wheels first creep and where they reach a
comfortable speed. Forward first (up from neutral), then reverse (down through and below neutral). Note the
throttle printed at each step: the first that moves = your **MIN**, a comfortable one = your **MAX**.

```bash
brain/.venv/bin/python - 14 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

NEUTRAL = 0.25          # your ESC's arm/stop point (sweep to where wheels sit DEAD STILL + the long beep)
STEP, HOLD_S = 0.05, 1.2
FWD_MAX, REV_MAX = 0.95, -0.30      # how far each sweep goes (reverse engages just under 0.00; -0.30 covers crawl..fast)

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 14
esc = ServoKit(channels=16).continuous_servo[ch]
esc.set_pulse_width_range(1000, 2000)

def hold(v, label, secs):
    print(f">> {label:16s} ch={ch:2d}  throttle={v:+.2f}  ({1500 + v*500:4.0f}us)  {secs}s")
    esc.throttle = v; time.sleep(secs)

print(f">> ESC sweep on ch{ch}. Wheels OFF. Program card = Forward/Reverse.")
hold(NEUTRAL, "ARM/neutral", 5.0)   # POWER-CYCLE the ESC NOW; cell tones -> a long 'ready' beep
print(">> FORWARD sweep up -- note where it JUST creeps (MIN_FORWARD) and a good top speed (MAX_FORWARD):")
v = NEUTRAL
while v <= FWD_MAX + 1e-9:
    hold(round(v, 2), f"fwd {v:+.2f}", HOLD_S); v += STEP
hold(NEUTRAL, "STOP", 1.0)
print(">> REVERSE sweep down -- note where it JUST creeps (MIN_REVERSE) and a good speed (MAX_REVERSE):")
v = NEUTRAL
while v >= REV_MAX - 1e-9:
    hold(round(v, 2), f"rev {v:+.2f}", HOLD_S); v -= STEP
hold(NEUTRAL, "STOP", 1.0)
print("done -- put these in your ESC patch: ROBOT_ESC_NEUTRAL, ROBOT_MIN/MAX_FORWARD_SPEED, ROBOT_MIN/MAX_REVERSE_SPEED.")
PYEOF
```

## 3. Steering servo — calibration sweep (find center / left / right)

Shows your **currently saved** angles first (judge if they're already good), then slow-sweeps toward each
lock so you can read the exact angle where the wheels point **dead ahead** and the **furthest each way that
turns fully without the servo buzzing/binding**. `Ctrl-C` the instant it buzzes.

```bash
brain/.venv/bin/python - 1 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

# ---- adjust these in ONE place (degrees) ----
CENTER = 95     # wheels dead ahead
LEFT   = 135    # full LEFT lock
RIGHT  = 30     # full RIGHT lock
MARGIN = 5      # degrees to probe just PAST each lock (to find the true limit)
STEP   = 5      # sweep granularity, degrees
# ---------------------------------------------

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 1          # your steering channel
kit = ServoKit(channels=16); srv = kit.servo[ch]
srv.set_pulse_width_range(500, 2500)   # SAME as the driver -> angles map 1:1 to ROBOT_STEER_*
srv.actuation_range = 180

def go(a, label, hold=1.0):
    a = max(0.0, min(180.0, a))
    print(f">> {label:22s} angle={a:5.1f}deg ({500 + a/180*2000:4.0f}us)")
    srv.angle = a; time.sleep(hold)

def sweep(target):
    # step from CENTER to just past `target` so you can read off the exact angle
    end = target - MARGIN if target < CENTER else target + MARGIN
    d = STEP if end >= CENTER else -STEP
    a = CENTER
    while (a <= end) if d > 0 else (a >= end):
        go(a, f"  {a}")
        a += d

print(f">> STEERING sweep on ch {ch}. Watch the wheels. Ctrl-C the moment it BUZZES/BINDS.\n")
go(CENTER, f"current CENTER ({CENTER})", 1.5)
go(LEFT,   f"current LEFT ({LEFT})",     1.5)
go(RIGHT,  f"current RIGHT ({RIGHT})",   1.5)
go(CENTER, "back to centre", 1.2)
print(f">> toward LEFT  (~{LEFT}, +{MARGIN} past):");  sweep(LEFT)
go(CENTER, "back to centre", 1.2)
print(f">> toward RIGHT (~{RIGHT}, +{MARGIN} past):"); sweep(RIGHT)
go(CENTER, "park at centre", 0.5)
print("\ndone. Set ROBOT_STEER_CENTER / _LEFT / _RIGHT in config/robot.env (angles map 1:1).")
PYEOF
```

---

**After calibrating:** put the ESC numbers in your ESC patch and run it
(`./scripts/patches/common/1_esc_hb_qr_1060.sh`), put the steering angles in `config/robot.env`, then verify
end-to-end with `./scripts/run_diagnostics.sh --wheels-off`.

---

## 4. Piper voice — dial in a name's pronunciation

Piper mis-says some names (it reads *"Ramesh"* as *"RAM-esh"*, or spells tricky ones out letter-by-letter).
This one isn't a paste-in bench — it's a committed script, **`./scripts/pronounce_my_name.sh`** — because you
re-run it a lot while tuning a name. Give it a phonetic **respelling** and it speaks it in Jarvis's own voice,
once bare and once inside a greeting, so what you hear is exactly what a visitor hears:

```bash
./scripts/pronounce_my_name.sh Ruh-mesh                 # just listen (bare, then in a greeting)
./scripts/pronounce_my_name.sh Ruh-mesh --name=Ramesh   # ...and save it as Ramesh's say-as in enroll_all.sh
```

With `--name=<Name>` it also rewrites that person's **say-as** (the 3rd field) in `~/LocalAiRobot/enroll_all.sh`,
failing clearly if the name isn't enrolled there (it lists who is) or the file is missing. Re-run
`bash enroll_all.sh` on the Pi afterwards to push it into the RAG db. No wheels, no ESC — just a speaker (same
ALSA default as the robot voice; run `./scripts/setup_audio.sh` first if you hear nothing).

> 💡 Respell by **sound**, joined with hyphens (`Ruh-mesh`, `Shah-nee`). **Never space out single letters**
> (`R uh m e sh`) — Piper's espeak phonemizer reads lone letters as the alphabet and spells the name out.
> (Want it fixed *everywhere* the robot says the name, not just one greeting? Put it in `config/robot.env`
> instead: `ROBOT_TTS_PRONUNCIATIONS="Ramesh=Ruh-mesh"`.)

---

# Part 2 — Operator runbook

Everything below runs **on the robot** unless a step says *ON YOUR PC*. The robot is referred to as
`<user>@<robot-host>.local` — use the **hostname**, not an IP: avahi resolves it and it survives DHCP changes.

| § | What | When |
|---|---|---|
| [Volume](#volume--live-no-restart) | Change the speaker volume on a running robot | anytime |
| [5](#5-first-contact-a-fresh-board) | First contact — OS update, git identity, SSH key | once per board |
| [6](#6-quick-config--crawler-car) | Quick config: **crawler car** (it drives) | once, then rarely |
| [7](#7-quick-config--concierge) | Quick config: **concierge** (stationary greeter) | once, then rarely |
| [8](#8-the-update-loop) | **The update loop** — pull → purge → re-enrol → reboot | every code change |
| [9](#9-enrolling-faces) | Enrolling faces | whenever people change |
| [10](#10-pulling-logs) | Pulling logs & the one diagnostic line | when something's wrong |
| [11](#11-spoken-earcons) | Regenerating the spoken earcons | when the word list changes |
| [12](#12-recovery) | Recovery — Wi-Fi profile, offline disk read, golden image | when it breaks |

## Volume — live, no restart

The one you'll reach for most. On the robot, from `~/LocalAiRobot`:

```bash
sed -i 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=100|' config/robot.env
./scripts/setup_audio.sh
```

`setup_audio.sh` runs `amixer sset` against the **live** device, so it takes effect immediately — nothing
to restart. `ROBOT_SPEAKER_VOLUME` is the USB-speaker mixer percentage, 0–100; anything outside that range
is rejected with a message and 100 used instead, because a bad value makes every `amixer sset` fail and
merely *looks* like "this speaker has no volume control". Reference builds: crawler 100, concierge 75.

It also survives reboots — `run_me.sh` calls `setup_audio.sh` before every launch. That's not redundancy:
USB audio resets its own mixer to 0/low on a power cycle, **and** ALSA card ids are enumeration-order
dependent, so the speaker and mic can swap places between boots.

```bash
grep -E '^ROBOT_SPEAKER_VOLUME=' config/robot.env    # confirm what took
```

Still too quiet at 100? You're out of software headroom — what's left is the speaker's own hardware gain:

```bash
aplay -l              # find the card number
alsamixer -c 1        # raise the device's own control, Esc to leave
```

## 5. First contact (a fresh board)

Do this **before** §6/§7. `install_stack` runs apt itself and wants a current index, and **step 4 is the
clone** that every other section assumes already exists (`cd ~/LocalAiRobot` opens most of them).

```bash
# 1. Update the OS (can take a while on a fresh image)
sudo apt update && sudo apt upgrade -y
```

> ℹ️ Nothing here depends on libcamera any more. The supported cameras are **USB (UVC)**, which need no boot
> overlay, no vendor driver and no autofocus tuning — so an `apt upgrade` can't quietly change what the camera
> does. (This used to be a live hazard: an upgrade could replace libcamera and silently revert the Arducam
> IMX519 autofocus fork, leaving every frame fixed-focus and face scores stuck around 0.14–0.24.)

```bash
# 2. Git identity + rebase-on-pull
git config --global user.email "<your_email>"
git config --global user.name  "<Your Name>"
git config --global pull.rebase true

# 3. SSH key so the robot can pull/push without a password (empty passphrase = unattended git)
ssh-keygen -t ed25519 -C "<user>@<robot-host>"    # Enter x3
cat ~/.ssh/id_ed25519.pub                         # -> GitHub > Settings > SSH and GPG keys > New key
ssh -T git@github.com                             # "Hi <user>! You've successfully authenticated"

# Cloned over HTTPS? point the remote at SSH so pushes stop asking for a token:
git -C ~/LocalAiRobot remote set-url origin git@github.com:<github_user>/LocalAiRobot.git

# 4. CLONE THE REPO -- nothing in §6/§7 works before this; they all start with `cd ~/LocalAiRobot`
git clone git@github.com:<github_user>/LocalAiRobot.git ~/LocalAiRobot
cd ~/LocalAiRobot

# 5. Ignore a file LOCALLY, with no .gitignore commit -- this is where a private notes/keys file goes:
nano .git/info/exclude
```

`config/robot.env` arrives as the **committed template** with an empty `ROBOT_GEMINI_API_KEY` — the `sed`
block in §6/§7 step 2 is what fills in the real key. There is nothing else to set up by hand.

> **Restoring a cloned disk image onto a second board?** Regenerate the **host** keys (a different thing from
> the key above) or every SSH client will scream about a duplicate fingerprint:
> `sudo rm -f /etc/ssh/ssh_host_* && sudo dpkg-reconfigure openssh-server`

## 6. Quick config — crawler car

The mobile build: ESC + sensors, USB-C face panel. From `~/LocalAiRobot`, in order.

```bash
# 0. Get the code FIRST -- install_stack installs deps against whatever checkout is already there
cd ~/LocalAiRobot && git pull

# 1. Install the stack -- run the ONE for your board (needs internet; re-runnable)
./scripts/install_stack_pi5.sh              # Raspberry Pi 5
# ./scripts/install_stack_jetson.sh         # Jetson Orin Nano

# 2. Config -- one block. `turing` = the USB-C serial panel; `framebuffer` is the concierge's DSI/HDMI screen.
sed -i -e 's|^ROBOT_AI_BACKEND=.*|ROBOT_AI_BACKEND=mixed|' \
       -e 's|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=<gemini_api_key>|' \
       -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=0|' \
       -e 's|^ROBOT_FACE_BACKEND=.*|ROBOT_FACE_BACKEND=turing|' \
       -e 's|^ROBOT_FOLLOWUP_SILENCE_S=.*|ROBOT_FOLLOWUP_SILENCE_S=20|' \
       -e 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=100|' \
       config/robot.env

grep -E '^(ROBOT_AI_BACKEND|ROBOT_CONCIERGE_MODE|ROBOT_FACE_BACKEND|ROBOT_SPEAKER_VOLUME)=' config/robot.env

# 3. ESC patch -- REQUIRED before it drives (writes the throttle map + ROBOT_ESC_CHANNEL)
./scripts/patches/common/1_esc_hb_qr_1060.sh      # HobbyWing QuicRun WP-1060 (Mode cap OFF, LIPO cap ON)
# ./scripts/patches/common/1_esc_uliik_60a.sh     # ULIIK 60A instead

# 4. Any OTHER hardware patch you actually use -- ONE per part (catalog: scripts/patches/README.md)
# ./scripts/patches/common/2_sensor_hcsr04.sh     # 3x HC-SR04 ultrasonics
# ./scripts/patches/common/2_sensor_vl53l1x.sh    # VL53L1X ToF instead
# ./scripts/patches/common/4_imu_bno085.sh        # BNO085 IMU
./scripts/patches/pi5/3_camera_usb_webcam.sh 0    # your camera (USB) -- or 3_camera_usb_ir_1080p.sh

# 5. Prove the wiring -- ESC ON, WHEELS OFF THE GROUND
./scripts/run_diagnostics.sh --wheels-off

# 6. Auto-start on every boot, then reboot
./scripts/run_me.sh --install-boot && sudo reboot now
```

`ROBOT_SPEAKER_VOLUME` is the USB-speaker mixer %, re-applied on **every** launch. To change it on a running
robot: edit it, then `./scripts/setup_audio.sh`.

## 7. Quick config — concierge

The stationary greeter: PIR motion + face ID, DSI/HDMI face. From `~/LocalAiRobot`, in order.

**Setting up a board from scratch? Do [§5](#5-first-contact-a-fresh-board) first** — it ends with the
`git clone` that everything below assumes. On a fresh box you also skip step 0's `git pull` (nothing to
update) and step 5's purge (nothing to purge).

```bash
# 0. Get the code FIRST. The spoken WAVs are committed, so this is also how new voice cues reach the Pi.
cd ~/LocalAiRobot && git pull           # FRESH BOARD: skip -- §5 step 4 just cloned it
ls sounds/sleep_sounds/*.wav | wc -l    # want 12; 0 = the cues were never committed+pushed (see §11)

# 1. Install the stack. This also downloads BOTH face models: arcface.onnx (recogniser) and
#    face_detection_yunet.onnx (the landmark detector that makes recognition actually work -- see step 6).
#    SAY YES WHEN IT OFFERS TO REBOOT on a first install -- see the note below the block.
./scripts/install_stack_pi5.sh
# ./scripts/install_stack_jetson.sh

# 2. Config -- one block
sed -i -e 's|^ROBOT_AI_BACKEND=.*|ROBOT_AI_BACKEND=mixed|' \
       -e 's|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=<gemini_api_key>|' \
       -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=1|' \
       -e 's|^ROBOT_FACE_BACKEND=.*|ROBOT_FACE_BACKEND=framebuffer|' \
       -e 's|^ROBOT_FOLLOWUP_SILENCE_S=.*|ROBOT_FOLLOWUP_SILENCE_S=10|' \
       -e 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=75|' \
       -e 's|^ROBOT_CONCIERGE_GREET_ONLY=.*|ROBOT_CONCIERGE_GREET_ONLY=1|' \
       config/robot.env

# 3. Check it took
grep -E '^(ROBOT_AI_BACKEND|ROBOT_CONCIERGE_MODE|ROBOT_FACE_BACKEND|ROBOT_SPEAKER_VOLUME|ROBOT_PIR_PIN|ROBOT_PIR_COOLDOWN_S|ROBOT_CONCIERGE_SILENT_COOLDOWN_S|ROBOT_CONCIERGE_GREET_COOLDOWN_S|ROBOT_CONCIERGE_GREET_ONLY|ROBOT_FACE_ID_DETECTOR_BACKEND)=' config/robot.env

# 4. Camera -- REQUIRED, pick ONE. (No ESC patch: the concierge never drives.)
./scripts/patches/pi5/3_camera_usb_webcam.sh 0      # any USB webcam
# ./scripts/patches/pi5/3_camera_usb_ir_1080p.sh 0  # ...or the 1080p IR-CUT night-vision module
#
# No reboot and no boot-overlay step: a USB camera is not a CSI device. ./scripts/run_me.sh --reload is enough.
# Upgrading a robot that ONCE ran a CSI camera? a stale overlay can still hold /dev/video0 -- check once with
#   grep -n 'LocalAiRobot camera' /boot/firmware/config.txt
# and if it finds a dtoverlay line:  ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot
# (that is now the installer's DEFAULT, so a plain re-run clears it too).

# 4B. Wake sensor -- SKIP unless you swapped it; the defaults are already tuned for a PIR.
#     See "Swapping the wake sensor" below -- the two are NOT wire-compatible.
# ./scripts/patches/common/6_motion_pir_hcsr501.sh   # classic PIR (HC-SR501/Adafruit) -- the default
# ./scripts/patches/common/6_motion_vcnl4200.sh      # Vishay VCNL4200 active-IR proximity (I2C) instead

# 5. Clean slate -- only if this robot has run BEFORE (skip on a new board; nothing to purge).
#    Run it as YOURSELF -- no sudo (see the note under this block).
./scripts/purge_garbage.sh --dry-run    # see what would go, delete nothing
./scripts/purge_garbage.sh              # captures + logs + caches (KEEPS the face database)

# 6. Enrol people -- §9 leaves enroll_all.sh + ~/people_named/ on the Pi. File-based, so it's safe while
#    the robot is live (it reads photos, not the camera).
bash enroll_all.sh
./scripts/rag.sh --faces                # who's enrolled + how many photos each

# 7. Auto-start on every boot (also installs the detect-captures purge cron), then reboot
./scripts/run_me.sh --install-boot && sudo reboot now

# 8. Verify -- these four lines report the whole state
journalctl -u robot.service -n 300 | grep -E 'camera:|PIR motion wake|detect captures|face_id: ready'
```

> 🔴 **On a first install, say yes to the reboot.** It isn't cosmetic: the reboot is what applies your new
> **device groups** (`gpio`/`video`/`audio` — without them the PIR and camera fail on permissions) and
> **boot-to-console**, which is what gives the concierge face a `/dev/fb0` to draw on. Skip it and you get a
> blank face panel and a missing `PIR motion wake` line, neither of which points at the real cause.

Want:

```
camera: OpenCV ... backend started (still capture -> 512px square JPEG ...)
PIR motion wake: armed on BCM GPIO 4 (Concierge Mode) -- hold 0.10s, debounce 2.0s, pull=down
detect captures: keeping the deciding frame ... in /home/<user>/detect-captures
face_id: ready (model=arcface.onnx, detector=yunet, 5-point ALIGNED, threshold=0.38)
```

No `camera:` line at all means the step-4 patch never ran, or `/dev/video0` isn't the webcam
(`v4l2-ctl --list-devices`). No `PIR motion wake` line means `ROBOT_PIR_PIN` is empty — or the `gpio` group
never applied, i.e. you skipped the reboot above.

> 🔴 **The line that matters is `5-point ALIGNED`.** If it says `detector=haar, UNALIGNED crops` there is a
> loud WARNING right beneath it and recognition will be near-useless — ArcFace is trained on 5-point-aligned
> crops, so unaligned input makes every score collapse into 0.13–0.21 and the "closest" name a lottery (a
> field run recognised **2.3%** of frames that contained a face). Fix with `./scripts/provision_face_id.sh`,
> then **re-run step 6** so the stored embeddings are aligned too.

### `ROBOT_CONCIERGE_GREET_ONLY` — greet, then say nothing

`1` drops the spoken goodbye (`"Okay, chat later."` / `"Okay, see ya."`). **The conversation itself is
unchanged** — Jarvis greets, listens, and chats normally if you talk to it. The only difference is what
happens when the follow-up window times out after `ROBOT_FOLLOWUP_SILENCE_S`: instead of playing a
`sleep_sounds/*.wav` it simply goes quiet, so the **greeting becomes the only thing it ever says
unprompted**.

Worth having on a hallway greeter for two reasons:

- It gets **walked past** far more often than talked to, so that window usually expires with nobody there
  and the goodbye is a farewell to an empty corridor.
- The clip is **blocking** — it holds the audio lock until it finishes, and that's time the *next* arrival
  can't be greeted in.

You can still see it happen (the face changes to `sleeping`) and the log records it:

```
conversation: 16s of silence -> sleeping (say the wake word to talk again)
conversation: sleeping SILENTLY (ROBOT_CONCIERGE_GREET_ONLY=1)
```

`0` restores the goodbye. Unlike the volume, this one is read at startup — `./scripts/run_me.sh --reload`.

### Swapping the wake sensor (PIR ↔ VCNL4200 proximity)

**These two are NOT wire-compatible.** A PIR hands the Pi one digital HIGH/LOW on `ROBOT_PIR_PIN`; the
VCNL4200 talks I²C, boots with its sensor disabled, and must be configured over the bus. So its patch also
switches the backend (`ROBOT_WAKE_SENSOR=proximity` → [`brain/proximity.py`](brain/proximity.py)).

| | PIR (HC-SR501) | VCNL4200 |
|---|---|---|
| VCC | **5 V** — pin 2 *(3.3 V won't trigger)* | **3.3 V** — pin 1 |
| GND | pin 6 | pin 6 |
| Signal | OUT → GPIO 4 (pin 7) | SDA → GPIO 2 (pin 3), SCL → GPIO 3 (pin 5) |
| Extra | — | I²C enabled; `i2cdetect -y 1` shows `0x51` |

**Order matters** — wire it, prove the chip answers, *then* touch the config. Doing it the other way round
means debugging software against hardware that was never connected.

```bash
# PIR -> VCNL4200
sudo poweroff                                  # 1. wire the 4 pins above, power back on
sudo raspi-config                              # 2. Interface Options -> I2C -> Yes, then reboot
i2cdetect -y 1                                 # 3. MUST show 0x51 -- no device = wiring, stop here
cd ~/LocalAiRobot && git checkout main && git pull
./scripts/patches/common/6_motion_vcnl4200.sh  # 4. backend + starting thresholds + driver

sudo systemctl stop robot.service              # 5. measure YOUR thresholds (see the warning below)
./scripts/proximity_probe.sh
./scripts/patches/common/6_motion_vcnl4200.sh 480 260   # 6. re-run with the suggested NEAR / FAR
sudo systemctl start robot.service

journalctl -u robot.service -n 40 | grep proximity      # 7. verify
```

Want `proximity wake: VCNL4200 armed over I2C ... near>=480, far<=260`, then on approach
`proximity: someone arrived (raw 512 >= near 480) -> wake`.

```bash
# VCNL4200 -> PIR (going back)
./scripts/patches/common/6_motion_pir_hcsr501.sh   # ALSO resets the backend -- without this the brain
                                                   # keeps polling an I2C chip you just unplugged
# power off, re-wire VCC->5V (pin 2), GND->GND, OUT->GPIO 4 (pin 7), power on
./scripts/run_me.sh --reload
./scripts/pir_sensor_probe.sh                      # walk past 10x -- the count should track your passes
```

> ⚠️ **Measure the thresholds — you cannot guess them.** `ROBOT_PROXIMITY_NEAR`/`_FAR` are **raw
> reflected-IR counts, not centimetres**, and a white shirt at 80 cm can out-read a black coat at 40 cm.
> Stop the brain and run [`./scripts/proximity_probe.sh`](scripts/proximity_probe.sh): empty doorway first
> for the floor, then walk up — **in dark clothing as well as light, and take the lower reading**. It
> suggests values from the session.

### Swapping the PIR itself (hobby HC-SR501 → an industrial PaPIRs)

Different from the section above: this is one PIR for another, **not** a change of sensor *family*. Same
three wires, same `ROBOT_PIR_PIN`, same backend, **no patch to run and no code change** — the two keys
involved already exist and are inert at their defaults.

It is still not a drop-in. An **HC-SR501 is a hobby module**: 5 V in, its own regulator, a push-pull output
that drives HIGH hard, two pots, and a **~2.5 s latch** holding the output up after each trigger. A
**Panasonic PaPIRs (EKMC/EKMB) is the bare industrial part**: no pots, no latch, a deliberately weak output,
a real warm-up, and an output pin that swings to whatever you feed Vdd.

Figures below are from the **EKMB1105112** sheet (Ver. 1.1, WL series, 1 µA, black lens). Its siblings
`EKMB1205112` (2 µA) and `EKMB1305112K` (6 µA) share every number that matters here.

| | HC-SR501 | **PaPIRs EKMB1105112** |
|---|---|---|
| VCC | **5 V** (pin 2) — its regulator needs it | **3.3 V** (pin 1) — 2.3–4.0 V operating, **4.5 V absolute max** |
| Signal | OUT → GPIO 4 (pin 7) | OUT → GPIO 4 (pin 7) |
| Output stage | push-pull, drives HIGH hard | **sources only**, Iout max 100 µA, Vout ≥ Vdd−0.5 V |
| Output hold | latched ~2.5 s | **Standby Hold ~2.6 s typ**, then **Mask ~1.3 s** (blind) |
| Warm-up (Twu) | ~5 s | **25 s typ, 210 s MAX** — output undefined meanwhile |
| Detection | ~110°, a handful of zones | **122° / 88 zones** (area A) + 150° / 16 zones (area B), 5 m |
| Idle current | ~50 mA | **1.0 µA** |

> 🔴 **3.3 V only — 5 V destroys this sensor.** Operating range is **2.3–4.0 V** and the *absolute maximum
> rating* is **4.5 V**, so 5 V is beyond the destruct limit for the part itself — quite apart from putting
> 5 V on a GPIO that is 3.3 V-only and not 5 V-tolerant. Never wire this to pin 2. Good news: Panasonic
> characterises the detection performance **at 3 V**, so 3.3 V is essentially the reference condition and
> you give up nothing in range.

> 🔴 **The decoupling capacitor is required, not optional.** §6-3(8): *"Please use a stabilized power
> supply. Power supply noise can cause operating errors… install a capacitor on the sensor's power supply
> pin."* That is exactly the phantom-wake storm you already fought once. Put a **0.1 µF ceramic right at
> the sensor's Vdd/GND pins**, plus **10–100 µF** bulk if the run is long. §6-3(6) also asks for
> **shielded cable, kept as short as possible** — relevant here, because this sensor wants to be mounted
> *outside* the enclosure. §6-3(7) recommends surge protection, and §6-3(5) warns **±200 V of static can
> destroy it**: handle it by the body, not the pins.

**Mounting — this part is fussier than the HC-SR501, and the numbers are specified:**

- **Lens clearance is a hard number** (§7-1/7-2). The lens must protrude **≥ 6.2 mm** past your enclosure
  to get the full detection area (A **and** B); **≥ 0.7 mm** if area A alone is enough. Recess it further
  and you are optically cropping the sensor — which looks exactly like a dead sensor. The mounting hole
  should be **tapered or oversized**, not a tight straight bore.
- **No glass or acrylic in front of it** (§6-1(2a)) — those block IR transmission. A "protective window"
  over the lens disables the sensor.
- **Mount it on a PCB** (§6-3(2)) and don't press or deform the lens — it's soft polyethylene (§6-3(11)).
- **Keep it away from moving air and temperature swings** — HVAC vents, hot or cold draughts, humidifier
  vapour. It is worth restating that the original 465-false-wakes-per-hour storm on this build was the PIR
  sitting in the enclosure's only vent hole, watching the Pi's own warm exhaust. The datasheet independently
  names this class of cause; mount it **outside** the enclosure, in still air.

**The settings, from the datasheet:**

```bash
sed -i -e 's|^ROBOT_PIR_PULL=.*|ROBOT_PIR_PULL=down|' \
       -e 's|^ROBOT_PIR_WARMUP_S=.*|ROBOT_PIR_WARMUP_S=210|' \
       config/robot.env
./scripts/run_me.sh --reload
```

- **`ROBOT_PIR_PULL=down`** — the same value as the HC-SR501, for the opposite reason. The wiring diagram
  (§4-7) shows current flowing **out of the sensor into your load**: it **sources** only (Iout max 100 µA),
  is **active-high**, and is high-impedance when idle, so it cannot pull the pin down itself and *needs*
  the pull-down to define its LOW state. Leave the pin floating (`none`) and it wanders and invents visitors
  all night. The Pi's ~50 kΩ internal pull-down draws ~66 µA at 3.3 V, inside the 100 µA budget, and
  **Vout ≥ Vdd−0.5 V = 2.8 V** against that load — comfortably above the Pi's ~2.31 V logic-high threshold.
- **`ROBOT_PIR_WARMUP_S=210`** — Circuit Stability Time (Twu) is **25 s typical but 210 s maximum**, and
  during it "the output's status is undefined (ON/OFF) and detection is therefore not guaranteed". 210 is
  the only spec-compliant value, and it is nearly free: the **wake word still works** throughout, and the
  robot boots rarely. Without it you get a burst of phantom wakes at every boot, the chatter warning fires,
  and you go hunting a hardware fault that was never there. **Drop it to `30` while bench-testing**, or a
  3½-minute silence after each restart will look exactly like a dead sensor.
- **`ROBOT_PIR_HOLD_S` — leave it at `0.1`.** See the correction below.

> ⚠️ **Do not set `ROBOT_PIR_HOLD_S=0` on this part.** The EKMB's §4-5 timing chart defines a **Standby
> Hold Time of ~2.6 s typ** (extended if motion continues) followed by a **Mask Time of ~1.3 s** during
> which no detection is possible. So its output *is* latched, comparable to the HC-SR501's ~2.5 s, and the
> 100 ms re-read works exactly as designed — it still filters wire-level electrical noise, which isn't
> latched. It is the **EKMC1605111** that has no hold time, no mask time and no minimum pulse width; on
> *that* part `HOLD_S=0.1` would discard every real visitor. Check which part you have before touching it.

**Two consequences of the hold + mask times worth knowing.** Rising edges can be no closer together than
**~3.9 s** (2.6 hold + 1.3 mask), so this sensor self-limits the wake rate and `ROBOT_PIR_COOLDOWN_S=2`
never binds. And that 1.3 s mask is **real blindness** — a second visitor arriving inside it is not seen at
all, by the hardware, regardless of software.

**On the transient warning.** §6-1(4) notes an "unnecessary detection signal might be outputted, on rare
occasions… due to the nature of pyro-electric element", and suggests a pulse-count circuit. You already
have something better: the concierge's **Stage-A camera probe** settles it in ~120 ms *with evidence* — it
looks, finds nobody, and arms a short silent cooldown. A pulse counter guesses; a camera knows.

**The polling change.** [`brain/pir.py`](brain/pir.py) now uses `DigitalInputDevice` with **edge callbacks**
(lgpio → kernel edge events) instead of `MotionSensor`, which sampled at 10 Hz. On *this* part that was not
strictly necessary — a 100 ms sample period cannot miss a 2.6 s pulse — but it removes up to a full sample
period of latency from every wake, and it is required for the latch-less EKMC. No effect on the HC-SR501
beyond waking sooner.

**What actually makes this sensor better for a hallway.** Not sensitivity — **zone density**. Area A gives
**88 detection zones across 122°**, where an HC-SR501's Fresnel lens has roughly a dozen over a similar
span. Head-on approach produces only small changes in angular position, so the denser the zones, the more
likely a given step still crosses a boundary. That is the *mechanism* behind your 1–3/12 head-on result,
and this part attacks it directly. Two caveats from §6-1(2): the target must move at around **1.0 m/s**
(very fast movement can also be missed), and **glass or acrylic in front of the sensor blocks IR** — do not
put it behind a window in the enclosure.

**Verify after the reload** — the second line confirms the pull actually took:

```bash
journalctl -u robot.service -n 40 | grep 'PIR motion wake'
./scripts/pir_sensor_probe.sh          # walk past 10x -- the count should track your passes
```

```
PIR motion wake: warming up for 210s -- edges ignored until the sensor settles
PIR motion wake: armed on BCM GPIO 4 (Concierge Mode) -- hold 0.10s, debounce 2.0s, pull=down
```

**Optional sanity check on the wiring**, if it behaves oddly — read the raw pin with `pinctrl`, which
bypasses the brain entirely. Expect steady `lo` at rest and `hi` while you wave, after the 30 s warm-up:

```bash
sudo systemctl stop robot.service      # nothing else may hold the pin
sleep 40                               # WAIT OUT THE WARM-UP -- readings before this are junk
pinctrl set 4 ip pd && watch -n 0.2 pinctrl get 4    # Ctrl-C when done; reads "... | hi" / "| lo"
```

A reading that **wanders with nobody in the room** means the pin isn't being held down — check the
pull-down took, and check the decoupling capacitor. A pin that **never goes high** means either the supply
or the polarity is wrong.

> ⚠️ **A better PIR does not change the physics.** It is still passive, so it still fires when a body
> *crosses* between its Fresnel zones, and head-on approach is still its worst case. You are buying
> sensitivity and a cleaner output, **not a different detection geometry** — see the field numbers below.

### Which sensor, and why — field notes

**A PIR is passive.** It fires when a warm body *crosses* between its Fresnel zones, which makes head-on
approach its **worst** case. Measured on the reference build with sensitivity at MAX and the delay pot at
MIN: **12 deliberate walk-straight-at-it approaches → 1–3 detections. The same sensor, same settings, person
crossing its field instead → 12/12.** A heated room and winter clothing make it worse again (both cut the
thermal contrast it depends on), and the HC-SR501 has a hardware-fixed ~2.5 s blocking time after every
trigger. **So aim a PIR *across* the traffic, never down the approach lane** — that single change is usually
worth more than any config.

**Active IR proximity is the mirror image.** It emits its own beam and measures the reflection, so head-on
approach is its *best* case and thermal contrast stops mattering entirely. The trade-offs: useful range is
only ~50–100 cm (Adafruit's own guidance), and reflectivity matters a lot. Right for a greeter people walk
**up to**; wrong for one watching a corridor.

**24 GHz mmWave presence radar (HLK-LD2410C) was tried here and rejected.** It produced **zero** false wakes
against the PIR's 465/hour — genuinely impressive — but only **4 usable wakes in 25 minutes**. It reports
*presence*, sees through plasterboard, and in a room its owner occupies "is anyone present?" is permanently
true, so there are almost no rising edges to trigger on. It would suit a **doorway or porch**, where presence
actually changes, and would need range-gating over its UART to stop it announcing people the camera can't yet
resolve. Its patch was removed when the hardware went back — `git log --diff-filter=D -- scripts/patches`
will find it.


**Proving the PIR is optional now.** A chattering sensor no longer breaks anything: the cheap Stage-A probe
kills a false wake in ~120 ms and the silent outcome cooldown paces the rest, so this is tidy-up, not a
bring-up blocker. Reach for it only when the hourly summary says the sensor is lying —
`journalctl -u robot.service | grep 'concierge: last'`, then `./scripts/pir_sensor_probe.sh`. Wave = `####
MOTION ####`; it should then be **silent for 2 min** in an empty room. Causes, most likely first: sensitivity
**pot** turned up; **retrigger jumper** (HC-SR501 `H`/`L`) in the wrong position, which makes the module
free-run; aimed at the Pi's **fan exhaust** / a heat source / a sunlit window; VCC on a **noisy 5 V rail**
(100 µF across VCC/GND, or a separate supply).

## 8. The update loop

After changing code on the PC and pushing. **Order matters** — doing it wrong means debugging old captures
against a stale journal.

```bash
# 1. New code -- see the robot.env warning under this block before you commit anything on the robot
cd ~/LocalAiRobot && git pull

# 2. New config keys are NOT pulled (robot.env is ignored) -- absent keys fall back to code defaults,
#    which is usually fine, but check for ones you want to tune:
grep -c ROBOT_CONCIERGE_SILENT_COOLDOWN_S config/robot.env   # 0 = your robot.env predates the two-stage wake

# 3. Fetch any new models (idempotent -- instant no-op when they're already there)
./scripts/provision_face_id.sh

# 4. Clean slate, so the next log is only about the NEW code
./scripts/purge_garbage.sh --dry-run
./scripts/purge_garbage.sh
# ./scripts/purge_garbage.sh --all     # ...and wipe memory.db (backs it up first, asks you to type WIPE)

# 5. Re-enrol if anything about the face pipeline changed
bash enroll_all.sh && ./scripts/rag.sh --faces

# 6. Boot service + reboot, then verify as in §7 step 8
./scripts/run_me.sh --install-boot && sudo reboot now
```

> 🔴 **`config/robot.env` is TRACKED, not ignored.** It's committed as a *template* with an empty
> `ROBOT_GEMINI_API_KEY`; the real key exists only as a local modification on the robot. Consequences:
>
> - A pull **merges** — new keys arrive and your edited lines stay, unless the same line changed on both
>   sides, which is an ordinary conflict to resolve by hand.
> - **Never `git add -A` or `git commit -a` on the robot.** That stages `config/robot.env` *with your live
>   API key* and pushes it to GitHub. If you're unsure what's staged:
>   ```bash
>   git diff --cached | grep -i gemini     # must print nothing
>   git diff config/robot.env              # your local config drift, for eyeballing
>   ```
> - Editing it with `sed -i 's|^KEY=.*|KEY=value|'` **replaces the whole line, comments included** — the
>   documentation for that key is gone until the next merge brings it back. Harmless, but don't be surprised.

**When do you actually need `--all`?** `enroll_person.sh` **replaces** a person — it clears their prior
embeddings before storing the new ones — so a plain `bash enroll_all.sh` already rebuilds every embedding it
covers. You only need the wipe if someone was enrolled ad-hoc with `enroll_person.sh --camera` and is
therefore *not* in `enroll_all.sh`; their stale row would otherwise survive and pollute the runner-up margin.

> 🔴 **Never `sudo ./scripts/purge_garbage.sh`.** It calls `sudo` itself for the two steps that need it (the
> systemd journal and apt). Run the *whole* thing as root and `sudo` resets `HOME` to `/root`, so every path
> retargets — it would look for captures in `/root/detect-captures` and the database in
> `/root/.localairobot/`, find neither, and cheerfully report success having purged nothing. The script
> refuses when it detects it was started via `sudo` from a normal account (a genuine root login is fine).
>
> `sudo: ./scripts/purge_garbage.sh: command not found` means one of two things: you used `sudo` (drop it),
> or the file lost its executable bit. Git only carries file modes if they were staged with
> `git add --chmod=+x` — on Windows `core.filemode` is `false`, so a plain `chmod +x` never reaches the
> commit. Either fix works:
>
> ```bash
> bash ./scripts/purge_garbage.sh --dry-run   # works regardless of the +x bit
> chmod +x scripts/*.sh                       # or fix the bit locally
> ```

## 9. Enrolling faces

New faces → greeted by name. Steps 1–3 run **on your PC**; 4–5 push to the robot.

```bash
# 1. Slice a contact sheet into individual cells
#    people/people_all*.png (8x4) -> people/people_individual/1.jpg, 2.jpg, ...
./scripts/slice_image.sh

# 2. Rename each cell to the person's NAME and move it into people/people_named/
#    More shots of the same person: <Name>_02.jpg, <Name>_03.jpg -> all grouped under that name.

# 3. Generate the batch script, then fill in each "<FILL THE DESC>" line (and a say-as respelling for
#    tricky names, e.g. "Ruh-mesh"; blank = say the real name)
./scripts/create_enrollments.sh          # -> enroll_all.sh  (git-ignored; re-run to add new people)

# 4. Copy photos + script to the robot
ssh <user>@<robot-host>.local 'mkdir -p ~/people_named'
scp people/people_named/*.jpg <user>@<robot-host>.local:~/people_named/
scp enroll_all.sh             <user>@<robot-host>.local:~/LocalAiRobot/

# 5. Enrol ON THE ROBOT (reads files, not the camera -- safe while it's live)
ssh <user>@<robot-host>.local 'cd ~/LocalAiRobot && bash enroll_all.sh'
ssh <user>@<robot-host>.local 'cd ~/LocalAiRobot && ./scripts/rag.sh --faces'
```

Enrolment accepts a **smaller** face than live detection does (`ROBOT_FACE_ID_MIN_FACE_PX_ENROLL`, default 40
vs 60) — a photo you deliberately chose shouldn't be dropped for being a bit small. It logs when it accepts
one below the live floor, because that person will be harder to recognise from across a room.

## 10. Pulling logs

```bash
# Everything the journal still holds -> one local file
ssh <user>@<robot-host>.local 'journalctl -u robot.service --no-pager' > robot-all.log

# It gets BIG. Strip the per-second power spam for events only (154MB -> ~1MB):
ssh <user>@<robot-host>.local "journalctl -u robot.service --no-pager \
  | grep -av 'robot.main \(INFO power:\|DEBUG power rails\)'" > robot-events.log

# Just what decides behaviour
ssh <user>@<robot-host>.local "journalctl -u robot.service --no-pager \
  | grep -aE 'camera:|PIR|concierge:|face_id:|detect capture'"
```

**The one line worth reading first** (logged hourly, `ROBOT_CONCIERGE_SUMMARY_S`):

```bash
ssh <user>@<robot-host>.local "journalctl -u robot.service --no-pager | grep -a 'concierge: last'"
```

```
concierge: last 60 min -- PIR 431 wake(s) (+402 held by cooldown, +96 debounce, +0 noise)
           | 428 silent (probe), 3 looked at, 1 lost in the window, 2 greeting(s) (1 by name)
```

- `silent (probe)` ≫ `looked at` → the **sensor** is lying → hardware (§7, end).
- `looked at` ≫ `greeting(s)` → the **recogniser** is failing → check `5-point ALIGNED`, then re-enrol.

Those two need opposite fixes and used to look identical in the log, which is why the line exists.

## 11. Spoken earcons

The WAVs are **committed**, so the robot only ever gets them via `git pull`. Run this **on your PC** (needs
Piper, installed by `./scripts/host_setup.sh`) whenever the word list in `scripts/generate_sounds.sh` changes
— then commit *and push*, or the cue is silent on the robot.

```bash
./scripts/generate_sounds.sh          # writes only cues you don't have yet
./scripts/generate_sounds.sh --force  # re-speak everything
git status sounds/                    # review, then commit + push
```

`say()` **skips files that already exist**, so if you change the *wording* behind an existing filename you
must delete that WAV (or use `--force`) — otherwise you keep the old audio with the new script.

## 12. Recovery

### Wi-Fi profile for field access

> 🔴 **GOLDEN RULE.** From an SSH session, **never** run `sudo nmcli device wifi connect …` — it switches the
> radio immediately and freezes the very session you're on. `connection add` below only **saves** a profile;
> it does not switch, so your SSH stays alive.

```bash
# 1. Phone hotspot ON, then scan
sudo nmcli device wifi rescan; sleep 4
nmcli -f SSID,CHAN,FREQ,SIGNAL device wifi list      # not listed? wait 5s, rescan (2.4GHz shows up better)

# 2. SAVE it. Priority -10 is BELOW home Wi-Fi's default 0, so it can never hijack you while home is up.
sudo nmcli connection add type wifi con-name "phone-hotspot" ssid "<hotspot_ssid>" \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<hotspot_password>" \
     connection.autoconnect yes connection.autoconnect-priority -10

# 3. Verify -- still on home Wi-Fi, hotspot saved but idle
nmcli connection show --active
nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
```

Away from home: hotspot ON → the robot auto-joins → `ssh <user>@<robot-host>.local`. If `.local` won't
resolve (some Androids block mDNS), take the IP from the phone's *Hotspot → Connected devices*. **Caveat:**
some Android hotspots isolate clients (phone↔device blocked = SSH impossible) — test once at home. Fuller
write-up: [`docs/change-pi-wifi.md`](docs/change-pi-wifi.md).

### Read the disk offline (SSH is dead)

Pull the NVMe into a USB enclosure, plug it into a PC, then **in PowerShell as Administrator**:

```powershell
# ro = read-only: safe on a filesystem that just aborted its journal. Never write to a suspect disk.
wsl --mount \\.\PHYSICALDRIVE1 --partition 2 --type ext4 --options ro
# -> /mnt/wsl/PHYSICALDRIVE1p2   (p1 = FAT32 boot firmware, p2 = ext4 root)
# Wrong drive number?  Get-Disk
```

```bash
# In WSL -- rescue the only two irreplaceable things (everything else comes back from git + install_stack)
cp /mnt/wsl/PHYSICALDRIVE1p2/home/<user>/LocalAiRobot/config/robot.env ~/robot.env.rescue
cp /mnt/wsl/PHYSICALDRIVE1p2/home/<user>/.localairobot/memory.db       ~/memory.db.rescue
```

```powershell
wsl --unmount \\.\PHYSICALDRIVE1        # ALWAYS unmount before pulling the drive
```

### Is the drive actually dead?

A **fresh OS install** that still throws `EXT4-fs` I/O errors is *not* corruption — reformatting only rewrites
the filesystem layer; the fault is below it. Test the drive with the Pi out of the picture (enclosure on a PC,
`wsl --mount \\.\PHYSICALDRIVE1 --bare`):

```bash
lsblk
sudo nvme smart-log /dev/sdX          # critical_warning, media_errors, percentage_used
sudo badblocks -sv -b 4096 /dev/sdX   # full surface read -- must finish with ZERO errors
```

- **Errors** → the drive is dead, replace it.
- **Clean** → it's the Pi: **power** (official 27 W mains PSU; a UBEC/LiPo feed can't PD-negotiate and NVMe
  pulls 1–2 A in bursts) → **ribbon** (reseat both ends of the M.2 HAT FFC, check orientation) → **PCIe gen**
  (`dtparam=pciex1_gen=3` in `config.txt` is out of spec — set 2 or delete).
- Meanwhile, flash a plain SD card and build on that; it takes PCIe out of the equation entirely.

### Golden image

```bash
# Cheap + weekly -- 95% of the irreplaceable value, one second (ON THE ROBOT)
tar czf ~/state-$(date +%F).tgz -C ~/LocalAiRobot config/robot.env -C ~ .localairobot/memory.db

# Full image -- after a milestone. Zero the free space first or it won't compress (250GB -> ~5GB)
sudo dd if=/dev/zero of=/zerofill bs=1M status=progress; sync; sudo rm -f /zerofill
sudo shutdown -h now
```

Then either Rescuezilla/Clonezilla on an x86 PC (used-blocks only, ext4-aware; restore **whole disk**, not a
partition), or `dd` from WSL — note `--bare` (whole disk), **not** `--partition 2`, or you lose the bootloader:

```bash
sudo dd if=/dev/sdX bs=4M status=progress | gzip -c > ~/golden-$(date +%F).img.gz
gunzip -c ~/golden-YYYY-MM-DD.img.gz | sudo dd of=/dev/sdX bs=4M status=progress; sync   # restore
```

> **Clone only a clean filesystem** (`fsck -fy` first) or you enshrine the corruption. PiShrink afterwards
> gives a smaller image that auto-expands on first boot. ⚠️ **The image contains your API key, Wi-Fi password
> and `memory.db` — store it like a secret.**
