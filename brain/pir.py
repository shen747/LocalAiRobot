# brain/pir.py -- PIR motion sensor as an extra WAKE trigger for Concierge Mode.
#
# A passive-infrared sensor (e.g. Adafruit PIR, 3.3V digital out -> Pi-safe) fires when a person walks up to
# the robot. We treat that exactly like the wake word: on motion we set a shared Event AND call
# wake.cancel_listen(), which breaks the in-progress openWakeWord listen out with 'CANCELLED' -- so
# wait_for_trigger() returns immediately and the main loop runs the concierge greeting pipeline. The wake
# word keeps working in parallel; motion is purely additional.
#
# gpiozero.MotionSensor on the Pi (same lgpio pin factory as the ultrasonics). Lazy import + graceful
# degrade: no ROBOT_PIR_PIN, or no gpiozero (laptop/Jetson) -> available=False, a harmless no-op.

import logging
import os

log = logging.getLogger("robot.hw")


class MotionWake:
    """Watch a PIR sensor and call `on_motion` on each rising edge. Construct with the BCM pin + callback;
    .close() releases the GPIO. available=False (no-op) when no pin or gpiozero is present."""

    def __init__(self, pin, on_motion):
        self._sensor = None
        self._on_motion = on_motion
        self.available = False
        pin = str(pin).strip() if pin is not None else ""
        self._pin = pin
        # --verbose (ROBOT_SENSOR_VERBOSE=1): log every PIR trigger. In Concierge Mode the ultrasonic read-out
        # is suppressed (driver.py), so the PIR is what you watch instead -- like scripts/pir_sensor_probe.sh.
        self._verbose = os.environ.get("ROBOT_SENSOR_VERBOSE", "0") == "1"
        if not pin:
            return
        try:
            from gpiozero import MotionSensor
            self._sensor = MotionSensor(int(pin))
            self._sensor.when_motion = self._fire   # gpiozero fires this on its own thread
            self.available = True
            log.info("PIR motion wake: armed on BCM GPIO %s (Concierge Mode)", pin)
        except Exception as e:
            log.warning("PIR motion sensor init failed on pin %s (%s) -- motion wake disabled "
                        "(the wake word still works)", pin, e)

    def _fire(self):
        if self._verbose:
            log.info("PIR: motion detected on GPIO %s -> wake", self._pin)
        try:
            self._on_motion()
        except Exception as e:
            log.debug("PIR motion callback error: %s", e)

    def close(self):
        try:
            if self._sensor is not None:
                self._sensor.close()
        except Exception:
            pass
