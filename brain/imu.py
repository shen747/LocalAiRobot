# brain/imu.py -- optional 9-DOF IMU (BNO085) heading/orientation reader. OPT-IN, DISABLED BY DEFAULT.
#
# Selected by ROBOT_IMU_TYPE (empty = disabled -> the robot behaves exactly as if no IMU existed). When set
# to 'bno085', a background daemon thread reads yaw (heading, degrees) + yaw-rate over I2C into a cached
# value; .heading() / .yaw_rate() return the cache WITHOUT blocking (mirrors the distance backends, so it
# could later feed the 20 Hz loop without stalling it). FAIL-SAFE: any read error caches None, and the
# getters return None -- callers MUST treat None as "no IMU / unknown heading".
#
# SCOPE: this module provides the READER + the config seam ONLY. Consuming it (closed-loop "turn 90 deg" /
# heading-hold) is a separate, later project -- nothing in the current control flow imports or constructs an
# IMU, so this file is inert until wired in. make_imu() returns None unless ROBOT_IMU_TYPE selects a device.
#
# adafruit-circuitpython-bno08x + Blinka are imported LAZILY, so importing this module is safe on any machine.

import logging
import math
import os
import threading
import time

from vl53l1x_sensor import make_i2c   # board-neutral I2C (ROBOT_I2C_BUS -> ExtendedI2C, else Blinka default)

log = logging.getLogger("robot.hw")


def make_imu():
    """Return an IMU reader if ROBOT_IMU_TYPE selects a supported device, else None (DISABLED = the default,
    so an omitted/empty key changes nothing). Never raises: a bad type or a construction failure -> None."""
    kind = os.environ.get("ROBOT_IMU_TYPE", "").strip().lower()
    if not kind:
        return None
    try:
        addr = int(os.environ.get("ROBOT_IMU_ADDR", "0x4A") or "0x4A", 0)
    except ValueError:
        addr = 0x4A
    if kind == "bno085":
        try:
            return BNO085IMU(make_i2c(), addr)
        except Exception as e:
            log.warning("IMU (bno085 @0x%02x) init failed (%s) -- continuing without an IMU", addr, e)
            return None
    log.warning("ROBOT_IMU_TYPE=%s not supported (known: bno085) -- IMU disabled", kind)
    return None


class BNO085IMU:
    """BNO08x 9-DOF IMU over I2C. .heading() = yaw in degrees 0..360 (or None); .yaw_rate() = deg/s (or None).
    A daemon thread polls the rotation vector + gyro; the getters return the cache without blocking."""

    def __init__(self, i2c, address=0x4A, period=0.05):
        import adafruit_bno08x
        from adafruit_bno08x.i2c import BNO08X_I2C
        self._period = period
        self._heading = None
        self._yaw_rate = None
        self._closed = False
        self._imu = BNO08X_I2C(i2c, address=address)
        self._imu.enable_feature(adafruit_bno08x.BNO_REPORT_ROTATION_VECTOR)
        self._imu.enable_feature(adafruit_bno08x.BNO_REPORT_GYROSCOPE)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bno085")
        self._thread.start()

    def heading(self):
        return self._heading      # degrees 0..360, or None on no/failed read

    def yaw_rate(self):
        return self._yaw_rate     # degrees/second, or None

    def _loop(self):
        while not self._closed:
            try:
                i, j, k, real = self._imu.quaternion                       # rotation vector
                # yaw (heading about the vertical axis) from the quaternion, normalised to 0..360.
                yaw = math.degrees(math.atan2(2.0 * (real * k + i * j),
                                              1.0 - 2.0 * (j * j + k * k)))
                self._heading = (yaw + 360.0) % 360.0
                self._yaw_rate = math.degrees(self._imu.gyro[2])           # z-axis rate (rad/s -> deg/s)
            except Exception:
                self._heading = None                                      # FAIL-SAFE: unknown, never a stale lie
                self._yaw_rate = None
            time.sleep(self._period)

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)


if __name__ == "__main__":   # self-test: ROBOT_IMU_TYPE=bno085 python3 brain/imu.py
    logging.basicConfig(level=logging.INFO)
    imu = make_imu()
    if imu is None:
        print("No IMU (set ROBOT_IMU_TYPE=bno085 to enable).")
    else:
        try:
            for _ in range(40):
                print(f"heading={imu.heading()}  yaw_rate={imu.yaw_rate()}")
                time.sleep(0.25)
        finally:
            imu.close()
