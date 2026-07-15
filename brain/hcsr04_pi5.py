# brain/hcsr04_pi5.py -- raw-ping HC-SR04(P) driver for the Raspberry Pi 5.
#
# WHY THIS EXISTS: gpiozero.DistanceSensor on the Pi 5 is timed in SOFTWARE (the RP1 I/O controller gives no
# hardware echo timing -- that's the PWMSoftwareFallback warning). Under CPU load its queued sampler jitters
# and can WEDGE: one beam (typically the front) collapses to a stuck 0 cm / no-echo while the others read
# fine, which fail-closes the FORWARD gate and blocks all driving. This is a drop-in replacement that does
# the SAME raw ping scripts/ultrasonic_sensor_probe.sh does -- and the probe reads reliably on the exact pin
# where DistanceSensor doesn't. It drives a tight TRIG pulse and times the ECHO edges directly, via
# gpiozero's Digital I/O devices, so the Pi 5 lgpio pin factory still owns /dev/gpiochip selection (no raw
# chip-number guesswork).
#
# CROSS-TALK: three HC-SR04 firing at once let one beam hear another's burst -> a false ~0 cm. All instances
# share ONE lock so only a single beam pings at a time -- the same reason the probe pings the three in turn.
#
# INTERFACE: matches the subset of gpiozero.DistanceSensor that driver.py uses -- a `.distance` property in
# METRES and `.close()`. A background daemon thread pings so `.distance` never blocks the 20 Hz safety loop;
# a missing echo saturates to max_distance (reads "clear"), matching gpiozero + the Jetson backend so
# driver.py's fail-closed + ROBOT_OBSTACLE_DEBOUNCE logic is unchanged. gpiozero is imported LAZILY (inside
# __init__) so this module imports fine on a laptop/Jetson; only the pi5 board's make_distance_sensor()
# constructs one.

import logging
import threading
import time

log = logging.getLogger("robot.hw")

_SPEED_OF_SOUND = 343.0     # m/s (dry air, ~20C)
# While waiting for the echo to START (a no-echo ping busy-waits the whole timeout), sleep-poll instead of
# spinning: time.sleep RELEASES the GIL, so a dead/open-space beam can't starve the wake-word predict thread
# (the Pi's "went deaf" failure, in the shared CPython interpreter). ~200us keeps rising-edge detection
# tight; detecting the edge slightly late only shifts `start` later -> the pulse reads SHORTER -> a nearer
# (more conservative/safer) distance, never farther. The pulse-timing loop itself stays a tight busy-wait.
_RISING_POLL_S = 0.0002
# Serialise pings across ALL beams (anti-cross-talk). Module-level so every Pi5DistanceSensor shares it,
# and it survives the per-drive sensor rebuild (resume_sensing) since the module stays imported.
_PING_LOCK = threading.Lock()


class Pi5DistanceSensor:
    """Single HC-SR04(P) beam on the Pi 5 via gpiozero Digital I/O + raw edge timing. .distance is in METRES
    (driver.py multiplies by 100). A daemon thread pings every `period`; .distance returns the last reading."""

    def __init__(self, trigger, echo, max_distance=1.0, period=0.06):
        from gpiozero import DigitalInputDevice, DigitalOutputDevice
        self._trig = DigitalOutputDevice(int(trigger))   # starts LOW
        self._echo = DigitalInputDevice(int(echo))
        self.max_distance = float(max_distance)
        self._period = period
        self._distance = self.max_distance   # start "clear" so a not-yet-pinged beam doesn't block the gate
        self._closed = False
        time.sleep(0.05)                     # let the ECHO line settle before the first ping
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"hcsr04pi5-{int(trigger)}")
        self._thread.start()

    @property
    def distance(self):
        return self._distance                # METRES

    def _measure(self):
        """One ping, under the shared lock. Returns metres, or None on no-echo/timeout. Busy-waits ONLY
        within the echo window (bounded by `timeout`), so a dead beam can't hang the thread -- it returns
        None each period. The lock makes the three beams fire one-at-a-time (no cross-talk)."""
        # round-trip time to max range + margin bounds every wait (a no-echo ping returns after this)
        timeout = (2.0 * self.max_distance / _SPEED_OF_SOUND) + 0.02
        with _PING_LOCK:
            self._trig.off()
            self._trig.on()
            time.sleep(1e-5)                 # 10us trigger pulse
            self._trig.off()
            t0 = time.perf_counter()
            while self._echo.value == 0:     # wait for the echo rising edge
                if time.perf_counter() - t0 > timeout:
                    return None
                time.sleep(_RISING_POLL_S)   # yield the GIL during the no-echo busy-wait
            start = time.perf_counter()
            while self._echo.value == 1:     # ...then the falling edge (tight loop = precise pulse timing)
                if time.perf_counter() - start > timeout:
                    return None
            elapsed = time.perf_counter() - start
        return min(self.max_distance, elapsed * _SPEED_OF_SOUND / 2.0)

    def _loop(self):
        while not self._closed:
            try:
                d = self._measure()
            except Exception:
                d = None                     # a transient GPIO error reads as "clear", never crashes the loop
            self._distance = d if d is not None else self.max_distance
            time.sleep(self._period)

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)   # let the current ping finish (releases _PING_LOCK) before teardown
        for dev in (self._trig, self._echo):
            try:
                dev.close()
            except Exception:
                pass
