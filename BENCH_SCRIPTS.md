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
| [5](#5-first-contact-a-fresh-board) | First contact — OS update, git identity, SSH key | once per board |
| [6](#6-quick-config--crawler-car) | Quick config: **crawler car** (it drives) | once, then rarely |
| [7](#7-quick-config--concierge) | Quick config: **concierge** (stationary greeter) | once, then rarely |
| [8](#8-the-update-loop) | **The update loop** — pull → purge → re-enrol → reboot | every code change |
| [9](#9-enrolling-faces) | Enrolling faces | whenever people change |
| [10](#10-pulling-logs) | Pulling logs & the one diagnostic line | when something's wrong |
| [11](#11-spoken-earcons) | Regenerating the spoken earcons | when the word list changes |
| [12](#12-recovery) | Recovery — Wi-Fi profile, offline disk read, golden image | when it breaks |

## 5. First contact (a fresh board)

Do this **before** §6/§7 — `install_stack` runs apt itself and wants a current index.

```bash
# 1. Update the OS (can take a while on a fresh image)
sudo apt update && sudo apt upgrade -y
```

> ⚠️ A later `apt upgrade` can replace libcamera and silently **revert an Arducam IMX519 autofocus fork**. If
> the camera startup line says `FIXED-FOCUS` after an upgrade, re-run
> `./scripts/patches/pi5/3_camera_imx519.sh` (delete `~/.localairobot/.imx519_af_installed` first to force it).

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

# 4. Ignore a file LOCALLY, with no .gitignore commit -- this is where a private notes/keys file goes:
nano .git/info/exclude
```

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
# ./scripts/patches/pi5/3_camera_imx519.sh        # Arducam IMX519 CSI + autofocus

# 5. Prove the wiring -- ESC ON, WHEELS OFF THE GROUND
./scripts/run_diagnostics.sh --wheels-off

# 6. Auto-start on every boot, then reboot
./scripts/run_me.sh --install-boot && sudo reboot now
```

`ROBOT_SPEAKER_VOLUME` is the USB-speaker mixer %, re-applied on **every** launch. To change it on a running
robot: edit it, then `./scripts/setup_audio.sh`.

## 7. Quick config — concierge

The stationary greeter: PIR motion + face ID, DSI/HDMI face. From `~/LocalAiRobot`, in order.

```bash
# 0. Get the code FIRST. The spoken WAVs are committed, so this is also how new voice cues reach the Pi.
cd ~/LocalAiRobot && git pull
ls sounds/sleep_sounds/*.wav | wc -l    # want 12; 0 = the cues were never committed+pushed (see §11)

# 1. Install the stack. This also downloads BOTH face models: arcface.onnx (recogniser) and
#    face_detection_yunet.onnx (the landmark detector that makes recognition actually work -- see step 6).
./scripts/install_stack_pi5.sh
# ./scripts/install_stack_jetson.sh

# 2. Config -- one block
sed -i -e 's|^ROBOT_AI_BACKEND=.*|ROBOT_AI_BACKEND=mixed|' \
       -e 's|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=<gemini_api_key>|' \
       -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=1|' \
       -e 's|^ROBOT_FACE_BACKEND=.*|ROBOT_FACE_BACKEND=framebuffer|' \
       -e 's|^ROBOT_FOLLOWUP_SILENCE_S=.*|ROBOT_FOLLOWUP_SILENCE_S=10|' \
       -e 's|^ROBOT_SPEAKER_VOLUME=.*|ROBOT_SPEAKER_VOLUME=75|' \
       config/robot.env

# 3. Check it took (ROBOT_CAMERA_OVERLAY is deliberately absent -- it's an install-time env var for step 4,
#    not a robot.env key, so grepping for it always finds nothing)
grep -E '^(ROBOT_AI_BACKEND|ROBOT_CONCIERGE_MODE|ROBOT_FACE_BACKEND|ROBOT_SPEAKER_VOLUME|ROBOT_PIR_PIN|ROBOT_PIR_COOLDOWN_S|ROBOT_CONCIERGE_SILENT_COOLDOWN_S|ROBOT_CONCIERGE_GREET_COOLDOWN_S|ROBOT_FACE_ID_DETECTOR_BACKEND)=' config/robot.env

# 4. Camera -- ONLY for a USB webcam. install_stack already did the CSI/IMX519 overlay + autofocus.
#    (No ESC patch: the concierge never drives.)
# ./scripts/patches/pi5/3_camera_usb_webcam.sh 0
# ROBOT_CAMERA_OVERLAY=none ./scripts/install_stack_pi5.sh && sudo reboot   # frees /dev/video0 from the CSI overlay

# 4B. Wake sensor -- SKIP unless you swapped it; the defaults are already tuned for a PIR.
#     Config only, no code changes, identical wiring (VCC->5V, GND->GND, OUT->GPIO 4).
# ./scripts/patches/common/6_motion_pir_hcsr501.sh   # classic PIR (HC-SR501/Adafruit) -- the default
# ./scripts/patches/common/6_motion_ld2410c.sh       # HLK-LD2410C 24GHz mmWave presence radar instead

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

Want:

```
camera: ... autofocus=ON                      (", FIXED-FOCUS" = AF not engaged; the WARNING names the fix)
PIR motion wake: armed on BCM GPIO 4 (Concierge Mode) -- hold 0.25s, debounce 2.0s
detect captures: keeping the deciding frame ... in /home/<user>/detect-captures
face_id: ready (model=arcface.onnx, detector=yunet, 5-point ALIGNED, threshold=0.38)
```

> 🔴 **The line that matters is `5-point ALIGNED`.** If it says `detector=haar, UNALIGNED crops` there is a
> loud WARNING right beneath it and recognition will be near-useless — ArcFace is trained on 5-point-aligned
> crops, so unaligned input makes every score collapse into 0.13–0.21 and the "closest" name a lottery (a
> field run recognised **2.3%** of frames that contained a face). Fix with `./scripts/provision_face_id.sh`,
> then **re-run step 6** so the stored embeddings are aligned too.

### Swapping the wake sensor (PIR ↔ mmWave radar)

Both sensors hand the Pi the same thing — a digital HIGH/LOW on one wire — which is all
`gpiozero.MotionSensor` in [`brain/pir.py`](brain/pir.py) ever reads. So the swap is **three wires and one
patch, no code changes**:

| | Pi 5 |
|---|---|
| VCC | 5 V (pin 2) — *PIR needs 5 V, not 3.3 V, or it won't trigger* |
| GND | GND (pin 6) |
| OUT | GPIO 4 (pin 7) — the same pin either way |

```bash
./scripts/patches/common/6_motion_ld2410c.sh       # switch to the radar
./scripts/patches/common/6_motion_pir_hcsr501.sh   # ...and back again
./scripts/patches/common/6_motion_ld2410c.sh 17    # pass a BCM pin to move it off GPIO 4
```

All the patch changes is **how much defence the wake needs**. `ROBOT_PIR_HOLD_S`,
`ROBOT_CONCIERGE_SILENT_COOLDOWN_S` and `ROBOT_CONCIERGE_PROBE_TRIES` exist *solely* to survive a PIR that
lies — and it does: a field log had 3,741 of 3,786 wakes with nobody in front of the camera. Radar doesn't
false-trigger on fan exhaust or sunlight, so the patch drops all three: **~0.89 s to greet you with a ~43%
chance of waiting out a cooldown first → ~0.7 s essentially every time.** Verify with:

```bash
journalctl -u robot.service | grep 'from PIR'
```

> ⚠️ **Meter OUT before you connect it.** The LD2410C datasheet specifies 3.3 V IO (Pi-safe), but clones
> vary and 5 V on a GPIO destroys the pin.

Two behaviours to expect from radar, neither of which is a fault:

- **It sees through plasterboard.** You will get wakes from the next room until you range-gate it over its
  UART (TX/RX, 256000 baud 8N1). Budget an evening; this is the #1 complaint with these modules.
- **It reports presence, not movement.** OUT stays HIGH the whole time someone is in view, so you get one
  clean wake per arrival — and it finally notices someone who walks up and *stops moving*, which PIRs can't.
  The flip side: in a room you occupy all day it sits HIGH for hours and produces no new edges at all. Ideal
  by a door, wrong for a desk.

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
# 1. New code (config/robot.env is git-ignored, so your key + volume survive a pull)
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
