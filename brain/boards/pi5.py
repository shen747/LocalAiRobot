# brain/boards/pi5.py -- Raspberry Pi 5 board backend (the reference build).
#
# Wraps the hardware libraries the project has always used on the Pi: adafruit_servokit (PCA9685 PWM over
# I2C) for the ESC + steering servo, gpiozero.DistanceSensor (lgpio pin factory) for the 3 HC-SR04P beams,
# and Picamera2 for the camera. This is an extract-and-move of the code that used to live inline in
# driver.py / camera.py -- behaviour on the Pi is unchanged; only its location moved behind the HAL.
#
# All hardware imports are LAZY (inside the factory methods) so this module imports fine on any machine --
# importing `boards` on a laptop/Jetson must never require the Pi libraries.

import os

from hal import register


class Pi5Board:
    name = "pi5"
    pin_scheme = "BCM"      # gpiozero pin numbers are Broadcom (BCM) GPIO numbers on the Pi

    def matches(self):
        try:
            return "raspberry pi" in open("/proc/device-tree/model").read().lower()
        except OSError:
            return False

    def default_pins(self):
        # BCM GPIO numbers -- the same values config/robot.env documents. config/robot.env still wins
        # (main.py applies these with setdefault), so this is just the in-code fallback.
        return {
            "ROBOT_TRIG_PIN": 23, "ROBOT_ECHO_PIN": 24,
            "ROBOT_LEFT_TRIG_PIN": 5, "ROBOT_LEFT_ECHO_PIN": 6,
            "ROBOT_RIGHT_TRIG_PIN": 20, "ROBOT_RIGHT_ECHO_PIN": 21,
        }

    def launch_env(self):
        # The Pi 5 uses lgpio (the modern /dev/gpiochip driver); pigpio can't reach the RP1 I/O controller.
        # Pin the deterministic factory rather than risk gpiozero auto-picking a wrong one. (Was exported
        # by run_me.sh; moved here so each board carries its own backend.)
        return {"GPIOZERO_PIN_FACTORY": "lgpio"}

    def make_pwm_kit(self, channels):
        from adafruit_servokit import ServoKit
        return ServoKit(channels=channels)

    def make_distance_sensor(self, trigger, echo, max_distance):
        from gpiozero import DistanceSensor
        # partial=True is CRITICAL: without it gpiozero's .distance/.value BLOCKS until the sample queue
        # fills, and a dead/unwired sensor (no echo) never fills it -- which would HANG the 20 Hz safety
        # loop the first time the FORWARD gate reads it. partial=True returns whatever's available
        # immediately, so a bad sensor can never stall the loop. queue_len=3 keeps older gpiozero snappy.
        return DistanceSensor(echo=echo, trigger=trigger, max_distance=max_distance,
                              queue_len=3, partial=True)

    def make_digital_output(self, pin):
        # Drives a VL53L1X XSHUT line (ROBOT_SENSOR_TYPE=vl53l1x). gpiozero.DigitalOutputDevice already
        # exposes .on()/.off()/.close(); starts LOW (sensor held in reset until assign_addresses raises it).
        from gpiozero import DigitalOutputDevice
        return DigitalOutputDevice(int(pin))

    def make_camera(self, size):
        # Default = the CSI camera via Picamera2 (the reference build). Set ROBOT_CAMERA_BACKEND=v4l2 to use a
        # USB webcam instead -- GstCamera's v4l2 branch is plain OpenCV (cv2.VideoCapture), board-neutral, and
        # reads the webcam index from ROBOT_CAMERA_SOURCE. Needs opencv (cv2) in the venv; it imports lazily
        # and degrades to available=False if missing, so CSI builds are unaffected. Same surface either way.
        backend = os.environ.get("ROBOT_CAMERA_BACKEND", "").strip().lower()
        if backend == "v4l2":
            from gst_camera import GstCamera
            return GstCamera(size, "v4l2")
        from picamera2_backend import PiCamera2Camera
        return PiCamera2Camera(size)


register(Pi5Board())
