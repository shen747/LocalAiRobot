#!/usr/bin/env python3
# brain/calibrate_steering.py -- interactive STEERING SERVO calibrator (bench tool).
#
# Moves ONLY the steering servo on ROBOT_STEER_CHANNEL -- it never touches the ESC/motor channel, so it
# is SAFE to run with the wheels on the ground (steering doesn't drive the car). Nudge the servo live,
# watch the wheels, mark CENTER / LEFT / RIGHT, then write them straight back into config/robot.env.
#
# It replicates the driver's servo setup EXACTLY -- set_pulse_width_range(500, 2500) + actuation_range 180
# (see driver.py: self._steer_pulse / self._steer_actuation) -- so the angles you pick here map 1:1 to
# ROBOT_STEER_CENTER / ROBOT_STEER_LEFT / ROBOT_STEER_RIGHT that the driver reads.
#
# Launch via ./scripts/calibrate_steering.sh (sources config/robot.env, picks the venv python).

import argparse
import os
import re
import select
import shutil
import sys

# --- driver-matched servo geometry (keep in sync with brain/driver.py) ---
STEER_PULSE = (500, 2500)   # us; must match driver._steer_pulse so angles transfer 1:1
STEER_RANGE = 180           # deg; must match driver._steer_actuation
FINE, COARSE = 1.0, 5.0     # nudge steps (degrees)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENV_PATH = os.path.join(REPO, "config", "robot.env")

_TTY = sys.stdout.isatty()
try:
    import termios
    import tty
    _HAVE_TTY = True
except ImportError:                 # not a POSIX terminal (e.g. Windows dev box) -> line mode
    _HAVE_TTY = False


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def green(s): return _c("32", s)
def red(s): return _c("31", s)
def dim(s): return _c("2", s)
def bold(s): return _c("1", s)


def _envf(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def _fmt(v):
    """Whole degrees print as ints (95) so robot.env stays clean; else one decimal."""
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.1f}"


def _pulse_us(angle):
    lo, hi = STEER_PULSE
    return lo + (angle / STEER_RANGE) * (hi - lo)


# --- fake servo so the UI is usable off-Pi (no PCA9685); mirrors driver's dry-run fake ---
class _FakeChannel:
    def __init__(self):
        self.angle = None
        self.actuation_range = STEER_RANGE

    def set_pulse_width_range(self, a, b):
        pass


class _FakeKit:
    def __init__(self, channels=16):
        self.servo = [_FakeChannel() for _ in range(channels)]


def _make_kit(channels):
    """Real PCA9685 via adafruit_servokit; fall back to a fake (print-only) if it isn't present."""
    try:
        from adafruit_servokit import ServoKit
        return ServoKit(channels=channels), True
    except Exception as e:                       # no board / no lib / not on a Pi
        print(dim(f"   (no PCA9685: {e.__class__.__name__}) -- SIMULATED: angles print, nothing moves"))
        return _FakeKit(channels=channels), False


class Calibrator:
    def __init__(self, ch, channels):
        self.ch = ch
        self.kit, self.live = _make_kit(channels)
        self.kit.servo[ch].set_pulse_width_range(*STEER_PULSE)
        self.kit.servo[ch].actuation_range = STEER_RANGE
        self.center = _envf("ROBOT_STEER_CENTER", 90.0)
        self.left = _envf("ROBOT_STEER_LEFT", 55.0)
        self.right = _envf("ROBOT_STEER_RIGHT", 125.0)
        self.step = FINE
        self.angle = self.center
        self._apply()                            # park at the current CENTER (known-safe start)

    # ---- servo ----
    def _apply(self):
        self.angle = max(0.0, min(float(STEER_RANGE), self.angle))
        self.kit.servo[self.ch].angle = self.angle

    def nudge(self, d):
        self.angle += d
        self._apply()
        self.status()

    def goto(self, a):
        try:
            self.angle = float(a)
        except (TypeError, ValueError):
            print(red("  ? not a number"))
            return
        self._apply()
        self.status()

    def jump(self, name):
        self.angle = {"LEFT": self.left, "CENTER": self.center, "RIGHT": self.right}[name]
        self._apply()
        print(dim(f"  -> {name}"))
        self.status()

    # ---- marking ----
    def mark(self, name):
        setattr(self, name.lower(), self.angle)
        print(green(f"  set {name} = {_fmt(self.angle)} deg"))
        self.status()

    # ---- reporting ----
    def status(self):
        s = (f"  angle {bold(f'{self.angle:5.1f}')} deg ({_pulse_us(self.angle):4.0f}us)   "
             f"saved: L={_fmt(self.left)} C={_fmt(self.center)} R={_fmt(self.right)}   "
             f"step={_fmt(self.step)} deg")
        print(s)

    def print_vals(self):
        print(bold("\n  current calibration:"))
        print(f"    ROBOT_STEER_CENTER={_fmt(self.center)}")
        print(f"    ROBOT_STEER_LEFT={_fmt(self.left)}")
        print(f"    ROBOT_STEER_RIGHT={_fmt(self.right)}\n")

    # ---- persistence ----
    def write_env(self):
        if not os.path.exists(ENV_PATH):
            print(red(f"  config/robot.env not found at {ENV_PATH} -- copy the values in by hand:"))
            self.print_vals()
            return
        updates = {
            "ROBOT_STEER_CENTER": self.center,
            "ROBOT_STEER_LEFT": self.left,
            "ROBOT_STEER_RIGHT": self.right,
        }
        shutil.copy2(ENV_PATH, ENV_PATH + ".bak")
        with open(ENV_PATH) as f:
            lines = f.readlines()
        seen, out = set(), []
        for ln in lines:
            m = re.match(r"^(\s*)(ROBOT_STEER_(?:CENTER|LEFT|RIGHT))=(\S+)(.*)$", ln)
            if m and m.group(2) in updates:
                key = m.group(2)
                out.append(f"{m.group(1)}{key}={_fmt(updates[key])}{m.group(4)}\n")
                seen.add(key)
            else:
                out.append(ln)
        for k, v in updates.items():             # append any that weren't already present
            if k not in seen:
                out.append(f"{k}={_fmt(v)}\n")
        with open(ENV_PATH, "w") as f:
            f.writelines(out)
        print(green(f"  wrote {ENV_PATH}  (backup: {os.path.basename(ENV_PATH)}.bak)"))
        print(dim("  re-source it (or ./scripts/run_me.sh --reload) for the running robot to pick it up."))


LEGEND = f"""
{bold('Steering calibrator')} -- moves ONLY the steering servo (safe with wheels on the ground).
  {bold('a / d')}   nudge  -1{chr(176)} / +1{chr(176)}     ({bold('<- / ->')} arrows too)
  {bold('A / D')}   nudge  -5{chr(176)} / +5{chr(176)}     ({bold('up / down')} arrows too)
  {bold('g')}       go to a typed angle (0-180)
  {bold('c')}       mark the CURRENT angle as {bold('CENTER')}  (wheels dead ahead)
  {bold('l / r')}   mark the CURRENT angle as {bold('LEFT / RIGHT')}  (full lock, NO buzzing/binding)
  {bold('1 2 3')}   jump to saved  LEFT / CENTER / RIGHT
  {bold('p')}       print the values     {bold('w')}  write them into config/robot.env (.bak first)
  {bold('?')}       show this help        {bold('q')}  quit (prints the final values)
{dim('TIP: creep up on LEFT/RIGHT -- back off the moment the servo buzzes or the linkage binds.')}
"""


def _read_key():
    """One keypress (raw/cbreak). Decodes the four arrow keys to UP/DOWN/LEFT/RIGHT tokens."""
    fd = sys.stdin.fileno()
    b = os.read(fd, 1)
    if not b:
        return None
    ch = b.decode("utf-8", "ignore")
    if ch == "\x1b":                             # ESC: could be a bare Esc or an arrow sequence
        r, _, _ = select.select([fd], [], [], 0.06)
        if not r:
            return "ESC"
        seq = os.read(fd, 2).decode("utf-8", "ignore")
        return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(seq, "ESC")
    return ch


def key_loop(cal):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def ask(prompt):                             # drop to cooked mode for a typed line, then back
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        try:
            return input(prompt)
        finally:
            tty.setcbreak(fd)

    print(LEGEND)
    cal.status()
    try:
        tty.setcbreak(fd)
        while True:
            k = _read_key()
            if k in ("q", "\x04", None):          # q / Ctrl-D
                break
            elif k in ("a", "LEFT"):
                cal.nudge(-FINE)
            elif k in ("d", "RIGHT"):
                cal.nudge(+FINE)
            elif k in ("A", "DOWN"):
                cal.nudge(-COARSE)
            elif k in ("D", "UP"):
                cal.nudge(+COARSE)
            elif k == "g":
                cal.goto(ask("  go to angle (0-180): "))
            elif k == "c":
                cal.mark("CENTER")
            elif k == "l":
                cal.mark("LEFT")
            elif k == "r":
                cal.mark("RIGHT")
            elif k == "1":
                cal.jump("LEFT")
            elif k == "2":
                cal.jump("CENTER")
            elif k == "3":
                cal.jump("RIGHT")
            elif k == "p":
                cal.print_vals()
            elif k == "w":
                if ask("  write these into config/robot.env? [y/N] ").strip().lower() == "y":
                    cal.write_env()
                else:
                    print(dim("  (not written)"))
            elif k in ("?", "h"):
                print(LEGEND)
            # any other key: ignore
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print()
    cal.print_vals()


def line_loop(cal):
    """Fallback when stdin isn't a TTY: one command per line."""
    print(LEGEND)
    print(dim("  (line mode) type: a number = go there | +/- [n] = nudge | c/l/r | 1/2/3 | p | w | q"))
    cal.status()
    for raw in sys.stdin:
        cmd = raw.strip()
        if not cmd:
            continue
        low = cmd.lower()
        if low in ("q", "quit", "exit"):
            break
        elif low == "p":
            cal.print_vals()
        elif low == "w":
            cal.write_env()
        elif low in ("c", "l", "r"):
            cal.mark({"c": "CENTER", "l": "LEFT", "r": "RIGHT"}[low])
        elif low in ("1", "2", "3"):
            cal.jump({"1": "LEFT", "2": "CENTER", "3": "RIGHT"}[low])
        elif cmd[0] in "+-":
            try:
                cal.nudge(float(cmd) if len(cmd) > 1 else (FINE if cmd == "+" else -FINE))
            except ValueError:
                print(red("  ? use +, -, +5, -5 ..."))
        else:
            cal.goto(cmd)
    cal.print_vals()


def main():
    ap = argparse.ArgumentParser(description="Interactive steering-servo calibrator (bench tool).")
    ap.add_argument("-c", "--channel", type=int, default=int(_envf("ROBOT_STEER_CHANNEL", 1)),
                    help="PCA9685 channel the steering servo is on (default: ROBOT_STEER_CHANNEL)")
    ap.add_argument("--channels", type=int, default=int(_envf("ROBOT_PCA9685_CHANNELS", 16)),
                    help="PCA9685 channel count (default: ROBOT_PCA9685_CHANNELS)")
    args = ap.parse_args()

    print(bold(f"\nSteering servo on PCA9685 channel {args.channel}  "
               f"(range {STEER_PULSE[0]}-{STEER_PULSE[1]}us / {STEER_RANGE} deg, matches the driver)"))
    cal = Calibrator(args.channel, args.channels)

    if _HAVE_TTY and sys.stdin.isatty():
        key_loop(cal)
    else:
        line_loop(cal)


if __name__ == "__main__":
    main()
