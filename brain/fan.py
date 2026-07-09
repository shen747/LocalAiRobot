# brain/fan.py -- optional enclosure EXHAUST fan on a GPIO pin, temperature-controlled. OPT-IN, DISABLED BY DEFAULT.
#
# In a sealed box the Pi 5 (or Jetson) active cooler just recirculates hot air; this drives an exhaust fan to
# push it OUT of the enclosure. A GPIO pin switches the fan (through a MOSFET/transistor -- a 3.3V GPIO can't
# power a fan directly), and a background thread turns it ON when the SoC runs hot and OFF once it cools, with
# hysteresis so it doesn't chatter near the threshold. Rising CPU load shows up as rising temperature, so
# temperature IS the "when the load increases" trigger.
#
# Selected by ROBOT_FAN_ENABLE=1 (+ ROBOT_FAN_PIN). Empty/0 = disabled -> make_fan() returns None and nothing
# changes. Board-neutral: the pin is driven through the HAL (hal.current().make_digital_output), so it works
# on the Pi 5 (gpiozero, BCM pin) and the Jetson (Jetson.GPIO, BOARD pin) with no code change.
#
# FAIL-SAFE: if the temperature can't be read, the fan is forced ON (better to over-cool than cook the SoC).
# SCOPE: a control seam only -- nothing else in the robot depends on it. .close() turns the fan off + stops.

import logging
import os
import threading
import time

log = logging.getLogger("robot.hw")

# SoC temperature source. sysfs thermal_zone0 is millidegrees C and exists on BOTH the Pi 5 and the Jetson
# (and needs no extra tool, unlike the Pi-only `vcgencmd`), so it's the board-neutral choice.
_TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"


def read_cpu_temp_c():
    """SoC temperature in degrees C, or None if it can't be read. Board-neutral (sysfs millidegrees)."""
    try:
        with open(_TEMP_PATH) as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def _f(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def make_fan():
    """Return a running FanController if ROBOT_FAN_ENABLE=1 and ROBOT_FAN_PIN is set, else None (the default =
    DISABLED). Never raises -- a bad pin / GPIO failure logs a warning and returns None (the robot runs fine
    without the fan; the active cooler still works)."""
    if os.environ.get("ROBOT_FAN_ENABLE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    pin = os.environ.get("ROBOT_FAN_PIN", "").strip()
    if not pin:
        log.warning("ROBOT_FAN_ENABLE=1 but ROBOT_FAN_PIN is empty -- enclosure fan disabled")
        return None
    try:
        return FanController(int(pin))
    except Exception as e:
        log.warning("enclosure fan (pin %s) init failed (%s) -- continuing without it", pin, e)
        return None


class FanController:
    """Drives an enclosure exhaust fan ON/OFF on a GPIO pin by SoC temperature, with hysteresis. A daemon
    thread polls the temperature every ROBOT_FAN_POLL_S; the fan turns ON at/above ROBOT_FAN_ON_TEMP_C and
    OFF at/below ROBOT_FAN_OFF_TEMP_C (the gap between them stops on/off chatter). FAIL-SAFE: a failed temp
    read forces the fan ON."""

    def __init__(self, pin):
        self.on_temp = _f("ROBOT_FAN_ON_TEMP_C", 70.0)
        self.off_temp = _f("ROBOT_FAN_OFF_TEMP_C", 60.0)
        # Guard an inverted config (off >= on would never settle into a stable band): keep >= 2 C hysteresis.
        if self.off_temp >= self.on_temp:
            self.off_temp = self.on_temp - 2.0
        self.period = max(_f("ROBOT_FAN_POLL_S", 5.0), 1.0)
        import hal
        self._out = hal.current().make_digital_output(pin)   # starts LOW = fan off
        self._on = False
        self._temp = None
        self._closed = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fan")
        self._thread.start()
        log.info("enclosure fan: pin %s, ON>=%.0fC / OFF<=%.0fC, poll %.0fs",
                 pin, self.on_temp, self.off_temp, self.period)

    def _set(self, on):
        if on == self._on:
            return
        try:
            (self._out.on if on else self._out.off)()
            self._on = on
        except Exception as e:
            log.warning("enclosure fan: GPIO write failed (%s)", e)

    def _loop(self):
        while not self._closed:
            t = read_cpu_temp_c()
            self._temp = t
            if t is None:
                self._set(True)                 # FAIL-SAFE: unknown temp -> cool
            elif t >= self.on_temp:
                self._set(True)
            elif t <= self.off_temp:
                self._set(False)
            # between off_temp..on_temp: hold the current state (that's the hysteresis)
            time.sleep(self.period)

    def status(self):
        """(temp_c or None, fan_on) -- lets diagnostics/logging peek without touching the GPIO."""
        return self._temp, self._on

    def close(self):
        self._closed = True
        if self._thread.is_alive():
            self._thread.join(timeout=self.period + 1.0)
        try:
            self._out.off()
            self._out.close()
        except Exception:
            pass


if __name__ == "__main__":   # self-test: ROBOT_FAN_ENABLE=1 ROBOT_FAN_PIN=<n> python3 brain/fan.py
    logging.basicConfig(level=logging.INFO)
    fan = make_fan()
    if fan is None:
        t = read_cpu_temp_c()
        print(f"Fan disabled (set ROBOT_FAN_ENABLE=1 + ROBOT_FAN_PIN). CPU temp now: {t:.1f}C"
              if t is not None else "Fan disabled; CPU temp unavailable.")
    else:
        try:
            for _ in range(30):
                t, on = fan.status()
                print(f"temp={t:.1f}C  fan={'ON' if on else 'off'}"
                      if t is not None else f"temp=?  fan={'ON' if on else 'off'}")
                time.sleep(1.0)
        finally:
            fan.close()
