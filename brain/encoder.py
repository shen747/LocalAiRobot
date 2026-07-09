# brain/encoder.py -- optional AS5600 12-bit magnetic rotary encoder reader. OPT-IN, DISABLED BY DEFAULT.
#
# Selected by ROBOT_ENCODER_TYPE (empty = disabled -> nothing changes). When set to 'as5600', a background
# daemon thread reads the absolute angle (degrees) over I2C into a cached value; .angle() returns the cache
# without blocking. FAIL-SAFE: any read error caches None, and .angle() returns None -- callers MUST treat
# None as "no encoder / unknown".
#
# SCOPE: reader + config seam ONLY. Consuming it (steering-angle feedback / wheel odometry) is a separate,
# later project -- nothing in the current control flow imports or constructs an encoder, so this file is
# inert until wired in. make_encoder() returns None unless ROBOT_ENCODER_TYPE selects a device.
#
# NOTE: the AS5600 default I2C address is 0x36 -- the SAME as the UPS-HAT fuel gauge on the Pi reference
# build. If you fit both, put the encoder on a separate bus or use its DIR-pin address option (there is no
# single-bus fix -- the AS5600 address is not runtime-remappable like the VL53L1X's).
#
# adafruit-circuitpython-as5600 + Blinka are imported LAZILY, so importing this module is safe on any machine.

import logging
import os
import threading
import time

from vl53l1x_sensor import make_i2c   # board-neutral I2C (ROBOT_I2C_BUS -> ExtendedI2C, else Blinka default)

log = logging.getLogger("robot.hw")


def make_encoder():
    """Return an encoder reader if ROBOT_ENCODER_TYPE selects a supported device, else None (DISABLED = the
    default). Never raises: a bad type or a construction failure -> None."""
    kind = os.environ.get("ROBOT_ENCODER_TYPE", "").strip().lower()
    if not kind:
        return None
    try:
        addr = int(os.environ.get("ROBOT_ENCODER_ADDR", "0x36") or "0x36", 0)
    except ValueError:
        addr = 0x36
    if kind == "as5600":
        try:
            return AS5600Encoder(make_i2c(), addr)
        except Exception as e:
            log.warning("encoder (as5600 @0x%02x) init failed (%s) -- continuing without an encoder", addr, e)
            return None
    log.warning("ROBOT_ENCODER_TYPE=%s not supported (known: as5600) -- encoder disabled", kind)
    return None


class AS5600Encoder:
    """AS5600 magnetic rotary encoder over I2C. .angle() = absolute angle in degrees 0..360 (or None). A
    daemon thread polls the raw 12-bit angle; .angle() returns the cache without blocking."""

    def __init__(self, i2c, address=0x36, period=0.02):
        import adafruit_as5600
        self._period = period
        self._angle = None
        self._closed = False
        # adafruit-circuitpython-as5600's constructor takes (i2c, address); .angle is the SCALED 12-bit
        # reading (0..4095; == raw under default ZPOS/MPOS/MANG). .raw_angle is the unscaled count.
        self._enc = adafruit_as5600.AS5600(i2c, address=address)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="as5600")
        self._thread.start()

    def angle(self):
        return self._angle        # degrees 0..360, or None on a failed read

    def _loop(self):
        while not self._closed:
            try:
                raw = self._enc.angle            # 0..4095 (12-bit)
                self._angle = (float(raw) / 4096.0) * 360.0
            except Exception:
                self._angle = None               # FAIL-SAFE: unknown, never a stale lie
            time.sleep(self._period)

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)


if __name__ == "__main__":   # self-test: ROBOT_ENCODER_TYPE=as5600 python3 brain/encoder.py
    logging.basicConfig(level=logging.INFO)
    enc = make_encoder()
    if enc is None:
        print("No encoder (set ROBOT_ENCODER_TYPE=as5600 to enable).")
    else:
        try:
            for _ in range(40):
                a = enc.angle()
                print(f"angle={a:.1f} deg" if a is not None else "angle=None")
                time.sleep(0.25)
        finally:
            enc.close()
