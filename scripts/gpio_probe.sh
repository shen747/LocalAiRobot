#!/usr/bin/env bash
# gpio_probe.sh -- low-level HC-SR04 probe + pin scanner (bypasses gpiozero's DistanceSensor).
#
#   ./scripts/gpio_probe.sh              # test the 3 configured sensors once each
#   ./scripts/gpio_probe.sh --step       # test ONE sensor at a time (ENTER = retest, n = next, q = quit)
#   ./scripts/gpio_probe.sh --scan       # WORK BACKWARDS: pulse each candidate TRIG, watch every GPIO,
#                                        #   print which pin the echo actually appears on (finds mis-wires)
#   ./scripts/gpio_probe.sh --tries N    # readings per sensor (default 5)
#
# Use it when test_sensors.sh / run_diagnostics.sh say "no echo" but the pins look wired:
#   [OK] here but no-echo there -> wiring/sensor fine; gpiozero's Pi-5 timing is to blame.
#   [--] here too               -> real fault: TRIG jumper, ECHO/VCC/GND, or a dead sensor.
# Hold a target ~20-50 cm ahead for the per-sensor tests, ~0.5-1 m for --scan. Pins come from config/robot.env.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "brain venv not found ($PY) -- run ./scripts/install_stack.sh on the Pi first." >&2
  exit 1
fi

exec "$PY" - "$@" <<'PYEOF'
import os, sys, time
from gpiozero import DigitalOutputDevice, DigitalInputDevice

g = lambda n, d: int(os.environ.get(n, d) or d)
SENSORS = [
    ("front", g("ROBOT_TRIG_PIN", 23),       g("ROBOT_ECHO_PIN", 24)),
    ("left",  g("ROBOT_LEFT_TRIG_PIN", 5),   g("ROBOT_LEFT_ECHO_PIN", 6)),
    ("right", g("ROBOT_RIGHT_TRIG_PIN", 20), g("ROBOT_RIGHT_ECHO_PIN", 21)),
]
# BCM -> physical header pin (J8), so the report points straight at the connector.
PHYS = {2:3,3:5,4:7,5:29,6:31,7:26,8:24,9:21,10:19,11:23,12:32,13:33,14:8,15:10,
        16:36,17:11,18:12,19:35,20:38,21:40,22:15,23:16,24:18,25:22,26:37,27:13}
ph = lambda n: PHYS.get(n, "?")
# Pins safe to scan (the sensor pins + common alternates); excludes I2C(2,3)/SPI0(7-11)/UART(14,15).
SCAN = [4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]

args = sys.argv[1:]


def opt_int(flag, default):
    if flag in args:
        i = args.index(flag) + 1
        if i < len(args):
            try:
                return int(args[i])
            except ValueError:
                pass
    return default


TRIES = opt_int("--tries", 5)


def one(tp, ep, tries):
    """Raw trigger+echo on a single sensor -> list of cm (None per try that gave no echo)."""
    trig = DigitalOutputDevice(tp); echo = DigitalInputDevice(ep); out = []
    try:
        for _ in range(tries):
            trig.off(); time.sleep(0.003); trig.on(); time.sleep(11e-6); trig.off()
            t0 = time.time()
            while echo.value == 0 and time.time() - t0 < 0.04:
                pass
            if echo.value == 0:
                out.append(None); time.sleep(0.06); continue
            rise = time.time()
            while echo.value == 1 and time.time() - rise < 0.04:
                pass
            out.append((time.time() - rise) * 34300.0 / 2.0); time.sleep(0.06)
    finally:
        trig.close(); echo.close()
    return out


def report(name, tp, ep, hits):
    tag = f"TRIG GPIO{tp}(pin{ph(tp)}) / ECHO GPIO{ep}(pin{ph(ep)})"
    got = [h for h in hits if h is not None]
    if got:
        print(f"[OK] {name:<5} {tag}: ECHO responded {len(got)}/{len(hits)} -> ~{sum(got)/len(got):.0f} cm")
        return True
    print(f"[--] {name:<5} {tag}: NO echo in {len(hits)} tries -- check TRIG jumper, ECHO/VCC/GND, or swap the sensor")
    return False


def run_all():
    print("Raw HC-SR04 probe (bypasses gpiozero). Hold a target ~20-50 cm ahead.\n")
    for name, tp, ep in SENSORS:
        report(name, tp, ep, one(tp, ep, TRIES))


def run_step():
    print("One sensor at a time.  ENTER = (re)test  ·  n = next  ·  q = quit.\n")
    for name, tp, ep in SENSORS:
        while True:
            try:
                c = input(f">> {name.upper()} (TRIG GPIO{tp}/pin{ph(tp)}, ECHO GPIO{ep}/pin{ph(ep)}) -- "
                          f"aim it, ENTER to test [n=next, q=quit]: ").strip().lower()
            except EOFError:
                return
            if c == "q":
                return
            if c == "n":
                break
            if report(name, tp, ep, one(tp, ep, TRIES)):
                print(f"   {name} works -- ENTER to retest, or n for the next sensor.")


def run_scan():
    print("SCAN (work backwards): pulsing each candidate TRIG, watching every candidate GPIO for the echo.")
    print(f"Hold a target ~0.5-1 m in front of ONE sensor at a time.  Candidate pins: {SCAN}\n")
    found = []
    for tp in SCAN:
        trig = DigitalOutputDevice(tp)
        echoes = {p: DigitalInputDevice(p) for p in SCAN if p != tp}
        try:
            trig.off(); time.sleep(0.003); trig.on(); time.sleep(11e-6); trig.off()
            seen, t0 = set(), time.time()
            while time.time() - t0 < 0.05:                 # ~50 ms window (hold target ~1 m)
                for p, dev in echoes.items():
                    if dev.value:
                        seen.add(p)
            for p in sorted(seen):
                print(f"  TRIG GPIO{tp}(pin{ph(tp)})  ->  ECHO on GPIO{p}(pin{ph(p)})")
                found.append((tp, p))
        finally:
            trig.close()
            for dev in echoes.values():
                dev.close()
    if not found:
        print("  (no echo on any pin -- is the sensor powered + aimed at a target ~1 m away on a clear path?)")
    else:
        print("\nMatch the pairs above to config/robot.env (ROBOT_*_TRIG_PIN / ROBOT_*_ECHO_PIN).")


if "-h" in args or "--help" in args:
    print("usage: gpio_probe.sh [--step] [--scan] [--tries N]")
elif "--scan" in args:
    run_scan()
elif "--step" in args:
    run_step()
else:
    run_all()
PYEOF
