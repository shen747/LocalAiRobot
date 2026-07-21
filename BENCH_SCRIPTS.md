# Bench calibration scripts — ESC, steering servo & voice

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
