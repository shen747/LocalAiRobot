# brain/proximity.py -- ACTIVE IR proximity as the Concierge wake trigger (Vishay VCNL4200 over I2C).
#
# Drop-in alternative to brain/pir.py: same public surface (available / arm_cooldown / stats_line / close /
# last_wake_at / the accepted+rejected_* counters), so brain/main.py's two-stage wake, outcome cooldowns and
# latency logging all work unchanged. The only difference is WHAT decides "someone is there".
#
# WHY THIS EXISTS -- a PIR could not do this job on the reference build, and it is worth knowing why before
# you reach for one. A PIR is PASSIVE: it watches for a moving heat difference against the background, through
# a Fresnel lens that splits its view into alternating sensitive/blind zones, and it fires when a body CROSSES
# between zones. Four things stack up against a wall-mounted greeter that people walk straight up to:
#   1. HEAD-ON APPROACH is its worst geometry -- walking along the sensor's axis barely crosses any zones.
#      Measured on the reference build with sensitivity at MAX and delay at MIN: 12 deliberate walk-pasts
#      produced 3 detections, and a repeat produced 1.
#   2. THERMAL CONTRAST -- it sees the DIFFERENCE between body and background. A heated room flattens that.
#   3. CLOTHING -- a winter jumper absorbs most of the body IR that would otherwise reach the sensor.
#   4. BLOCKING TIME -- the HC-SR501 has a hardware-fixed ~2.5s dead period after every trigger, on top of the
#      delay pot, so rapid repeated approaches physically cannot all register.
# None of those are tunable. An ACTIVE sensor sidesteps all four: the VCNL4200 emits its own IR and measures
# what bounces back, so thermal contrast, clothing emission and direction of travel stop mattering. It answers
# a simpler question -- "is something within X cm of me?" -- and a visitor walking straight at it is the BEST
# case rather than the worst.
#
# WHAT IT COSTS YOU IN EXCHANGE (be honest about this before wiring it in):
#   * RANGE. Adafruit rate the module 0-1.5m but advise 50-100cm in practice, and precision falls off with
#     distance. That is FINE for a greeter people walk up to, and useless for spotting someone across a room.
#     It also happens to match the camera's useful face-recognition range, which neither the PIR nor a 6m
#     mmWave radar did -- the range mismatch is what made both of those announce people the camera couldn't see.
#   * REFLECTIVITY. It measures reflected IR, so a black wool coat returns far less than a white shirt. Set
#     the threshold against the worst case you care about, not against your own T-shirt.
#   * AMBIENT IR. Direct sunlight is a big IR source. The chip has background cancellation and sunlight
#     immunity, but pointing it at a sunlit doorway is still asking for trouble.
#   * It reports PRESENCE, not movement -- so, like any presence sensor, it produces ONE rising edge per
#     arrival and holds while somebody stands there. That is what you want for a greeter (nobody gets
#     re-greeted mid-conversation), but it means a person who never leaves generates no further wakes.
#
# WHY POLLING AND NOT THE INT PIN. The VCNL4200 has a programmable interrupt output that could drive a GPIO
# exactly like a PIR's OUT. We don't use it, for three reasons: the thresholds would live in chip registers
# instead of config/robot.env; it is active-LOW open-drain (the inverse of what gpiozero.MotionSensor
# expects); and, most usefully, polling lets us LOG THE ACTUAL DISTANCE on every wake. A whole week of
# debugging the previous two sensors came down to not knowing how far away the thing they fired at was.
#
# I2C WIRING (the Adafruit STEMMA QT breakout; Blinka is already a dependency via adafruit-circuitpython-servokit):
#     VIN -> 3.3V (pin 1)      SDA -> GPIO2 (pin 3)
#     GND -> GND  (pin 6)      SCL -> GPIO3 (pin 5)
#   The INT pin is left unconnected. ROBOT_PIR_PIN is ignored entirely by this backend.
#
# Enable with ROBOT_WAKE_SENSOR=proximity (see scripts/patches/common/6_motion_vcnl4200.sh). Everything
# degrades to available=False if the library or the chip is missing, exactly like the PIR path -- a missing
# sensor means "wake word only", never a crash.

import logging
import os
import threading
import time

log = logging.getLogger("robot.hw")


def _envf(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _envi(key, default):
    try:
        return int(float(os.environ.get(key, "") or default))
    except (TypeError, ValueError):
        return int(default)


class ProximityWake:
    """Active IR proximity wake. Mirrors pir.MotionWake so main.py can hold either without knowing which.

    A background thread reads the raw proximity count every ROBOT_PROXIMITY_POLL_S. The count RISES as an
    object gets CLOSER (it is reflected light, not distance), so "someone arrived" is a rising crossing of
    ROBOT_PROXIMITY_NEAR. Leaving is a fall below ROBOT_PROXIMITY_FAR, which is deliberately LOWER: that gap
    is hysteresis, and without it a visitor hovering exactly at the threshold would machine-gun wakes."""

    def __init__(self, enabled, on_motion):
        self._on_motion = on_motion
        self.available = False
        self._sensor = None
        self._stop = threading.Event()
        self._thread = None

        self.near = _envi("ROBOT_PROXIMITY_NEAR", 300)
        self.far = _envi("ROBOT_PROXIMITY_FAR", 200)
        self.poll_s = max(0.02, _envf("ROBOT_PROXIMITY_POLL_S", 0.1))
        self._verbose = os.environ.get("ROBOT_SENSOR_VERBOSE", "0") == "1"
        # Same throttle surface as the PIR so main.py's outcome cooldowns are untouched.
        self._cooldown_s = max(0.0, _envf("ROBOT_PIR_COOLDOWN_S", 2))
        self._lock = threading.Lock()
        self._last_accepted = 0.0
        self._suppress_until = 0.0
        self._suppress_reason = ""
        self.last_wake_at = 0.0
        self.accepted = 0
        self.rejected_spike = 0        # unused here; kept so stats_line/main.py stay uniform
        self.rejected_cooldown = 0
        self.rejected_outcome = 0
        self.last_reading = 0          # newest raw count, for diagnostics
        self.max_reading = 0           # loudest thing seen this run -- tells you where to put NEAR

        if not enabled:
            return
        if self.far >= self.near:
            log.warning("proximity: ROBOT_PROXIMITY_FAR (%d) must be BELOW _NEAR (%d) -- that gap is the "
                        "hysteresis. Using %d.", self.far, self.near, max(1, self.near // 2))
            self.far = max(1, self.near // 2)
        try:
            import board                       # Blinka -- already pulled in by adafruit-circuitpython-servokit
            import adafruit_vcnl4200
            self._sensor = adafruit_vcnl4200.Adafruit_VCNL4200(board.I2C())
            self._sensor.proximity_shutdown = False      # the chip powers up with the PS disabled
            self.available = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="proximity-wake")
            self._thread.start()
            log.info("proximity wake: VCNL4200 armed over I2C (Concierge Mode) -- near>=%d, far<=%d "
                     "(hysteresis), polled every %.2fs. Raw counts RISE as someone gets closer; "
                     "run ./scripts/proximity_probe.sh to pick the numbers for YOUR doorway.",
                     self.near, self.far, self.poll_s)
        except Exception as e:
            # No Blinka / no driver / no chip on the bus / I2C not enabled -> wake word only, never a crash.
            log.warning("proximity wake unavailable (%s) -- Concierge will trigger on the wake word only. "
                        "Check: I2C enabled (raspi-config), wiring SDA->pin3 SCL->pin5, `i2cdetect -y 1` "
                        "shows 0x51, and `pip install adafruit-circuitpython-vcnl4200` in brain/.venv", e)
            self.available = False

    # -- same throttle surface as pir.MotionWake ---------------------------------------------------------
    def arm_cooldown(self, seconds, reason=""):
        """Suppress wakes for `seconds`, armed by main.py AFTER a look, from what the camera actually found.
        Identical contract to pir.MotionWake.arm_cooldown -- see brain/pir.py for why the throttle lives on
        the outcome rather than the trigger. <= 0 clears any pending suppression."""
        try:
            secs = max(0.0, float(seconds or 0.0))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._suppress_until = (time.monotonic() + secs) if secs > 0 else 0.0
            self._suppress_reason = reason or ""

    def _read(self):
        """Raw proximity count, or None. Higher = closer. Never raises."""
        try:
            return int(self._sensor.proximity)
        except Exception as e:
            log.debug("proximity read failed: %s", e)
            return None

    def _loop(self):
        near_now = False                 # True while something is inside the NEAR threshold
        while not self._stop.is_set():
            value = self._read()
            if value is not None:
                self.last_reading = value
                self.max_reading = max(self.max_reading, value)
                if not near_now and value >= self.near:
                    near_now = True
                    self._fire(value)
                elif near_now and value <= self.far:
                    near_now = False     # they left -- re-arm for the next arrival
                    if self._verbose:
                        log.info("proximity: cleared (%d <= far %d) -- re-armed", value, self.far)
            self._stop.wait(self.poll_s)

    def _fire(self, value):
        """A rising crossing of NEAR. Gated exactly like the PIR's accepted edge."""
        now = time.monotonic()
        with self._lock:
            if self._suppress_until and now < self._suppress_until:
                self.rejected_outcome += 1
                if self._verbose:
                    log.info("proximity: arrival at %d ignored -- %s cooldown, %.1fs left",
                             value, self._suppress_reason or "outcome", self._suppress_until - now)
                return
            if self._last_accepted and (now - self._last_accepted) < self._cooldown_s:
                self.rejected_cooldown += 1
                if self._verbose:
                    log.info("proximity: arrival at %d ignored -- inside the %.1fs debounce",
                             value, self._cooldown_s)
                return
            self._last_accepted = now
            self.last_wake_at = now
            self.accepted += 1
        # The distance number is the whole point of polling rather than using the INT pin: when a wake turns
        # out to be wrong, the log says how close the thing actually was.
        log.info("proximity: someone arrived (raw %d >= near %d) -> wake", value, self.near)
        try:
            self._on_motion()
        except Exception as e:
            log.debug("proximity callback error: %s", e)

    def stats_line(self):
        """One-line run summary, logged on shutdown. max_reading is the useful one: if it never approached
        ROBOT_PROXIMITY_NEAR, the threshold is too high (or people never get close enough) -- and if it sits
        near NEAR with nobody there, something reflective is parked in front of the sensor."""
        if not self.available:
            return ""
        return (f"proximity VCNL4200: woke {self.accepted}x, suppressed {self.rejected_outcome} "
                f"(outcome cooldown) + {self.rejected_cooldown} (debounce) | raw last {self.last_reading}, "
                f"peak {self.max_reading}, near threshold {self.near}")

    def close(self):
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self.available = False
