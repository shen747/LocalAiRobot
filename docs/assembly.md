# Assembly & Wiring Notes

On-the-bench build & wiring reference for LocalAiRobot. Pairs with the setup steps in the
[README](../README.md). Capture your own photos as you go.

> 📷 Drop your own annotated wiring photo/diagram at `docs/wiring.png` when you have one
> (binary assets are intentionally not committed).

---

## Two independent power domains

Never bridge these. The Pico is powered and data-linked over the single USB cable from the Pi.

```
 car battery pack ──▶ motors + Pico                (motor power)
 GeeekPi UPS (2× 18650) ──▶ Raspberry Pi 5         (brain power)
 Pi 5 USB-A ──── micro-USB ───▶ Pico W             (command/data link, 115200 8N1)
```

- ✅ Verify cell **polarity and voltage with a multimeter** before inserting 18650s. A reversed cell can destroy the Pi.
- ✅ Confirm the UPS HAT's 5V output is rated **≥ 4A** — many 18650 HATs only do ~2.5–3A and will brown out a Pi 5 under LLM load.
- ✅ The Pi 5 **Active Cooler is mandatory** — sustained LLM inference thermal-throttles without it.

---

## Mechanical build

1. Build the Freenove car base: motors, mecanum wheels, main PCB; seat the Pico W in its socket.
   - **Roller direction matters.** Front-left & rear-right are a mirror pair of front-right & rear-left.
     Mounting them wrong breaks strafing — match Freenove's diagram exactly.
2. Attach the active cooler to the Pi 5; mount the UPS HAT; insert the 18650s (polarity!).
3. Mount the brain stack on the top deck (M2.5 standoffs or heavy-duty Velcro). Keep the fan airflow clear.
4. Plug in: USB mic + speaker → Pi USB; **Pi USB-A → Pico micro-USB**.
5. Zip-tie cables clear of the wheels; keep the mic away from the fan to reduce recorded noise.

**Verify:** car rolls freely by hand; Pi boots from the UPS; Pico's onboard LED matches Freenove's "powered" state.

---

## ⚠️ HC-SR04: 5V on a 3.3V pin

A bare HC-SR04 drives its **ECHO line at 5V**. The RP2040 GPIO is **NOT 5V-tolerant** — wiring 5V ECHO
straight to a Pico pin can damage the board. Pick **one**:

- Use the Freenove PCB's **level-shifted ultrasonic header** (easiest), **or**
- Add a divider: **1kΩ** from ECHO → the GPIO pin, **2kΩ** from that pin → GND (5V → ~3.3V), **or**
- Use a **3.3V HC-SR04P** module.

---

## Pin mapping

The pin numbers in [`firmware/main.py`](../firmware/main.py) are **placeholders** — set them to match
your Freenove board revision's schematic before the car will move correctly.

| Signal | Default in firmware | Set to your board's… |
|---|---|---|
| FL motor (fwd / bwd) | GP2 / GP3 | front-left driver pins |
| FR motor | GP4 / GP5 | front-right driver pins |
| RL motor | GP6 / GP7 | rear-left driver pins |
| RR motor | GP8 / GP9 | rear-right driver pins |
| Ultrasonic TRIG | GP16 | TRIG pin (3.3V OK) |
| Ultrasonic ECHO | GP17 | ECHO pin (**level-shifted!**) |

Tuning constants also live at the top of the firmware: `SPEED` (default duty),
`OBSTACLE_CM` (forward stop distance), `MOVE_TIMEOUT_MS` (watchdog).

---

## OS / host setup quick refs

```bash
# Mic device id  →  put the plughw string in config/robot.env (ROBOT_MIC)
arecord -l

# Audio sanity check (SAME format the app uses: 16 kHz mono S16_LE)
arecord -D plughw:1,0 -d 3 -f S16_LE -r 16000 -c 1 /tmp/test.wav && aplay /tmp/test.wav

# Pico serial port + serial access without sudo (re-login after)
ls /dev/ttyACM* /dev/ttyUSB*
sudo usermod -aG dialout $USER
```

---

## Bring-up order — keep wheels OFF the ground until step 6

1. **Pico alone** (Pico REPL — MicroPico in VS Code, or Thonny): type `FWD`, `STOP`, `DIST?`; confirm each direction and that strafe slides (not rotates).
2. **Serial loopback from the Pi**: close Thonny first, then `screen /dev/ttyACM0 115200` → `PING`→`PONG`, `FWD` → `LOG watchdog stop` after ~1.5 s.
3. **STT alone**: record with the fan running and transcribe.
4. **LLM alone**: confirm Ollama returns clean JSON.
5. **TTS alone**: `echo "test" | ~/piper/piper/piper -m ~/piper/en_US-lessac-medium.onnx --output_file /tmp/t.wav && aplay /tmp/t.wav`
6. **Full loop on blocks**: run `./brain`, press ENTER, say "drive forward". Only after this passes, put the car on the floor.
