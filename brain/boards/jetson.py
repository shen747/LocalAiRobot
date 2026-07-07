# brain/boards/jetson.py -- NVIDIA Jetson board backend (Orin Nano target).
#
# Supplies the three board-specific factories the HAL needs, using the Jetson's stacks:
#   * PWM kit        -- adafruit_servokit (Blinka supports the Jetson); the I2C bus is selectable via
#                       ROBOT_I2C_BUS (the 40-pin header is usually bus 7, not the Pi's bus 1).
#   * distance sensor-- Jetson.GPIO (brain/hcsr04_jetson.py); gpiozero has no Jetson pin factory.
#   * camera         -- OpenCV/GStreamer (brain/gst_camera.py); Picamera2 is Raspberry-Pi-only.
#
# All hardware imports are LAZY (inside the factory methods) so this module imports fine on a Pi / laptop
# too -- importing `boards` must never require the Jetson libraries. Only when this board is SELECTED and a
# factory is CALLED (live mode on an actual Jetson) are Jetson.GPIO / cv2 / adafruit imports touched.
#
# The board interface this implements is documented in brain/hal.py. Pin numbers here are BOARD (physical
# 40-pin header) numbering -- they are PLACEHOLDERS: verify against the Orin Nano pinout and free the pins
# in `jetson-io` before wiring.

import os

from hal import register


class JetsonBoard:
    name = "jetson"
    pin_scheme = "BOARD"      # Jetson.GPIO BOARD mode = physical header pin numbers (NOT Pi BCM)

    def matches(self):
        try:
            m = open("/proc/device-tree/model").read().lower()
        except OSError:
            return False
        return any(k in m for k in ("jetson", "orin", "tegra"))

    def default_pins(self):
        # BOARD/physical-header numbering. PLACEHOLDERS -- verify against the Orin Nano 40-pin pinout and
        # free them in jetson-io. config/robot.env (Jetson profile) overrides these; this is the fallback.
        return {
            "ROBOT_TRIG_PIN": 16, "ROBOT_ECHO_PIN": 18,
            "ROBOT_LEFT_TRIG_PIN": 29, "ROBOT_LEFT_ECHO_PIN": 31,
            "ROBOT_RIGHT_TRIG_PIN": 32, "ROBOT_RIGHT_ECHO_PIN": 33,
        }

    def launch_env(self):
        return {}             # Jetson.GPIO needs no gpiozero pin factory

    def make_pwm_kit(self, channels):
        from adafruit_servokit import ServoKit
        bus = os.environ.get("ROBOT_I2C_BUS", "").strip()
        if bus:
            # adafruit-extended-bus lets us bind ServoKit to an arbitrary numbered I2C bus (the Jetson
            # 40-pin header is usually bus 7; Blinka's board default may point elsewhere).
            try:
                from adafruit_extended_bus import ExtendedI2C
            except ImportError as e:
                raise RuntimeError("ROBOT_I2C_BUS=%s needs the 'adafruit-extended-bus' package "
                                   "(pip install adafruit-extended-bus)" % bus) from e
            return ServoKit(channels=channels, i2c=ExtendedI2C(int(bus)))
        return ServoKit(channels=channels)   # Blinka board default (verify it is the header's I2C bus)

    def make_distance_sensor(self, trigger, echo, max_distance):
        from hcsr04_jetson import JetsonDistanceSensor
        return JetsonDistanceSensor(trigger=trigger, echo=echo, max_distance=max_distance)

    def make_camera(self, size):
        from gst_camera import GstCamera
        backend = os.environ.get("ROBOT_CAMERA_BACKEND", "").strip().lower() or "gstreamer"
        return GstCamera(size, backend)


register(JetsonBoard())
