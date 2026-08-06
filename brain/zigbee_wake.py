# brain/zigbee_wake.py -- a WIRELESS Zigbee occupancy sensor as an extra Concierge wake source.
#
# Reads a Zigbee2MQTT device topic over MQTT and wakes the robot on the sensor's RISING edge, exposing the
# SAME surface as pir.MotionWake (available / arm_cooldown / stats_line / close / last_wake_at + counters)
# so brain/main.py holds it without knowing which kind of sensor it is. Normally used via
# wake_sources.CompositeWake, OR'd with the wired GPIO PIR.
#
# WHAT THIS BUYS YOU: COVERAGE, NOT SPEED. The GPIO PIR interrupts in microseconds; a Zigbee event travels
# sensor -> coordinator -> Zigbee2MQTT -> broker -> here, which is 50-300ms on a good day. For the SAME
# person the wired sensor wins essentially every race. This sensor earns its place by watching somewhere the
# wired one cannot see -- round a corner, the far end of the hall, the top of the stairs. Site it there.
#
# EVERY FAILURE DEGRADES TO available=False, never to an exception: no paho installed, no topic configured,
# broker refusing connections, dongle unplugged. A missing wireless sensor must never be the difference
# between a working robot and a silent one -- the wired PIR and the wake word carry on untouched.

import json
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


class ZigbeeWake:
    """Wake on a Zigbee occupancy sensor's rising edge, delivered over MQTT by Zigbee2MQTT."""

    source_name = "zigbee-pir"

    def __init__(self, on_motion):
        self._on_motion = on_motion
        self._client = None
        self.available = False
        self.last_wake_at = 0.0
        self.accepted = 0
        self.rejected_cooldown = 0
        self.rejected_outcome = 0
        self.rejected_retained = 0    # broker replaying old state, not an arrival
        self.rejected_falling = 0     # occupancy -> false: the CLEAR, not the trigger
        self.rejected_spike = 0       # no spike filter here; present so the composite can sum it blindly
        self.rejected_warmup = 0
        self._lock = threading.Lock()
        self._last_accepted = 0.0
        self._suppress_until = 0.0
        self._suppress_reason = ""
        self._occupied = False        # so only false->true counts as an arrival
        self._online = None           # from the Z2M availability topic; None = not heard from yet
        self._topic = ""
        self._cooldown_s = max(0.0, _envf("ROBOT_ZIGBEE_COOLDOWN_S", 2))

        topic = (os.environ.get("ROBOT_ZIGBEE_TOPIC", "") or "").strip()
        if not topic:
            # Not configured. Silent on purpose: this is the normal state for every build without a dongle,
            # and a warning here would cry wolf on every boot of the reference robot.
            return
        self._topic = topic
        self._avail_topic = topic + "/availability"
        host = (os.environ.get("ROBOT_MQTT_HOST", "") or "localhost").strip()
        port = int(_envf("ROBOT_MQTT_PORT", 1883))

        try:
            import paho.mqtt.client as mqtt
        except Exception as e:
            log.warning("zigbee wake: paho-mqtt not installed (%s) -- wireless wake OFF, the GPIO PIR and "
                        "the wake word are unaffected. Install it with "
                        "./scripts/patches/common/6_wake_zigbee_pir.sh", e)
            return

        try:
            # paho-mqtt 2.x REQUIRES an explicit callback API version. VERSION2 changes on_connect's
            # signature (reason_code + properties instead of rc); on_message is unchanged. Pinning to
            # VERSION2 rather than the VERSION1 shim means no deprecation warning now and no breakage when
            # the shim is eventually dropped.
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="localairobot-wake")
            user = (os.environ.get("ROBOT_MQTT_USER", "") or "").strip()
            if user:
                self._client.username_pw_set(user, os.environ.get("ROBOT_MQTT_PASS", "") or "")
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect
            # Let paho own the reconnect backoff. A broker restart, or Zigbee2MQTT coming up AFTER the
            # robot, must not need a robot restart -- connect_async + loop_start means we attach whenever
            # the broker appears, so no startup ordering sleep is ever required.
            self._client.reconnect_delay_set(min_delay=1, max_delay=60)
            self._client.connect_async(host, port, keepalive=60)
            self._client.loop_start()   # background thread -- matches the threaded brain, no event loop
            self.available = True
            log.info("zigbee wake: watching %r via %s:%d (debounce %.1fs) -- OR'd with the GPIO PIR",
                     topic, host, port, self._cooldown_s)
        except Exception as e:
            log.warning("zigbee wake disabled (%s) -- the GPIO PIR and the wake word are unaffected", e)
            self.available = False
            self._client = None

    # -- MQTT plumbing ---------------------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.warning("zigbee wake: MQTT connect refused (%s) -- retrying in the background", reason_code)
            return
        client.subscribe([(self._topic, 0), (self._avail_topic, 0)])
        log.info("zigbee wake: MQTT connected, subscribed to %r", self._topic)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        # INFO, not WARNING: paho reconnects on its own, and a broker restart is routine. The thing worth
        # warning about is the SENSOR going quiet, which the availability topic reports separately.
        log.info("zigbee wake: MQTT disconnected (%s) -- reconnecting in the background", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            if msg.topic == self._avail_topic:
                self._note_availability(msg.payload)
                return
            # RETAINED = the broker replaying the LAST known state to every new subscriber. A retained
            # `occupancy: true` would wake the camera for someone who walked past an hour ago -- and would
            # do it again on every single reconnect. Never an arrival.
            if getattr(msg, "retain", False):
                self.rejected_retained += 1
                log.debug("zigbee wake: ignoring RETAINED %s (broker replay, not an arrival)", msg.topic)
                return
            data = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception as e:
            log.debug("zigbee wake: unparsable payload on %s (%s)", getattr(msg, "topic", "?"), e)
            return
        if not isinstance(data, dict):
            return
        occ = data.get("occupancy")
        if occ is None:
            occ = data.get("occupancy_state")     # some vendors use this spelling
        if occ is None:
            return                                # a battery/linkquality-only report; not about presence
        if isinstance(occ, str):
            occ = occ.strip().lower() in ("true", "1", "on", "occupied", "detected")
        occ = bool(occ)
        with self._lock:
            was, self._occupied = self._occupied, occ
        # RISING EDGE ONLY. Z2M publishes `true` on detection and `false` once the sensor's occupancy_timeout
        # expires. Treating every message as a trigger would wake the camera on the CLEAR as well -- i.e.
        # reliably, 60-90s after the person has already left.
        if occ and not was:
            self._fire()
        elif not occ:
            self.rejected_falling += 1

    def _note_availability(self, payload):
        raw = payload.decode("utf-8", "replace").strip().lower()
        online = "offline" not in raw          # Z2M sends "online"/"offline" or {"state":"online"}
        if self._online is not None and online != self._online:
            # A battery sensor that has gone flat is otherwise INVISIBLE: it simply stops waking the robot,
            # which looks exactly like "nobody came by". Say it out loud, once, on the transition.
            if online:
                log.info("zigbee wake: sensor %r is back ONLINE", self._topic)
            else:
                log.warning("zigbee wake: sensor %r went OFFLINE -- check its battery. Motion wake now "
                            "relies on the GPIO PIR alone.", self._topic)
        self._online = online

    @property
    def is_active(self):
        """Is the sensor reporting occupancy RIGHT NOW? Mirrors pir.MotionWake.is_active so Stage A can keep
        looking while presence holds. This is the LAST STATE PUBLISHED, not a live read -- a Zigbee sensor
        only speaks when something changes -- so it stays True until the sensor's own occupancy_timeout
        expires (typically 60-90s). That is far longer than a PIR's ~3s latch, which is precisely why Stage
        A's max_s ceiling matters more here than on the wired pin."""
        return bool(self._occupied) if self.available else False

    def presence_age(self):
        """Seconds since this sensor last reported presence -- mirrors pir.MotionWake.presence_age so the
        face and the retry loop can apply their own windows to either source. A Zigbee sensor only speaks on
        change, so this is 0.0 while occupied and inf otherwise: there is no pulse train to age out, and its
        own occupancy_timeout is what eventually publishes the clear."""
        return 0.0 if self.is_active else float("inf")

    # -- the same filter chain as pir.MotionWake -------------------------------------------------------
    def _fire(self):
        edge_at = time.monotonic()
        with self._lock:
            # Outcome cooldown FIRST: it is the deliberate one, armed by main.py from what the camera
            # actually found, so it should be what the counters attribute the rejection to.
            if self._suppress_until and edge_at < self._suppress_until:
                self.rejected_outcome += 1
                return
            if self._last_accepted and (edge_at - self._last_accepted) < self._cooldown_s:
                self.rejected_cooldown += 1
                return
            self._last_accepted = edge_at
            self.last_wake_at = edge_at
            self.accepted += 1
        try:
            self._on_motion()
        except Exception as e:
            log.debug("zigbee wake callback error: %s", e)

    def arm_cooldown(self, seconds, reason=""):
        """Suppress wakes for `seconds`. Same contract as pir.MotionWake -- see CompositeWake.arm_cooldown
        for why it is essential that this reaches EVERY source and not just the wired one."""
        try:
            secs = max(0.0, float(seconds or 0.0))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._suppress_until = (time.monotonic() + secs) if secs > 0 else 0.0
            self._suppress_reason = reason or ""

    def stats_line(self):
        if not self.available:
            return ""
        flag = " [SENSOR OFFLINE]" if self._online is False else ""
        return (f"Zigbee {self._topic}: woke {self.accepted}x, suppressed {self.rejected_outcome} "
                f"(outcome cooldown) + {self.rejected_cooldown} (debounce), ignored "
                f"{self.rejected_retained} retained + {self.rejected_falling} clear(s){flag}")

    def close(self):
        try:
            line = self.stats_line()
            if line:
                log.info("%s", line)
        except Exception:
            pass
        try:
            if self._client is not None:
                self._client.loop_stop()
                self._client.disconnect()
        except Exception:
            pass
