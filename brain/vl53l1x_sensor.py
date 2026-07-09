# brain/vl53l1x_sensor.py -- VL53L1X laser Time-of-Flight distance backend (I2C).
#
# The alternative to the 3 HC-SR04P ultrasonics, selected by ROBOT_SENSOR_TYPE=vl53l1x. Drop-in for the
# subset of gpiozero.DistanceSensor that driver.py uses: a `.distance` property in METRES + a `.close()`.
# Like the HC-SR04 backends (and for the SAME reason -- never block the 20 Hz safety loop) a background
# daemon thread does the ranging while `.distance` just returns the most recent cached reading.
#
# Three VL53L1X all power up at I2C 0x29, so assign_addresses() re-addresses them one at a time using their
# XSHUT pins, driven board-neutrally through Board.make_digital_output() -- so this module imports NO GPIO
# library directly (parity: pure I2C on Pi 5 and Jetson).
#
# FAIL CLOSED: a read error/timeout caches a NEGATIVE distance, so driver.distance_cm() returns its -1.0
# "read failed" sentinel and the FORWARD gate treats it as BLOCKED (the SEN-04/05 safety rule -- an errored
# sensor must never read "clear"). "No target in range" is a legitimate CLEAR (like an ultrasonic no-echo)
# and caches max_distance.
#
# adafruit-circuitpython-vl53l1x + Blinka are imported LAZILY, so importing this module is safe on any
# machine; only ROBOT_SENSOR_TYPE=vl53l1x on live hardware ever constructs a sensor.

import logging
import os
import threading
import time

log = logging.getLogger("robot.hw")


def make_i2c():
    """Board-neutral I2C bus for the ToF sensors, mirroring boards/jetson.py's make_pwm_kit logic:
    ROBOT_I2C_BUS set -> ExtendedI2C(bus) (the Jetson 40-pin header is ~bus 7); else Blinka's board default
    (Pi = bus 1). Lazy imports so the module stays import-safe off-hardware."""
    bus = os.environ.get("ROBOT_I2C_BUS", "").strip()
    if bus:
        from adafruit_extended_bus import ExtendedI2C
        return ExtendedI2C(int(bus))
    import board
    import busio
    return busio.I2C(board.SCL, board.SDA)


def assign_addresses(i2c, plan, make_output):
    """Give three VL53L1X distinct I2C addresses. `plan` = [(xshut_pin, new_addr), ...]. All units boot at
    0x29, so: hold EVERY XSHUT low (all sensors in reset), then bring them up ONE at a time and re-address
    each. `make_output(pin)` is Board.make_digital_output -> a DigitalOutputDevice-like object exposing
    .on()/.off()/.close(). Returns the list of XSHUT handles -- KEEP them alive, releasing an XSHUT powers
    its sensor down. Safe to re-run (every sensor is pulled low first, so re-addressing from 0x29 is
    deterministic)."""
    import adafruit_vl53l1x
    xshuts = [make_output(pin) for pin, _addr in plan]
    for x in xshuts:
        x.off()                                  # all sensors held in reset (only 0x29 would answer otherwise)
    time.sleep(0.05)
    for (pin, addr), x in zip(plan, xshuts):
        x.on()                                   # power up just THIS sensor (it comes up at the default 0x29)
        time.sleep(0.05)
        s = adafruit_vl53l1x.VL53L1X(i2c)        # talk to it at 0x29
        if addr != 0x29:
            s.set_address(addr)                  # move it so the next sensor can claim 0x29
        time.sleep(0.02)
    return xshuts


class VL53L1XSensor:
    """One VL53L1X beam. `.distance` is METRES (driver.py multiplies by 100). A daemon thread polls at
    ~50 Hz into a cached float; `.distance` returns the cache without blocking. See the module header for
    the fail-closed contract."""

    def __init__(self, i2c, address, max_distance=1.0, period=0.02):
        import adafruit_vl53l1x
        self.max_distance = float(max_distance)
        self._period = period
        self._distance = self.max_distance       # start CLEAR so a not-yet-read sensor doesn't block at boot
        self._closed = False
        self._vl = adafruit_vl53l1x.VL53L1X(i2c, address=address)
        try:
            self._vl.distance_mode = 2           # 2 = long range (~4 m); 1 = short
            self._vl.timing_budget = 20          # ms integration window -> ~50 Hz
        except Exception:
            pass                                 # keep library defaults if these knobs aren't exposed
        self._vl.start_ranging()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"vl53l1x-0x{int(address):02x}")
        self._thread.start()

    @property
    def distance(self):
        return self._distance                    # METRES (negative sentinel on a read error -> fail closed)

    def _loop(self):
        while not self._closed:
            try:
                if self._vl.data_ready:
                    cm = self._vl.distance       # centimetres, or None when there is no target in range
                    self._vl.clear_interrupt()
                    if cm is None:
                        self._distance = self.max_distance          # no target -> clear path (like no-echo)
                    else:
                        self._distance = min(self.max_distance, max(0.0, cm / 100.0))
            except Exception:
                self._distance = -1.0            # FAIL CLOSED: an errored read must never read "clear"
            time.sleep(self._period)

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self._vl.stop_ranging()
        except Exception:
            pass
