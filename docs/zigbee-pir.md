# Wireless Zigbee PIR

A Zigbee motion sensor can wake the greeter as well as the wired PIR, whichever fires first. The wired sensor
keeps working exactly as before:

| You have | Result |
|---|---|
| Wired PIR only | unchanged; skip this page |
| Both | either one wakes it; two hits from the same arrival become one look |
| Wireless only (`ROBOT_PIR_PIN` empty) | the Zigbee sensor wakes it alone |
| Neither working | motion wake goes quiet, the wake word still works |

This buys coverage, not speed. A Zigbee event takes 50 to 300 ms to arrive, so for the same person the wired
sensor nearly always wins. **Put the wireless one where the wired one can't see**: round a corner, at the far
end of a hall, at the top of the stairs.

The reference setup is a **Sonoff ZBDongle-E** as the coordinator and any Zigbee 3.0 occupancy sensor.

## 1. Update the dongle's firmware

The dongle ships with EmberZNet 6.10.3, and Zigbee2MQTT's current `ember` adapter needs 7.4 or newer.

1. Flashing erases the Zigbee network. On a dongle already in use, back up Zigbee2MQTT's
   `data/coordinator_backup.json` first.
2. Open a Silabs web flasher in Chrome or Edge. Get the current link from Zigbee2MQTT's EmberZNet adapter docs.
3. Hold the dongle's **BOOT** button while plugging it in, then let go.
4. Flash the **`ncp-uart-hw`** image for **EFR32MG21**, version 7.4 or newer.
5. Replug it and check: `journalctl -u zigbee2mqtt -n 50 | grep -i 'ember\|version'`.

## 2. Give it a fixed device name

`/dev/ttyUSB0` can change when you plug in another serial device. Read your dongle's IDs:

```bash
udevadm info -a -n /dev/ttyUSB0 | grep -m3 -E 'idVendor|idProduct|serial'
```

Then add a rule with your values (`10c4:ea60` is usual for this dongle; the serial is yours):

```bash
sudo tee /etc/udev/rules.d/99-zigbee.rules >/dev/null <<'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", \
  ATTRS{serial}=="PUT_YOUR_SERIAL_HERE", SYMLINK+="ttyUSB_ZIGBEE", MODE="0660", GROUP="dialout"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/ttyUSB_ZIGBEE
sudo systemctl disable --now ModemManager   # it grabs new serial devices
```

## 3. Install the broker and Zigbee2MQTT

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo tee /etc/mosquitto/conf.d/local.conf >/dev/null <<'EOF'
listener 1883 127.0.0.1
allow_anonymous true
EOF
sudo systemctl enable --now mosquitto
```

The broker only listens on the Pi itself, because everything that uses it runs there. Install Zigbee2MQTT
following its own docs, then set this in its `data/configuration.yaml`:

```yaml
serial:
  port: /dev/ttyUSB_ZIGBEE
  adapter: ember
advanced:
  transmit_power: 20
  channel: 15          # 15, 20 or 25 sit between the Wi-Fi channels; pick the one furthest from yours
  network_key: GENERATE
frontend:
  port: 8080
permit_join: false
```

```bash
sudo systemctl enable --now zigbee2mqtt
```

Range tip: a short USB extension cable that gets the dongle 30 cm away from the Pi helps more than transmit
power. Use a USB 2.0 port if you can.

## 4. Pair the sensor

```bash
mosquitto_pub -h localhost -t zigbee2mqtt/bridge/request/permit_join -m '{"time":120}'
mosquitto_sub -h localhost -t 'zigbee2mqtt/#' -v       # trigger the sensor and watch
```

Rename it in the Zigbee2MQTT frontend (for example `pir_hallway`) and make sure you see `{"occupancy":true}`
arrive. Most battery sensors then stay "occupied" for 60 to 90 s before they can fire again, which is why
this adds to the wired PIR rather than replacing it.

## 5. Point the robot at it

```bash
cd ~/LocalAiRobot
./scripts/patches/common/6_wake_zigbee_pir.sh zigbee2mqtt/pir_hallway   # your topic
./scripts/run_me.sh --reload
```

The patch also installs `paho-mqtt`. Turn it off again with `--off`.

## 6. Check it

```bash
journalctl -u robot.service -n 40 | grep -E 'wake sources:|zigbee wake:'
```

You want `wake sources: gpio-pir OR zigbee-pir` and `zigbee wake: MQTT connected`.

| Symptom | Cause |
|---|---|
| no `wake sources:` line | `ROBOT_ZIGBEE_WAKE` isn't `1` |
| `[single source]` on that line | one sensor isn't available; the next line says which |
| `paho-mqtt not installed` | re-run the patch |
| connected but never wakes | wrong topic; check it with `mosquitto_sub` |
| `sensor went OFFLINE` | flat battery; the wired PIR carries on |

After a week, `journalctl -u robot.service | grep 'wake wins:'` shows which sensor woke it first. If the
wireless one never wins, it's watching the same spot as the wired one: move it.
