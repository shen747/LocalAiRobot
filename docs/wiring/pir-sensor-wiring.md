# PIR motion sensor wiring (Concierge Mode)

The optional **PIR (passive-infrared) motion sensor** is the extra wake trigger for
[Concierge Mode](../concierge.md): when someone walks up, it wakes Jarvis exactly like the wake word so it
can greet them. It's a **3-wire** sensor — power, ground, and one digital output — and it's **only used when
`ROBOT_CONCIERGE_MODE=1`** (in the normal crawler build the pin is never touched).

Reference part: **Adafruit PIR (ADA189)** — but any HC-SR501-style PIR works. Power it from **5 V** — **not**
the Pi's 3.3 V pin: a PIR under-powered at 3.3 V typically won't trigger (or reads stuck/erratic). Only its
**OUT is 3.3 V logic**, so the signal line is **Pi-safe with no level shifter**.

## Pins (default wiring)

| PIR pin (board label) | Connect to | Pi physical pin | Notes |
|---|---|---|---|
| **VCC / `+`** | **5 V** — ⚠️ **not 3.3 V** | **pin 2** (or 4) | powers the sensor; on 3.3 V it won't trigger reliably |
| **OUT** | **GPIO4** (`ROBOT_PIR_PIN=4`) | **pin 7** | the signal — HIGH on motion (this line *is* 3.3 V, Pi-safe) |
| **GND / `-`** | **GND** | **pin 6** (or 9/14/20/25/30/34/39) | common ground |

> ⚠️ **Power from 5 V, not 3.3 V.** VCC must go to a **5 V** header pin (**2** or **4**). On the Pi's 3.3 V
> rail most PIRs are unreliable — they miss motion or never fire. The **only** 3.3 V part is the **OUT** line
> (that's what makes the *signal* Pi-safe); it is not the supply voltage.

> **Match the board's silkscreen** (`+`/`OUT`/`-` or `VCC`/`OUT`/`GND`) — jumper-wire colours vary between
> sellers, so don't rely on colour alone.

```
   Raspberry Pi 40-pin header (top-right corner)
   ┌───────────────────────────
   │ [1]     [2] 5V ─────────────── VCC (+)   ┐
   │ [3]     [4] 5V                            │
   │ [5]     [6] GND ────────────── GND (−)    ├─  PIR sensor
   │ GPIO4 [7] ──────────────────── OUT        ┘
   │ [8] ...
```

Default: `ROBOT_PIR_PIN=4`. **GPIO4 (pin 7) is free in both the HC-SR04 and the VL53L1X builds** — it doesn't
clash with I²C (`GPIO2/3`), the ultrasonics (`23,24,5,6,20,21`), or the ToF XSHUT pins (`17,27,22`).

To use a different GPIO, wire OUT there and set it:
```bash
sed -i 's/^ROBOT_PIR_PIN=.*/ROBOT_PIR_PIN=<BCM>/' config/robot.env
```
Leave `ROBOT_PIR_PIN=` **empty** to run Concierge wake-word-only (no PIR).

> **Unwired is safe.** The input is pulled **down** internally, so if nothing is on the pin it reads LOW (no
> false wakes). You only need a real PIR on it for motion to do anything.

## Tuning the sensor

Most PIR modules have two trim pots and a jumper:

- **Sensitivity** — detection range/angle. Turn up if it misses people, down if it triggers on nothing.
- **Time delay** — how long OUT stays HIGH after a trigger. Keep it **short** so it re-arms quickly; a long
  delay leaves OUT stuck HIGH and the probe/robot sees continuous "motion".
- **Retrigger jumper (H / L)** — set to **H (repeat)** so it keeps firing while someone's present, rather
  than once per absence.

PIRs also need **~30–60 s to settle** after power-on before readings are reliable.

## Test it

Live monitor (wave your hand, watch it flip):
```bash
./scripts/pir_sensor_probe.sh          # uses ROBOT_PIR_PIN (GPIO4 by default)
./scripts/pir_sensor_probe.sh 17       # or probe a specific BCM pin while testing wiring
```
It prints `#### MOTION ####` / `... no motion ...` with a running detection count. If it **never flips**,
recheck VCC→5 V, GND→GND, OUT→the pin. If it's **stuck HIGH**, turn the time-delay pot down.

Then in the robot: with `ROBOT_CONCIERGE_MODE=1`, walk up — the log shows the wake and the friendly face
appears (see [docs/concierge.md](../concierge.md)).
