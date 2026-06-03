# Assembly & Wiring Notes

On-the-bench build & wiring reference for the LocalAiRobot RC crawler. Pairs with the setup steps in
the [README](../README.md). Capture your own photos as you go.

> 📷 Drop your own annotated wiring photo/diagram at `docs/wiring.png` when you have one
> (binary assets are intentionally not committed).

---

## Two isolated power domains

**Never bridge the two batteries.** But **do** tie all the *grounds* together, or the PWM signals from
the Pi won't register at the ESC/servo.

![Two isolated power domains: 2S 7.4V LiPo -> ESC -> drive motor (motor power) and UPS HAT -> Raspberry Pi 5 (brain power); the two battery positives are never bridged, but all grounds are tied common across Pi, PCA9685 and ESC](images/assembly.png)

For the full picture — where the two domains *legitimately* meet (shared ground, plus the ESC's **6 V
BEC** feeding the PCA9685 V+ rail and the steering servo), and where the HC-SR04P sits:

![Detailed power domains: 2S 7.4V LiPo -> ESC -> drive motor, and the ESC 6V/3A BEC -> PCA9685 V+ rail -> DS3225 steering servo (servo power stays inside the LiPo/motor domain); UPS HAT -> Raspberry Pi 5 -> PCA9685 3.3V logic + I2C and the HC-SR04P; every device shares a common ground bus, while the two battery positives are never bridged](images/power-domains.png)

- ✅ Match the LiPo plug (XT60 / T-plug / Deans) to the ESC; don't solder if you can pick the right one.
- ✅ The Pi 5 **Active Cooler is mandatory** — sustained LLM inference thermal-throttles without it.
- ✅ A **LiPo balance charger** is required for the 2S pack; never charge LiPo on a dumb charger.
- 🔴 Put a **physical kill switch / unplug loop** on the LiPo→ESC line within reach. With no hardware
  watchdog (the Pico is gone), this is your last-resort stop if the software dies. See the safety note below.

---

## The PCA9685 — the PWM bridge

The Pi 5 cannot cleanly bit-bang three+ servo signals while running an LLM, so a PCA9685 generates them
over I2C. Wire it like this:

| PCA9685 pin | Connect to | Why |
|---|---|---|
| `VCC` (logic) | Pi **3.3V** | chip logic + I2C level (matches the Pi's 3.3V I2C) |
| `SDA` | Pi **GPIO2 / SDA** | I2C data |
| `SCL` | Pi **GPIO3 / SCL** | I2C clock |
| `GND` | Pi **GND** | common ground (also shared with the ESC) |
| `V+` (servo rail) | **ESC 6V BEC** (the red wire of the ESC's servo plug) | powers the steering servo — **not** from the Pi |
| Channel 0 (signal/V+/GND) | **ESC signal plug** | throttle PWM to the ESC (+ BEC feeds V+, + GND) |
| Channel 1 (signal/V+/GND) | **steering servo** | steering PWM + 6V power + GND |

Notes:
- The ESC's 3-wire plug (the one that normally goes to an RC receiver's throttle channel) carries
  **signal + 6V BEC + GND**. Signal → PCA9685 ch0 signal; BEC red → PCA9685 V+ rail; GND → PCA9685 GND.
- Default I2C address is **0x40** (`i2cdetect -y 1`). If you stacked boards/changed solder jumpers,
  update the driver accordingly.
- The DS3225 is a hungry servo (stall ~2.5–3A). The QuicRun's 6V/3A BEC is enough for normal steering;
  if you see brownouts under hard steering load, add a separate servo BEC on the V+ rail.

---

## Distance sensor — HC-SR04P (3.3 V, no level shifter)

The BOM uses the **HC-SR04P** (the 3.3 V variant). Powered from the Pi's **3.3 V** rail, its **ECHO**
line stays at 3.3 V, so it wires **straight to the Pi's GPIO — no divider, no level shifter** (this
matches the README).

Wiring (defaults in `config/robot.env`):

| HC-SR04P | Connect to |
|---|---|
| `VCC` | Pi **3.3V** |
| `GND` | Pi **GND** |
| `TRIG` | Pi **GPIO23** |
| `ECHO` | Pi **GPIO24** — Pi-safe at 3.3 V, no divider |

Mount it forward-facing, clear of the wheels and chassis, roughly level.

> ⚠️ **Bare HC-SR04 fallback.** A non-P **HC-SR04** wants **5 V** on `VCC` and then drives its **ECHO at
> 5 V** — and the Pi's GPIO is **NOT 5 V-tolerant**. If that's all you have, power `VCC` from Pi **5V** and
> add a divider on `ECHO` only: **1 kΩ** from ECHO → the GPIO pin, **2 kΩ** from that pin → GND
> (5 V → ~3.3 V). `TRIG` driven by the Pi's 3.3 V output is fine either way.

---

## Mechanical build

1. Assemble the 1/10 roller chassis (or start from your built RTR): drivetrain, portal axles, links,
   shocks, 1.9" wheels. Mount the **540 35T motor** into the transmission; mesh the pinion/spur with a
   sliver of slack (a strip of paper between the gears while you tighten sets the backlash).
2. Mount the **steering servo** to the front axle servo mount; connect the steering link. Leave the
   horn off until you've found the servo's center in calibration, then attach it pointing straight.
3. Build the brain stack: Pi 5 + active cooler + UPS HAT. Mount on a 3D-printed/cut deck on the frame
   rails (the SCX10 313 mm form factor has many free community trays). Keep fan airflow clear.
4. Mount the **ESC**, **PCA9685**, and **LiPo** on the chassis; route wiring clear of the driveshafts
   and steering links. Keep the mic away from the cooler fan to reduce recorded noise.

---

## OS / host setup quick refs

```bash
# Mic device id  →  put the plughw string in config/robot.env (ROBOT_MIC)
arecord -l

# Audio sanity check (same 16 kHz mono S16_LE the app uses)
arecord -D plughw:1,0 -d 3 -f S16_LE -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav

# Confirm I2C is on and the PCA9685 is visible
i2cdetect -y 1        # expect a device at 0x40
```

---

## Bring-up order — keep the wheels OFF the ground until the very end

1. **Bus check:** `i2cdetect -y 1` shows `0x40`.
2. **ESC arm:** with the LiPo connected, the brain holds neutral for `ROBOT_ESC_ARM_SECS` on start — the
   ESC should beep its arming tones. No arm = check signal/GND to PCA9685 ch0 and the BEC on V+.
3. **Steering:** run the brain, say *"center" / "left" / "right"*; tune `ROBOT_STEER_CENTER/LEFT/RIGHT`
   so the wheels center true and turn fully without the servo buzzing (binding = back the angle off).
4. **Throttle direction:** say *"go forward"* slowly. Backwards? Set `ROBOT_THROTTLE_INVERT=1` (or swap
   the motor wires). Reverse may need the ESC put in a forward/reverse mode (see the ESC manual).
5. **Obstacle gate:** *"go forward"* + hand ~20 cm ahead of the HC-SR04 → motor cuts, robot says
   *"Obstacle ahead. Stopping."*
6. **On the floor:** only now, at low `ROBOT_MAX_THROTTLE`, with the kill switch in hand.

---

## Safety note — software failsafe, not hardware

`driver.py` runs a control thread that forces the ESC to **neutral** on STOP, on error, on a forward
obstacle, and when a move's `duration_ms` deadline passes; the ESC is armed at neutral on boot. This
replaces the old Pico hardware watchdog **in software only** — if the whole Python process is killed,
the PCA9685 keeps emitting the last pulse and nothing in hardware stops the motor. The **physical kill
switch on the LiPo is your real safety net.** Keep `ROBOT_MAX_THROTTLE` low and durations short.
