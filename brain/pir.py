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
#
# TRIGGER HYGIENE (why this file has more than a callback in it). A PIR is an ANALOG sensor with a digital
# output, and in a robot it sits inches from a warm Pi 5, its fan exhaust and a noisy 5V rail -- all classic
# false-trigger sources. A field run showed the cost of trusting it blindly: ~508 "motion" wakes an HOUR, all
# night, every hour, with nobody in the house. Each one woke the greeting pipeline for a burst of camera
# stills, so the robot spent 24/7 photographing a dark hallway. Three cheap defences live here:
#   1. HOLD    -- re-read the pin a moment after the edge; a spike that has already fallen was electrical
#                 noise, not a person (a real visitor is still there 250ms later).
#   2. DEBOUNCE-- ignore edges for a couple of seconds after an accepted one, so ONE sensor pulse train is
#                 ONE wake. This is deliberately SHORT (see below) -- it is not the rate limiter.
#   3. CHATTER -- count accepted triggers per window and WARN, loudly and once, when the rate can only mean a
#                 misbehaving sensor. Silent degradation is what let the storm above run for a whole night.
# All three are tunable (and disableable: set the seconds to 0) via config/robot.env.
#
# WHY THE DEBOUNCE IS SHORT NOW (and what replaced the old 15s cooldown). The rate limit used to live HERE,
# on the EDGE: 15 seconds of enforced blindness after every accepted trigger, applied before anything had
# looked at anything. On a sensor that offers an edge every ~7s that is a catastrophe -- a 19-hour field log
# showed 205 accepted wakes an hour, 24/7, which means 205 x 15s = 85% of every hour the robot was
# STRUCTURALLY DEAF to a new arrival. A real visitor walking up waited a random 0-15s before the camera even
# opened, by which time they had turned away, the recognition window saw nothing, and Jarvis stayed silent.
# That was the "the PIR doesn't wake it 90% of the time" bug: not too FEW triggers, too much blind throttle.
#
# The fix is to throttle on the OUTCOME instead of the edge. main.py takes a cheap one-frame look ("is anyone
# actually there?") and then calls arm_cooldown() with a duration that MATCHES WHAT IT FOUND:
#   nobody there  -> a short silent cooldown  (ROBOT_CONCIERGE_SILENT_COOLDOWN_S) -- the real idle limiter,
#                    now applied WITH evidence instead of instead of it.
#   greeted them  -> a long one              (ROBOT_CONCIERGE_GREET_COOLDOWN_S)  -- don't re-greet a visitor
#                    who is still standing there.
# So this file keeps a debounce only long enough that one pulse train can't fire twice; the interesting
# decision belongs to the caller, which is the only thing that knows what the camera saw.

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


class MotionWake:
    """Watch a PIR sensor and call `on_motion` on each rising edge that survives the hold + cooldown filters
    (see the module note). Construct with the BCM pin + callback; .close() releases the GPIO.
    available=False (no-op) when no pin or gpiozero is present."""

    def __init__(self, pin, on_motion):
        self._sensor = None
        self._on_motion = on_motion
        self.available = False
        pin = str(pin).strip() if pin is not None else ""
        self._pin = pin
        # --verbose (ROBOT_SENSOR_VERBOSE=1): log every PIR trigger. In Concierge Mode the ultrasonic read-out
        # is suppressed (driver.py), so the PIR is what you watch instead -- like scripts/pir_sensor_probe.sh.
        self._verbose = os.environ.get("ROBOT_SENSOR_VERBOSE", "0") == "1"
        self._hold_s = max(0.0, _envf("ROBOT_PIR_HOLD_S", 0.25))
        self._cooldown_s = max(0.0, _envf("ROBOT_PIR_COOLDOWN_S", 2))
        self._chatter_n = int(_envf("ROBOT_PIR_CHATTER_N", 20))
        self._chatter_window_s = max(0.0, _envf("ROBOT_PIR_CHATTER_WINDOW_S", 600))
        self._lock = threading.Lock()
        self._last_accepted = 0.0        # monotonic; 0 = none yet
        # Outcome cooldown: an absolute monotonic deadline armed by the CALLER via arm_cooldown() once it
        # knows what the camera saw. Separate from _last_accepted so the two throttles can't clobber each
        # other -- the debounce paces the sensor, this paces the ROBOT's reaction to it.
        self._suppress_until = 0.0
        self._suppress_reason = ""
        # Monotonic timestamp of the last ACCEPTED edge -- the moment the visitor actually tripped the sensor.
        # main.py subtracts it from "we decided" to log a real wake latency, so "it feels slow" becomes a
        # number instead of an impression. Stamped BEFORE the callback so it covers the whole wake path.
        self.last_wake_at = 0.0
        self._window_start = 0.0
        self._window_count = 0
        self._chatter_warned = False
        # Counters for the shutdown summary -- so a run's log ends with how trustworthy the sensor was.
        self.accepted = 0
        self.rejected_spike = 0
        self.rejected_cooldown = 0
        self.rejected_outcome = 0
        if not pin:
            return
        try:
            from gpiozero import MotionSensor
            self._sensor = MotionSensor(int(pin))
            self._sensor.when_motion = self._fire   # gpiozero fires this on its own thread
            self.available = True
            log.info("PIR motion wake: armed on BCM GPIO %s (Concierge Mode) -- hold %.2fs, debounce %.1fs "
                     "(outcome cooldowns are set by the concierge look, not here)",
                     pin, self._hold_s, self._cooldown_s)
        except Exception as e:
            log.warning("PIR motion sensor init failed on pin %s (%s) -- motion wake disabled "
                        "(the wake word still works)", pin, e)

    # -- trigger filters ---------------------------------------------------------------------------------
    def _is_spike(self):
        """True when the edge had already fallen again by the end of the hold window -- i.e. electrical noise,
        not a person. Skipped when ROBOT_PIR_HOLD_S=0, or if the sensor can't report its level."""
        if self._hold_s <= 0 or self._sensor is None:
            return False
        time.sleep(self._hold_s)
        try:
            return not bool(self._sensor.value)
        except Exception:
            return False          # can't tell -> trust the edge (fail OPEN: never miss a real visitor)

    def _note_chatter(self, now):
        """Count accepted triggers per window; WARN ONCE when the rate can only be a misbehaving sensor."""
        if self._chatter_n <= 0 or self._chatter_window_s <= 0:
            return
        if not self._window_start or now - self._window_start >= self._chatter_window_s:
            self._window_start, self._window_count = now, 0
            self._chatter_warned = False           # a fresh window may warn again if it's still storming
        self._window_count += 1
        # Report the ELAPSED time, not the configured window: "21 triggers in 2 min" says far more about how
        # hard the sensor is chattering than "21 in the last 10 min" would. Rendered in seconds below a minute
        # so a burst never prints as the meaningless "in 0 min".
        elapsed = max(now - self._window_start, 0.0)
        elapsed_txt = f"{elapsed:.0f}s" if elapsed < 60 else f"{elapsed / 60.0:.1f} min"
        if self._window_count > self._chatter_n and not self._chatter_warned:
            self._chatter_warned = True
            log.warning(
                "PIR CHATTER: %d motion triggers in %s on GPIO %s -- that is far more than real "
                "visitors, so the sensor is almost certainly false-triggering. Each one wakes the camera, so "
                "this burns CPU + disk all day. Usual causes: VCC on a NOISY 5V rail (try a separate supply "
                "or add a 100uF cap across the PIR's VCC/GND), the sensor pointed at the Pi's own FAN EXHAUST "
                "/ a heat source / a sunlit window, or the sensitivity pot turned right up. Probe it with "
                "./scripts/pir_sensor_probe.sh, and raise ROBOT_PIR_COOLDOWN_S / ROBOT_PIR_HOLD_S (or clear "
                "ROBOT_PIR_PIN for wake-word-only) meanwhile.",
                self._window_count, elapsed_txt, self._pin)

    def arm_cooldown(self, seconds, reason=""):
        """Suppress motion wakes for the next `seconds` -- called by main.py AFTER a look, with a duration
        chosen from what the camera actually found (nobody there = short, greeted them = long). This is the
        real rate limiter; the edge debounce above is only a pulse-train filter. `seconds` <= 0 clears any
        pending suppression (so setting the env keys to 0 restores "look at every accepted edge").

        Deliberately ABSOLUTE (a deadline, not a duration counted from the last edge): the look itself takes
        time, and the cooldown should start when we FINISHED deciding, not when the edge arrived."""
        try:
            secs = max(0.0, float(seconds or 0.0))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._suppress_until = (time.monotonic() + secs) if secs > 0 else 0.0
            self._suppress_reason = reason or ""

    def _fire(self):
        """gpiozero's rising-edge callback (its own thread). Filters the edge, then wakes the brain."""
        edge_at = time.monotonic()                 # BEFORE the hold sleep: that sleep is part of the latency
        if self._is_spike():                       # sleeps up to hold_s -- fine, this is gpiozero's thread
            self.rejected_spike += 1
            if self._verbose:
                log.info("PIR: edge on GPIO %s fell within %.2fs -- ignored as noise, not a visitor",
                         self._pin, self._hold_s)
            return
        now = time.monotonic()
        with self._lock:
            # Outcome cooldown FIRST: it is the deliberate one (the last look decided how long to wait),
            # so it should be what the counters and the verbose log attribute the rejection to.
            if self._suppress_until and now < self._suppress_until:
                self.rejected_outcome += 1
                if self._verbose:
                    log.info("PIR: motion on GPIO %s ignored -- %s cooldown, %.1fs left",
                             self._pin, self._suppress_reason or "outcome", self._suppress_until - now)
                return
            if self._last_accepted and (now - self._last_accepted) < self._cooldown_s:
                self.rejected_cooldown += 1
                if self._verbose:
                    log.info("PIR: motion on GPIO %s ignored -- still inside the %.1fs debounce (%.1fs ago)",
                             self._pin, self._cooldown_s, now - self._last_accepted)
                return
            self._last_accepted = now
            self.last_wake_at = edge_at
            self.accepted += 1
            self._note_chatter(now)
        if self._verbose:
            log.info("PIR: motion detected on GPIO %s -> wake", self._pin)
        try:
            self._on_motion()
        except Exception as e:
            log.debug("PIR motion callback error: %s", e)

    def stats_line(self):
        """One-line summary of how the sensor behaved this run (logged on shutdown), or '' if it never armed.
        A big `debounce`/`outcome` count next to a small `woke` count is the signature of a chattery PIR --
        and the SPLIT is the diagnosis: `outcome` means we looked and found nobody (the sensor is lying),
        while `debounce` is just the same pulse train arriving twice."""
        if not self.available:
            return ""
        return (f"PIR GPIO {self._pin}: woke {self.accepted}x, suppressed {self.rejected_outcome} "
                f"(outcome cooldown) + {self.rejected_cooldown} (debounce) + {self.rejected_spike} "
                f"(noise spikes)")

    def close(self):
        try:
            line = self.stats_line()
            if line:
                log.info("%s", line)
        except Exception:
            pass
        try:
            if self._sensor is not None:
                self._sensor.close()
        except Exception:
            pass
