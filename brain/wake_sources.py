# brain/wake_sources.py -- fan SEVERAL Concierge wake sensors into one, presenting the same surface as one.
#
# THE "OR": whichever sensor fires FIRST wakes the robot. Anything from any source inside the dedupe window
# is the SAME arrival seen twice (a visitor crossing both a wired PIR and a Zigbee one), not a second person.
#
# WHY THIS EXISTS AT ALL. brain/main.py consumes a wake sensor through a narrow surface -- available /
# arm_cooldown / stats_line / close / last_wake_at + four counters -- and nothing downstream cares which
# hardware produced the edge. So "two sensors OR'd together" needs no changes to the two-stage look, the
# outcome cooldowns, the latency line or the hourly summary: it just needs one more object honouring that
# same surface. This is that object.
#
# DEGRADATION IS THE POINT. Every source is optional and independent:
#   * GPIO PIR only  (no dongle)      -> the composite holds one available source; identical behaviour to
#                                        before this file existed.
#   * BOTH                            -> either wakes the robot, duplicates collapse into one look.
#   * Zigbee only    (ROBOT_PIR_PIN empty, or the PIR unplugged) -> still wakes. A wired sensor is not
#                                        required by anything here.
#   * NEITHER available               -> available=False, and main.py falls back to wake-word-only exactly
#                                        as it does with a single unconfigured sensor.
# A broker outage, a flat sensor battery or a yanked dongle must never take the wired PIR down with it.

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


class CompositeWake:
    """Hold N wake sources and behave exactly like one of them.

    Construct, then add() each source built with a callback from make_callback():

        composite = CompositeWake(on_motion)
        composite.add(MotionWake(pin, composite.make_callback("gpio-pir")))
        composite.add(ZigbeeWake(composite.make_callback("zigbee-pir")))

    The two-step exists because each source needs the composite's callback while the composite needs the
    sources -- add() breaks the cycle without leaving a half-built object lying around.
    """

    source_name = "composite"

    def __init__(self, on_motion, dedupe_s=None):
        self._sources = []
        self._on_motion = on_motion
        self._lock = threading.Lock()
        self._by_name = {}          # source_name -> source, filled by add(); see make_callback
        self._last_fire = 0.0
        self.last_wake_at = 0.0
        self.winner = None          # which source woke us LAST
        self.wins = {}              # name -> count. The line that says whether a sensor earns its keep.
        self.accepted = 0           # times the ROBOT actually woke (NOT the sum of the sources -- see below)
        self.deduped = 0            # second-source hits collapsed into an existing wake
        self._dedupe_s = max(0.0, _envf("ROBOT_WAKE_DEDUPE_S", 2.0 if dedupe_s is None else dedupe_s))

    def add(self, source):
        if source is not None:
            self._sources.append(source)
            # Register by name so make_callback's closure can find its own source at fire time and read
            # that source's edge timestamp (see the note there). Built after the callback by necessity.
            self._by_name[getattr(source, "source_name", type(source).__name__)] = source
        return source

    def log_ready(self):
        """Say what actually came up. Call once after every add(), so the log shows the REAL set -- a
        missing dongle or an empty ROBOT_PIR_PIN is then obvious at a glance instead of being inferred
        from the absence of wakes hours later."""
        names = [n for n, _ in self.available_sources()]
        if not names:
            log.info("wake sources: NONE available -- motion wake is off, the wake word still works")
        else:
            log.info("wake sources: %s (dedupe %.1fs)%s", " OR ".join(names), self._dedupe_s,
                     "" if len(names) > 1 else "  [single source]")
        for name, src in self._all_sources():
            if not getattr(src, "available", False):
                log.info("wake sources: %s is NOT available -- the rest carry on without it", name)

    def _all_sources(self):
        return [(getattr(s, "source_name", type(s).__name__), s) for s in self._sources]

    def available_sources(self):
        return [(n, s) for n, s in self._all_sources() if getattr(s, "available", False)]

    def make_callback(self, name):
        """Build the on_motion each source is constructed with, so we learn WHO won the race."""
        def _cb():
            now = time.monotonic()
            # TAKE THE SOURCE'S OWN EDGE TIMESTAMP when it has one, rather than stamping at callback time.
            # MotionWake deliberately records last_wake_at as the moment the PIN went high, BEFORE it
            # sleeps ROBOT_PIR_HOLD_S to filter spikes (pir.py: "BEFORE the hold sleep: that sleep is part
            # of the latency"). Stamping here instead would start the clock after that sleep and silently
            # shave hold_s off every "[N.NNs from PIR to decision]" line -- the one number used to judge
            # whether the greeter feels quick, reported as better than it is. Looked up by name at CALL
            # time because the callback has to be built before the source that owns it exists. Falling
            # back to `now` keeps sources with no edge clock working unchanged.
            src = self._by_name.get(name)
            edge_at = getattr(src, "last_wake_at", 0.0) or now
            deduped_after = None
            with self._lock:
                if self._last_fire and (now - self._last_fire) < self._dedupe_s:
                    self.deduped += 1
                    deduped_after = (now - self._last_fire, self.winner)
                else:
                    self._last_fire = now
                    self.last_wake_at = edge_at
                    self.winner = name
                    self.accepted += 1
                    self.wins[name] = self.wins.get(name, 0) + 1
            if deduped_after is not None:
                # Expected and healthy when two sensors cover the same doorway: this IS the OR
                # collapsing one arrival into one look. Only a problem if it is ALL you ever see.
                log.debug("wake: %s fired %.2fs after %s -- same arrival, deduped",
                          name, deduped_after[0], deduped_after[1])
                return
            try:
                self._on_motion()
            except Exception as e:
                log.debug("wake callback error after %s: %s", name, e)
        return _cb

    # -- the surface brain/main.py consumes ------------------------------------------------------------
    @property
    def available(self):
        """True while ANY source is usable. One dead sensor must not disable motion wake."""
        return any(getattr(s, "available", False) for s in self._sources)

    @property
    def is_active(self):
        """True while ANY source still asserts presence -- the OR again, now for 'are they still there?'
        rather than 'did someone arrive?'. Stage A keeps looking while this holds."""
        for _, s in self._all_sources():
            try:
                if getattr(s, "is_active", False):
                    return True
            except Exception:
                pass
        return False

    def presence_age(self):
        """FRESHEST presence across all sources -- the OR, applied to 'how recently'. Whichever sensor saw
        somebody most recently wins, so one source going quiet never ages out presence another still sees."""
        best = float("inf")
        for _, s in self._all_sources():
            try:
                age = s.presence_age()
            except Exception:
                continue
            if age < best:
                best = age
        return best

    def arm_cooldown(self, seconds, reason=""):
        """*** THE LOAD-BEARING METHOD. ***

        main.py calls this after EVERY look, with a duration chosen from what the camera found -- short for
        'nobody there', long for 'already greeting them'. It MUST reach every source. Suppress only the
        wired PIR and the Zigbee one keeps firing straight through the cooldown: the camera runs flat out on
        an empty hallway and the greeting re-fires at someone who is mid-conversation. The whole two-stage
        design rests on this fanning out."""
        for name, s in self._all_sources():
            try:
                s.arm_cooldown(seconds, reason)
            except Exception as e:
                log.debug("arm_cooldown failed on %s: %s", name, e)

    # Counters. main.py's hourly summary reads these four straight off the object, so they must all exist
    # and must all be ints -- a source lacking one (ZigbeeWake has no spike filter) contributes 0 rather
    # than raising. `accepted` is deliberately OUR count, not the sum: two sources firing for one visitor
    # is ONE wake for the robot, and summing them would inflate the headline number in the summary. The
    # collapsed duplicates land in rejected_cooldown, which is exactly what a debounce means.
    def _sum(self, attr):
        return sum(int(getattr(s, attr, 0) or 0) for s in self._sources)

    @property
    def rejected_outcome(self):
        return self._sum("rejected_outcome")

    @property
    def rejected_cooldown(self):
        return self._sum("rejected_cooldown") + self.deduped

    @property
    def rejected_spike(self):
        return self._sum("rejected_spike")

    @property
    def rejected_warmup(self):
        return self._sum("rejected_warmup")

    @property
    def cleared_on_departure(self):
        return self._sum("cleared_on_departure")

    @property
    def arrivals_after_gap(self):
        """Summed across sources: rising edges that followed a real quiet gap. main.py samples this to
        tell a new visitor arriving mid-conversation from the person it is already talking to."""
        return self._sum("arrivals_after_gap")

    def stats_line(self):
        lines = []
        for s in self._sources:
            try:
                ln = s.stats_line()
            except Exception:
                ln = ""
            if ln:
                lines.append(ln)
        if not lines:
            return ""
        # THE line that answers "is the second sensor earning its keep?". A source that never wins is
        # mounted where another already covers -- move it, or drop it.
        won = ", ".join(f"{n}={c}" for n, c in sorted(self.wins.items())) or "none"
        lines.append(f"wake wins: {won} ({self.deduped} duplicate(s) collapsed into an existing wake)")
        return " | ".join(lines)

    def close(self):
        for name, s in self._all_sources():
            try:
                s.close()
            except Exception as e:
                log.debug("close failed on %s: %s", name, e)
