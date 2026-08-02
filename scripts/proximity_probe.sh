#!/usr/bin/env bash
# proximity_probe.sh -- live VCNL4200 read-out, for picking ROBOT_PROXIMITY_NEAR / _FAR.
#
# The VCNL4200 reports a RAW REFLECTED-IR COUNT, not centimetres. The number rises as something gets closer,
# but how fast depends on the surface: a white shirt at 80cm can read higher than a black wool coat at 40cm.
# So the thresholds are not a distance you can look up -- they are two numbers you MEASURE in the doorway
# where the sensor will live, wearing the kind of clothes people actually wear.
#
#   ./scripts/proximity_probe.sh              # live counts, 10/s
#   ./scripts/proximity_probe.sh 0.5          # slower sampling
#
# HOW TO PICK THE TWO NUMBERS
#   1. Run it with the doorway EMPTY. Note the resting count -- that's the floor (walls/floor reflect a bit).
#   2. Walk up and stop where you want to be greeted. Note the count. Do it in a DARK top as well as a light
#      one; take the LOWER reading, or people in dark coats won't be seen.
#   3. ROBOT_PROXIMITY_NEAR = comfortably above the empty-room floor, at/below your darkest-clothing reading.
#   4. ROBOT_PROXIMITY_FAR  = roughly halfway between the floor and NEAR. The gap is HYSTERESIS: a visitor
#      hovering exactly on one threshold would otherwise machine-gun wakes.
#
# STOP THE BRAIN FIRST -- it holds the I2C sensor:  sudo systemctl stop robot.service
set -uo pipefail
REPO="$(cd "$(dirname "$0")" && cd .. && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"
[ -x "$PY" ] || { echo "brain venv not found ($PY) -- run ./scripts/install_stack_pi5.sh first." >&2; exit 1; }

if pgrep -f 'python.*brain/main\.py' >/dev/null 2>&1; then
  echo "!! The brain is running and holding the sensor. Stop it first:" >&2
  echo "     sudo systemctl stop robot.service     (then: sudo systemctl start robot.service)" >&2
  exit 1
fi

exec "$PY" - "$@" <<'PYEOF'
import os, sys, time
try:
    import board, adafruit_vcnl4200
except Exception as e:
    print(f"driver/Blinka missing ({e})", file=sys.stderr)
    print("  brain/.venv/bin/pip install adafruit-circuitpython-vcnl4200", file=sys.stderr)
    sys.exit(1)

interval = 0.1
for a in sys.argv[1:]:
    if a in ("-h", "--help"):
        print("usage: proximity_probe.sh [seconds_between_reads]"); sys.exit(0)
    try: interval = max(0.02, float(a))
    except ValueError: print(f"ignoring {a!r}", file=sys.stderr)

try:
    s = adafruit_vcnl4200.Adafruit_VCNL4200(board.I2C())
    s.proximity_shutdown = False          # the chip powers up with the proximity sensor DISABLED
except Exception as e:
    print(f"no VCNL4200 on the I2C bus ({e})", file=sys.stderr)
    print("  check: raspi-config -> Interface -> I2C enabled; SDA->pin3, SCL->pin5, VIN->3.3V, GND->GND",
          file=sys.stderr)
    print("  then:  i2cdetect -y 1     (expect a device at 0x51)", file=sys.stderr)
    sys.exit(1)

def envi(k, d):
    try: return int(float(os.environ.get(k, "") or d))
    except (TypeError, ValueError): return d
near, far = envi("ROBOT_PROXIMITY_NEAR", 300), envi("ROBOT_PROXIMITY_FAR", 200)

print(f"VCNL4200 live. Current thresholds: NEAR>={near} (arrive), FAR<={far} (leave).")
print("Empty doorway first -> note the floor. Then walk up -> note the count. Ctrl-C to stop.\n")
print(f"  {'time':8}  {'raw':>6}   {'':32} state")

lo, hi, n, state = None, 0, 0, False
try:
    while True:
        try:
            v = int(s.proximity)
        except Exception as e:
            print(f"  read failed: {e}"); time.sleep(interval); continue
        n += 1
        lo = v if lo is None else min(lo, v)
        hi = max(hi, v)
        if not state and v >= near: state = True
        elif state and v <= far:    state = False
        # a 32-char bar scaled to the biggest value seen so far -- makes an approach obvious at a glance
        bar = "#" * min(32, int(32 * v / max(hi, 1)))
        tag = "NEAR (would wake)" if state else ("" if v < far else "between")
        print(f"  {time.strftime('%H:%M:%S')}  {v:6d}   {bar:32} {tag}")
        time.sleep(interval)
except KeyboardInterrupt:
    print(f"\nstopped after {n} reads.  min={lo}  max={hi}")
    if lo is not None and hi > lo:
        sug_near = lo + int(0.6 * (hi - lo))
        sug_far = lo + int(0.3 * (hi - lo))
        print(f"\nFrom THIS session (only valid if it included both an empty doorway and a real approach):")
        print(f"  ROBOT_PROXIMITY_NEAR={sug_near}")
        print(f"  ROBOT_PROXIMITY_FAR={sug_far}")
        print("Re-check the NEAR figure wearing something dark before you trust it.")
PYEOF
