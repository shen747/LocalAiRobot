# brain/hal.py -- board Hardware Abstraction Layer (HAL).
#
# The robot runs the same brain on different single-board computers (Raspberry Pi 5 today, NVIDIA Jetson
# next, others later). Almost everything is already board-agnostic -- the AI stack is subprocess/HTTP and
# the audio is ALSA/PortAudio. Only THREE concerns are board-specific:
#
#   1. distance sensor -- an object exposing `.distance` (metres) + `.close()`   (HC-SR04 on the GPIO)
#   2. PWM kit         -- a ServoKit-compatible object (ESC throttle + steering servo over I2C)
#   3. camera          -- an object exposing `.capture_jpeg()` + `.close()`
#
# ...plus a little board metadata (name, an auto-detect predicate, the pin-numbering scheme, a default pin
# map, and any launch-time env such as the Pi's gpiozero pin factory).
#
# Each board is a small self-registering module under brain/boards/ that supplies those. Core code
# (driver.py, camera.py, main.py) asks hal.current() for the backends and NEVER branches on a board name,
# so adding a board is purely additive: drop brain/boards/<name>.py, implement the factories, register it.
#
# NOTE: this module is deliberately NOT named board.py -- Adafruit Blinka (pulled in by adafruit_servokit)
# does `import board`, and brain/ is on sys.path, so a brain/board.py would shadow it and break the PCA9685.
#
# Pure stdlib; importing hal is cheap and safe on any machine (no hardware libraries are touched until a
# factory is actually called, which only happens in live mode).

import logging
import os

try:
    from typing import Protocol
except ImportError:  # pragma: no cover -- Protocol exists on every supported Python, this is belt-and-braces
    Protocol = object

log = logging.getLogger("robot.hal")


class DistanceSensor(Protocol):
    """The subset of gpiozero.DistanceSensor the driver relies on."""

    @property
    def distance(self) -> float: ...   # METRES (driver.py multiplies by 100)

    def close(self) -> None: ...


class Board(Protocol):
    """What every brain/boards/<name>.py must provide. Duck-typed; this Protocol is documentation."""

    name: str            # "pi5", "jetson", ...  (matches ROBOT_BOARD)
    pin_scheme: str      # "BCM" | "BOARD"

    def matches(self) -> bool: ...                        # auto-detect (usually /proc/device-tree/model)
    def default_pins(self) -> dict: ...                   # {"ROBOT_TRIG_PIN": 23, ...}
    def launch_env(self) -> dict: ...                     # {"GPIOZERO_PIN_FACTORY": "lgpio"} or {}
    def make_pwm_kit(self, channels: int): ...            # -> ServoKit-compatible object
    def make_distance_sensor(self, trigger, echo, max_distance) -> "DistanceSensor": ...
    def make_camera(self, size: int): ...                 # -> object with .capture_jpeg()/.close()


_REGISTRY = []
_current = None


def register(board):
    """Called at import time by each brain/boards/<name>.py module."""
    _REGISTRY.append(board)


def current():
    """Return the selected board (cached). On first call this imports brain/boards (so every board
    self-registers), selects one, and applies its launch env (e.g. the Pi's gpiozero pin factory) via
    os.environ.setdefault -- so any hardware entry point (main.py OR diagnostics.py) gets it right, and
    anything already set in the environment still wins."""
    global _current
    if _current is None:
        if not _REGISTRY:
            import boards  # noqa: F401 -- side effect: each board module calls register()
        _current = _select()
        for k, v in (_current.launch_env() or {}).items():
            os.environ.setdefault(k, str(v))
        log.info("board selected: %s (pin scheme %s)", _current.name, _current.pin_scheme)
    return _current


def _select():
    """ROBOT_BOARD forces a board by name; 'auto' (default) picks the first whose matches() is true;
    pi5 is the safe fallback (e.g. on a dev laptop with no device tree)."""
    forced = os.environ.get("ROBOT_BOARD", "auto").strip().lower()
    if forced and forced != "auto":
        for b in _REGISTRY:
            if b.name == forced:
                return b
        raise ValueError(f"unknown ROBOT_BOARD={forced!r}; known={[b.name for b in _REGISTRY]}")
    for b in _REGISTRY:
        try:
            if b.matches():
                return b
        except Exception:
            continue
    for b in _REGISTRY:
        if b.name == "pi5":
            return b
    return _REGISTRY[0]   # last resort: whatever registered first
