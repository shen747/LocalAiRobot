# brain/hcsr04_jetson.py -- HC-SR04(P) ultrasonic driver for NVIDIA Jetson (Jetson.GPIO).
#
# Drop-in for the subset of gpiozero.DistanceSensor that driver.py uses: a `.distance` property in METRES
# and a `.close()`. Like gpiozero (and for the SAME reason -- never block the 20 Hz safety loop) a
# background daemon thread does the pinging while `.distance` just returns the most recent reading. A
# missing echo saturates to max_distance (reads "clear"), matching gpiozero so driver.py's fail-closed +
# ROBOT_OBSTACLE_DEBOUNCE logic is unchanged.
#
# Jetson.GPIO is imported LAZILY (inside __init__) so this module imports fine on a Pi / laptop; only the
# jetson board's make_distance_sensor(), on an actual Jetson, ever constructs one.

import logging
import threading
import time

log = logging.getLogger("robot.hw")

_SPEED_OF_SOUND = 343.0   # m/s (dry air, ~20C)
# While waiting for the echo to START (the no-echo case busy-waits the whole timeout), sleep-poll instead of
# spinning: time.sleep RELEASES the GIL, so a dead/open-space sensor can't starve the wake-word predict
# thread (the Pi's "went deaf" failure, in the shared CPython interpreter). ~200us keeps the rising-edge
# detection tight; detecting the edge slightly late only shifts `start` later -> the pulse reads SHORTER ->
# a nearer (more conservative/safer) distance, never farther. The pulse-timing loop stays a tight busy-wait.
_RISING_POLL_S = 0.0002


class JetsonDistanceSensor:
    """Single HC-SR04(P) beam on Jetson.GPIO. .distance is in METRES (driver.py multiplies by 100)."""

    def __init__(self, trigger, echo, max_distance=1.0, period=0.06):
        import Jetson.GPIO as GPIO
        self._GPIO = GPIO
        self._trig, self._echo = int(trigger), int(echo)
        self.max_distance = float(max_distance)
        self._period = period
        self._distance = self.max_distance      # start "clear" so a not-yet-pinged sensor doesn't block
        self._closed = False
        # BOARD (physical header) numbering. setmode is process-global and one-shot in Jetson.GPIO: set it
        # only if unset (all our sensors share BOARD). If something already set BCM, that's a config error.
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self._trig, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self._echo, GPIO.IN)
        time.sleep(0.05)
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"hcsr04-{self._trig}")
        self._thread.start()

    @property
    def distance(self):
        return self._distance                   # METRES

    def _measure(self):
        """One ping. Returns metres, or None on no-echo/timeout. Busy-waits ONLY within the echo window
        (bounded by `timeout`), so a dead sensor can't hang the thread -- it returns None each period."""
        GPIO = self._GPIO
        GPIO.output(self._trig, GPIO.HIGH)
        time.sleep(1e-5)                         # 10us trigger pulse
        GPIO.output(self._trig, GPIO.LOW)
        # Bound the wait by the round-trip time to max range + margin (a no-echo ping returns after this).
        timeout = (2.0 * self.max_distance / _SPEED_OF_SOUND) + 0.02
        t0 = time.perf_counter()
        while GPIO.input(self._echo) == 0:       # wait for the echo rising edge
            if time.perf_counter() - t0 > timeout:
                return None
            time.sleep(_RISING_POLL_S)           # yield the GIL (this is the no-echo busy-wait that starves it)
        start = time.perf_counter()
        while GPIO.input(self._echo) == 1:       # ...then the falling edge (tight loop = precise pulse timing)
            if time.perf_counter() - start > timeout:
                return None
        return min(self.max_distance, (time.perf_counter() - start) * _SPEED_OF_SOUND / 2.0)

    def _loop(self):
        while not self._closed:
            try:
                d = self._measure()
            except Exception:
                d = None                         # a transient GPIO error reads as "clear", never crashes
            self._distance = d if d is not None else self.max_distance
            time.sleep(self._period)

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self._GPIO.cleanup((self._trig, self._echo))   # free just our two channels (leave others)
        except Exception:
            pass


class JetsonDigitalOutput:
    """A DigitalOutputDevice-like GPIO output on Jetson.GPIO (.on()/.off()/.close()) -- used to drive VL53L1X
    XSHUT lines (ROBOT_SENSOR_TYPE=vl53l1x) board-neutrally, so vl53l1x_sensor.py imports no GPIO library.
    BOARD (physical header) numbering; lazy Jetson.GPIO import; mirrors JetsonDistanceSensor's setmode/cleanup
    discipline (setmode only if unset; cleanup just our own pin)."""

    def __init__(self, pin, initial=False):
        import Jetson.GPIO as GPIO
        self._GPIO = GPIO
        self._pin = int(pin)
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self._pin, GPIO.OUT, initial=GPIO.HIGH if initial else GPIO.LOW)

    def on(self):
        self._GPIO.output(self._pin, self._GPIO.HIGH)

    def off(self):
        self._GPIO.output(self._pin, self._GPIO.LOW)

    def close(self):
        try:
            self._GPIO.cleanup(self._pin)
        except Exception:
            pass
