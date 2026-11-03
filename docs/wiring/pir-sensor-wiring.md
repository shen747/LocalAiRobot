# PIR motion sensor wiring

The greeter's motion sensor wakes the robot when someone walks up. It has three wires (power, ground and a
signal) and is only used in [Concierge mode](../concierge.md).

Two sensors are supported. They use the same signal pin and differ only in power:

| | HC-SR501 (hobby, or Adafruit PIR) | Panasonic PaPIRs EKMB1105112 |
|---|---|---|
| Power | **5 V, pin 2** | **3.3 V, pin 1. 5 V destroys it.** |
| Signal | OUT to GPIO 4, pin 7 | OUT to GPIO 4, pin 7 |
| Ground | pin 6 | pin 6 |
| Warm-up after power-on | under a minute | up to 210 s, which the robot waits out |
| Detection zones | about a dozen | 88 across 122° |
| Tuning | two knobs and a jumper | none; aim it well instead |
| Settings patch | `6_motion_pir_hcsr501.sh` (the defaults already match) | `6_motion_pir_papirs.sh` |

Both outputs are 3.3 V, so the signal is safe for the Pi without a level shifter. The HC-SR501 needs 5 V
because of its on-board regulator; on 3.3 V it misses people.

**Why upgrade to the PaPIRs?** A PIR only fires when someone crosses between its zones, and walking straight
at it barely crosses any. With 88 zones the PaPIRs catches head-on approaches that the HC-SR501 missed (it
scored 1 to 3 out of 12 head-on). A new sensor only fixes "nothing happens". If the face smiles and then goes
back to sleep, the PIR worked and the camera found no face: see [troubleshooting](../troubleshooting.md#face-recognition).

## Wire it

Power the Pi off first (`sudo poweroff`, wait for the green LED to stop), then:

```
   Pi 40-pin header (top-right corner)
   [1] 3.3V ─────────── VDD   (PaPIRs only)
   [2] 5V   ─────────── VCC   (HC-SR501 only)
   [6] GND  ─────────── GND
   [7] GPIO4 ────────── OUT
```

- Match the labels on the sensor (`+`/`OUT`/`-` or `VCC`/`OUT`/`GND`), not the wire colours. For the PaPIRs,
  read the pin order off the "Bottom view" drawing in its datasheet, and handle it by the body.
- Plain jumper wires are fine. Keep them under about 30 cm and away from motor wiring. On the PaPIRs, a
  0.1 µF capacitor across VDD and GND at the sensor helps if you see phantom wakes.
- GPIO 4 doesn't clash with I²C, the ultrasonic sensors or the laser sensors. To use another pin, wire OUT
  there and set `ROBOT_PIR_PIN=<BCM number>`. Leave it empty to wake by voice only.
- An unwired pin is safe: it's pulled low, so nothing on it means no wakes.

## Apply the settings

```bash
cd ~/LocalAiRobot
./scripts/patches/common/6_motion_pir_papirs.sh     # PaPIRs (add a BCM number to use a pin other than 4)
./scripts/patches/common/6_motion_pir_hcsr501.sh    # or back to the HC-SR501
./scripts/run_me.sh --reload
```

Both patches write the same settings, so switching either way is complete.

## Test it

```bash
./scripts/pir_sensor_probe.sh          # wave a hand and watch for #### MOTION ####; Ctrl-C stops it
journalctl -u robot.service -n 40 | grep 'PIR motion wake'
```

The robot log should end with `PIR motion wake: armed on BCM GPIO 4`. A PaPIRs logs `warming up for 210s`
first, and during that time it looks exactly like a dead sensor (the wake word still works). Bench testing
and don't want to wait? Set `ROBOT_PIR_WARMUP_S=30` and reload.

To read the raw pin with the robot out of the way:

```bash
sudo systemctl stop robot.service
pinctrl set 4 ip pd && watch -n 0.2 pinctrl get 4      # shows "| hi" or "| lo"; Ctrl-C to quit
```

| What you see | What it means |
|---|---|
| `lo` at rest, `hi` when you wave | the sensor works; look elsewhere |
| it wanders with nobody there | electrical noise: shorten the wires, move them from motor wiring, add the capacitor |
| never `hi` | power or pin order (PaPIRs power must be on pin 1) |
| the pin works but the robot logs nothing | `ROBOT_PIR_PIN` doesn't match the pin you wired |

## Where to point it

Where it points matters more than which sensor you bought.

- **Across the path people walk**, not straight down it.
- **Outside the enclosure, in still air.** One build logged 465 false wakes an hour from a PIR in the case's
  vent hole, watching the Pi's own warm air. Keep it away from heaters, air vents and sunny windows.
- **Nothing in front of the lens.** Glass and acrylic block infrared.
- **Hold it still**, with tape, hot glue or a clip, without pressing on the soft lens.
- Mounting through a panel? The lens needs to stick out at least 6.2 mm.

## HC-SR501 knobs

- **Sensitivity:** up if it misses people, down if it fires at nothing.
- **Time delay:** keep it short, or the output stays high and looks like constant motion.
- **Retrigger jumper:** set it to **H** so it keeps firing while someone is there.

False wakes on an HC-SR501 usually come from the sensitivity knob or the jumper.

## Why the PaPIRs patch sets what it sets

From the EKMB1105112 datasheet:

- `ROBOT_PIR_PULL=down`: the output only drives high and floats when idle, so the pin needs a pull-down or it
  invents visitors.
- `ROBOT_PIR_WARMUP_S=210`: the output is undefined for up to 210 s after power-on.
- `ROBOT_PIR_HOLD_S=0`: the spike filter guesses "person" from pulse length, and when pulses vary (a different
  model, a sagging supply) it silently drops every real visitor. The camera check filters noise better.
- After each trigger the sensor is blind for about 1.3 s, so triggers are at least ~3.9 s apart.
