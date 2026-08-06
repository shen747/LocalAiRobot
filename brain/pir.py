# brain/pir.py -- PIR motion sensor as an extra WAKE trigger for Concierge Mode.
#
# A passive-infrared sensor (e.g. Adafruit PIR, 3.3V digital out -> Pi-safe) fires when a person walks up to
# the robot. We treat that exactly like the wake word: on motion we set a shared Event AND call
# wake.cancel_listen(), which breaks the in-progress openWakeWord listen out with 'CANCELLED' -- so
# wait_for_trigger() returns immediately and the main loop runs the concierge greeting pipeline. The wake
# word keeps working in parallel; motion is purely additional.
#
# gpiozero.DigitalInputDevice on the Pi (same lgpio pin factory as the ultrasonics), driven by EDGE
# callbacks rather than polling -- see the note on the construction below for why that matters on a sensor
# with no output latch. Lazy import + graceful degrade: no ROBOT_PIR_PIN, or no gpiozero (laptop/Jetson) ->
# available=False, a harmless no-op.
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

    # Identifies this source in the wake log when several are OR'd together (wake_sources.CompositeWake).
    source_name = "gpio-pir"

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
        self.rejected_warmup = 0
        self._warmup_s = max(0.0, _envf("ROBOT_PIR_WARMUP_S", 0))
        self._ready_at = 0.0        # monotonic deadline; 0 = no warm-up window
        # Presence stickiness for is_active() -- see its docstring. Must comfortably exceed the GAP between
        # a PIR's output pulses (not the pulse itself): the SEN0171 latches ~2.3-3s and re-triggers, so 5s
        # spans a missed pulse without keeping a departed visitor "present" for long.
        self._last_high = 0.0
        self._presence_hold_s = max(0.0, _envf("ROBOT_PIR_PRESENCE_HOLD_S", 5))
        if not pin:
            return
        try:
            # EDGE-DRIVEN, NOT POLLED. This used to be gpiozero.MotionSensor, a SmoothedInputDevice that
            # SAMPLES the pin at 10Hz and reports through a queue. Whether that can MISS a detection depends
            # on the part, and the PaPIRs family is not consistent about it:
            #   EKMC1605111  -- no hold time, no mask time, no minimum pulse width; the output just tracks
            #                   target present/absent, so a real detection CAN be shorter than one sampling
            #                   period and vanish unseen. Polling is genuinely unsafe here.
            #   EKMB1105112  -- has a Standby Hold Time of ~2.6s (extended while motion continues) and then
            #                   a ~1.3s Mask Time. A 100ms sampling period cannot miss a 2600ms pulse, so
            #                   polling would in fact have been safe on this one.
            # Edge callbacks are the right answer for both: correct on the parts that need it, and on the
            # rest they simply remove up to a full sampling period (100ms) of latency from every wake, which
            # is worth having on a greeter people walk straight past. DigitalInputDevice hands the callback
            # straight off the pin factory's edge detection (lgpio -> kernel edge events on the Pi 5).
            from gpiozero import DigitalInputDevice
            # PULL. What the sensor's output stage can DRIVE decides this, and the two families differ:
            #   HC-SR501-class module -- push-pull, drives HIGH hard and LOW hard.
            #   PaPIRs (EKMB/EKMC)    -- SOURCES current only (datasheet 4-7 wiring diagram: current flows
            #                            OUT of the sensor into your load, Iout max 100uA), active-HIGH, and
            #                            high-impedance when idle. It cannot pull the pin down itself.
            # Both therefore want the internal pull-DOWN (the default): it defines the LOW state that the
            # PaPIRs leaves floating, and on the HC-SR501 it is free insurance that an UNWIRED pin reads LOW
            # instead of floating and inventing motion. The Pi's pull-down is ~50k, which at 3.3V draws ~66uA
            # -- inside the 100uA budget -- and Vout is specified as >= Vdd-0.5V, i.e. 2.8V on a 3.3V supply,
            # comfortably above the Pi's ~2.31V logic-high threshold. Verified against EKMB1105112 4-4/4-7.
            # ROBOT_PIR_PULL=none removes the resistor entirely: correct ONLY for a sensor that actively
            # drives BOTH states. On a source-only or open-collector output a floating pin is noise, so do not
            # use it for the PaPIRs -- an idle pin would wander and invent visitors all night.
            pull = (os.environ.get("ROBOT_PIR_PULL", "") or "down").strip().lower()
            if pull in ("none", "float", "off"):
                self._sensor = DigitalInputDevice(int(pin), pull_up=None, active_state=True)
            elif pull == "up":                       # active-LOW modules (open-collector "alarm" outputs)
                self._sensor = DigitalInputDevice(int(pin), pull_up=True)
            else:
                self._sensor = DigitalInputDevice(int(pin))
            self._sensor.when_activated = self._fire   # gpiozero fires this on its own thread
            self.available = True
            # WARM-UP. A PIR's output is UNDEFINED while its pyroelectric front-end settles, and the datasheet
            # figure is not small: Panasonic PaPIRs specifies up to 30 SECONDS, during which the pin may toggle
            # on its own. Arming immediately means a burst of phantom wakes after every boot -- and since the
            # brain is starting up at that exact moment, they are the least useful wakes of the whole run.
            # We arm the sensor now (so the pin is claimed) but IGNORE its edges until the window expires.
            if self._warmup_s > 0:
                self._ready_at = time.monotonic() + self._warmup_s
                log.info("PIR motion wake: warming up for %.0fs -- edges ignored until the sensor settles "
                         "(ROBOT_PIR_WARMUP_S; PaPIRs EKMB Circuit Stability Time is 25s TYP but 210s MAX, "
                         "HC-SR501 needs ~5s). The wake WORD is unaffected during this window.",
                         self._warmup_s)
            log.info("PIR motion wake: armed on BCM GPIO %s (Concierge Mode) -- hold %.2fs, debounce %.1fs, "
                     "pull=%s (outcome cooldowns are set by the concierge look, not here)",
                     pin, self._hold_s, self._cooldown_s, pull)
        except Exception as e:
            log.warning("PIR motion sensor init failed on pin %s (%s) -- motion wake disabled "
                        "(the wake word still works)", pin, e)

    @property
    def is_active(self):
        """Is somebody STILL THERE according to the sensor? Used by the concierge to keep looking rather
        than stopping at a fixed frame budget.

        *** THIS IS NOT A BARE PIN READ, AND IT MUST NOT BE. ***  A PIR's output is a PULSE TRAIN, not a
        level: this build's SEN0171 latches HIGH for ~2.3-3s per trigger, then drops LOW, then re-triggers
        if the person is still moving. The indicator LED follows it, and the gaps are far too short to see
        -- so the human watching it reads "solidly on" while a bare `self._sensor.value` sampled inside one
        of those gaps reads False.

        That mismatch is exactly what made the concierge give up with the sensor apparently still lit: a
        41-give-up field log showed EVERY Stage A loop ending on a pin read (never on the safety watchdog),
        at 4.1-12.1s -- i.e. landing in the LOW gaps of a pulse train that was still running.

        So presence is STICKY: HIGH now, or HIGH within the last ROBOT_PIR_PRESENCE_HOLD_S. That window
        only has to outlast the gap BETWEEN pulses, not the whole visit -- each new pulse re-stamps it, so
        genuine presence extends indefinitely while a person who has actually left expires one window after
        their last pulse. False on any error or when unavailable, so a caller can never hang on it."""
        # getattr rather than a bare attribute: is_active is called from a lambda inside the concierge's
        # look, so an AttributeError here would abort the whole look rather than just misreport presence.
        return self.presence_age() < getattr(self, "_presence_hold_s", 5.0)

    def presence_age(self):
        """SECONDS SINCE THE SENSOR WAS LAST HIGH -- the single source of truth about presence, so callers
        that need different patience can each pick their own window off one reading.

        Two callers want very different things from the same pulse train:
          * the FACE wants to stop smiling promptly once you walk away (a short window, ~1.5s -- just enough
            to bridge the gap between pulses so it doesn't flicker);
          * the RETRY LOOP wants to keep hunting for a face for a good while longer (~5s), because the
            camera may still catch someone the instant before they leave.
        Returning the AGE rather than a bool is what lets both read the same sensor and disagree usefully.

        Reading this also STAMPS the clock when the pin is high, so it doubles as the sampler: callers poll
        it every few hundred ms during a look, which is far faster than the ~2.3-3s pulses it has to track.
        Returns float('inf') when unavailable or unreadable, so every comparison is safely False."""
        if self._sensor is None:
            return float("inf")
        try:
            live = bool(self._sensor.value)
        except Exception:
            return float("inf")
        now = time.monotonic()
        if live:
            self._last_high = now
            return 0.0
        return (now - self._last_high) if self._last_high else float("inf")

    # -- trigger filters ---------------------------------------------------------------------------------
    def _is_spike(self):
        """True when the edge had already fallen again by the end of the hold window -- i.e. electrical noise,
        not a person. Skipped when ROBOT_PIR_HOLD_S=0, or if the sensor can't report its level.

        !! THIS FILTER NEEDS A LATCHED OUTPUT, so check your sensor before trusting it. It assumes an
        HC-SR501-style held pulse, where "still HIGH 100ms later" really does separate a person from a spike.
        Where that holds:
          HC-SR501     -- ~2.5s latch.                                          keep the default.
          EKMB1105112  -- Standby Hold Time ~2.6s typ, then a ~1.3s Mask Time.  keep the default.
          EKMC1605111  -- NO hold time, NO mask time, NO minimum pulse width.   SET ROBOT_PIR_HOLD_S=0.
        On a part with no latch a perfectly genuine short detection has already fallen by the time we
        re-read, so this discards EVERY real visitor and files them as noise -- the symptom is the
        `noise spikes` counter climbing in lockstep with people walking past. The fallback transient filter
        for those parts is the concierge's Stage-A camera probe (~120ms, and it settles the question with
        evidence rather than a guess), which is what Panasonic's suggested "pulse count circuit" is for."""
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
                "this burns CPU + disk all day. Usual causes: VCC on a NOISY rail (a decoupling cap across "
                "the sensor's VCC/GND is REQUIRED by the PaPIRs datasheet, not optional), the sensor pointed "
                "at the Pi's own FAN EXHAUST / a heat source / a sunlit window / an HVAC vent, a sensitivity "
                "pot turned right up, or -- on a source-only output like the PaPIRs -- ROBOT_PIR_PULL=none "
                "leaving the pin floating when it should be 'down'. Probe it with "
                "./scripts/pir_sensor_probe.sh, and raise ROBOT_PIR_COOLDOWN_S (or clear ROBOT_PIR_PIN for "
                "wake-word-only) meanwhile. Do NOT reach for ROBOT_PIR_HOLD_S on a latch-less sensor -- see "
                "_is_spike(); it will start discarding real visitors instead.",
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
        # Warm-up FIRST, before the spike filter even sleeps: during the window the pin is meaningless, so
        # there is nothing to measure and no point paying hold_s to measure it.
        if self._ready_at and edge_at < self._ready_at:
            self.rejected_warmup += 1
            if self._verbose:
                log.info("PIR: edge on GPIO %s ignored -- still warming up (%.0fs left)",
                         self._pin, self._ready_at - edge_at)
            return
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
        warm = f" + {self.rejected_warmup} (warm-up)" if self.rejected_warmup else ""
        return (f"PIR GPIO {self._pin}: woke {self.accepted}x, suppressed {self.rejected_outcome} "
                f"(outcome cooldown) + {self.rejected_cooldown} (debounce) + {self.rejected_spike} "
                f"(noise spikes){warm}")

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
