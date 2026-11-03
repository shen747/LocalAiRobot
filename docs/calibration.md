# Calibration

Find the numbers your ESC and steering servo need, on the bench, before the first drive. Do it again after
swapping the ESC, servo or motor. The last section tunes how the robot says a name.

**Wheels off the ground, ESC switched on.** Each script first holds neutral; wait for the ESC's long "ready"
beep, which means it has armed. Ctrl-C stops any script. Run them on the Pi in `~/LocalAiRobot`.

## Where the numbers go

| You find | Put it in | Setting |
|---|---|---|
| neutral: wheels still, long ready beep | your ESC patch | `ROBOT_ESC_NEUTRAL` |
| slowest forward that moves | your ESC patch | `ROBOT_MIN_FORWARD_SPEED` |
| a good forward top speed | your ESC patch | `ROBOT_MAX_FORWARD_SPEED` |
| slowest reverse that moves | your ESC patch | `ROBOT_MIN_REVERSE_SPEED` |
| a good reverse top speed | your ESC patch | `ROBOT_MAX_REVERSE_SPEED` |
| wheels pointing straight | `config/robot.env` | `ROBOT_STEER_CENTER` |
| full left and right without the servo buzzing | `config/robot.env` | `ROBOT_STEER_LEFT`, `ROBOT_STEER_RIGHT` |

ESC values live in the patch, so they survive a reinstall: edit the numbers at the top of
`scripts/patches/common/1_esc_hb_qr_1060.sh` (or `1_esc_uliik_60a.sh`) and run it.

## How throttle maps to the ESC

Throttle runs from -1.0 to +1.0 and becomes a pulse of `1500 + throttle × 500` µs.

- `0.00` is 1500 µs. Positive is forward, negative is reverse.
- Many crawler ESCs have an offset neutral: the WP-1060 arms and stops at `+0.25` (1625 µs).
- Forward only pulls above a dead band (about `+0.55`, 1775 µs, on the WP-1060).
- Reverse starts just below 1500 µs: `0.00` does nothing, `-0.01` already creeps back. More negative is faster.

## 1. Quick ESC test

Arms, moves forward, stops, moves back, stops. Use it to check that your chosen values move the wheels. The
number after `-` is the ESC's PCA9685 channel (`14` on the reference build, set by its patch as
`ROBOT_ESC_CHANNEL`).

```bash
brain/.venv/bin/python - 14 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

NEUTRAL = 0.25     # arms and stops (WP-1060)
FORWARD = 0.55     # slowest forward crawl
REVERSE = -0.01    # slowest reverse
ARM_S, MOVE_S = 2.0, 2.0

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 14
esc = ServoKit(channels=16).continuous_servo[ch]
esc.set_pulse_width_range(1000, 2000)

def hold(v, label, secs):
    print(f">> {label:12s} ch={ch:2d}  throttle={v:+.2f}  ({1500 + v*500:4.0f}us)  {secs}s")
    esc.throttle = v
    time.sleep(secs)

hold(NEUTRAL, "ARM/neutral", ARM_S)
hold(FORWARD, "FORWARD", MOVE_S)
hold(NEUTRAL, "STOP", MOVE_S)
hold(REVERSE, "REVERSE", MOVE_S)
hold(NEUTRAL, "STOP", 1.0)
PYEOF
```

## 2. ESC sweep

Steps the throttle in 0.05 increments, forward then reverse. Note the throttle where the wheels first creep
(your minimum) and a comfortable speed (your maximum). Power-cycle the ESC during the 5 s neutral at the start.

```bash
brain/.venv/bin/python - 14 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

NEUTRAL = 0.25
STEP, HOLD_S = 0.05, 1.2
FWD_MAX, REV_MAX = 0.95, -0.30

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 14
esc = ServoKit(channels=16).continuous_servo[ch]
esc.set_pulse_width_range(1000, 2000)

def hold(v, label, secs):
    print(f">> {label:12s} ch={ch:2d}  throttle={v:+.2f}  ({1500 + v*500:4.0f}us)  {secs}s")
    esc.throttle = v; time.sleep(secs)

hold(NEUTRAL, "ARM/neutral", 5.0)
v = NEUTRAL
while v <= FWD_MAX + 1e-9:
    hold(round(v, 2), f"fwd {v:+.2f}", HOLD_S); v += STEP
hold(NEUTRAL, "STOP", 1.0)
v = NEUTRAL
while v >= REV_MAX - 1e-9:
    hold(round(v, 2), f"rev {v:+.2f}", HOLD_S); v -= STEP
hold(NEUTRAL, "STOP", 1.0)
PYEOF
```

## 3. Steering sweep

Shows your current angles, then sweeps slowly toward each side. Note the angle where the wheels point straight,
and the furthest each way that turns fully without the servo buzzing. Ctrl-C the moment it buzzes. The number
after `-` is the steering channel (`ROBOT_STEER_CHANNEL`, `1` by default).

```bash
brain/.venv/bin/python - 1 <<'PYEOF'
import sys, time
from adafruit_servokit import ServoKit

CENTER, LEFT, RIGHT = 95, 135, 30   # your current angles, in degrees
MARGIN, STEP = 5, 5

ch = int(sys.argv[1]) if len(sys.argv) > 1 else 1
srv = ServoKit(channels=16).servo[ch]
srv.set_pulse_width_range(500, 2500)   # same as the robot, so angles map 1:1 to ROBOT_STEER_*
srv.actuation_range = 180

def go(a, label, hold=1.0):
    a = max(0.0, min(180.0, a))
    print(f">> {label:22s} angle={a:5.1f}deg")
    srv.angle = a; time.sleep(hold)

def sweep(target):
    end = target - MARGIN if target < CENTER else target + MARGIN
    d = STEP if end >= CENTER else -STEP
    a = CENTER
    while (a <= end) if d > 0 else (a >= end):
        go(a, f"  {a}")
        a += d

go(CENTER, f"current CENTER ({CENTER})", 1.5)
go(LEFT, f"current LEFT ({LEFT})", 1.5)
go(RIGHT, f"current RIGHT ({RIGHT})", 1.5)
go(CENTER, "back to centre", 1.2)
sweep(LEFT)
go(CENTER, "back to centre", 1.2)
sweep(RIGHT)
go(CENTER, "park at centre", 0.5)
PYEOF
```

When you're done: put the ESC numbers in your patch and run it, put the steering angles in
`config/robot.env`, then check everything with `./scripts/run_diagnostics.sh --wheels-off`.

## Programming the ESC

Optional, with the ESC's program card: plug the card onto the ESC's three-wire lead and power the ESC, then set the
reverse force to taste, the drag brake to about 70 to 80%, the low-voltage cut-off to 3.1 V per cell, and a low
start force. The ESC remembers the settings.

## How it says a name

Piper gets some names wrong. Give it a respelling and hear it in the robot's own voice, once alone and once in a
greeting:

```bash
./scripts/pronounce_my_name.sh Ruh-mesh                 # just listen
./scripts/pronounce_my_name.sh Ruh-mesh --name=Ramesh   # and save it as Ramesh's say-as in enroll_all.sh
```

After `--name`, run `bash enroll_all.sh` on the Pi to store it. Spell by sound, joined with hyphens
(`Ruh-mesh`, `Shah-nee`); never space out letters (`R uh m e sh`), which Piper reads as the alphabet. To fix a
name everywhere the robot says it, set `ROBOT_TTS_PRONUNCIATIONS="Ramesh=Ruh-mesh"` in `config/robot.env`.
Hear nothing? Run `./scripts/setup_audio.sh`.
