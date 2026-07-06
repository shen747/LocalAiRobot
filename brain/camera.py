# brain/camera.py -- camera capture for the multimodal vision pipeline.
#
# This module holds the board-agnostic POLICY (enable flag, dry-run, target size, the shared
# letterbox/JPEG helper) and delegates the actual capture to a board-selected backend from the HAL:
#   Pi 5   -> Picamera2 / libcamera            (brain/picamera2_backend.py, brain/boards/pi5.py)
#   Jetson -> GStreamer/V4L2                    (added in JETSON_NANO_SUPPORT.md Phase 4)
# Every backend exposes the same .capture_jpeg()/.close()/.available surface, so the rest of the vision
# pipeline (vision.py, the REMEMBER/LOOK/EXPLORE actions) only ever sees JPEG bytes and never a board.
#
# Grabs a single still frame, letterbox-pads it toward a square ~512px JPEG (so the VLM's vision encoder
# uses a single crop -> fewer image tokens -> less CPU), and hands the JPEG bytes to vision.py. The backend
# is built LAZILY (only in live mode), so this module imports fine on a laptop / unwired board; in dry run
# there is no capture at all and vision.py falls back to ROBOT_FAKE_VISION.

import logging
import os

from driver import dry_run_enabled

log = logging.getLogger("robot.cam")


def _letterbox_square(img, size):
    from PIL import ImageOps
    return ImageOps.pad(img.convert("RGB"), (size, size), color=(0, 0, 0))


class Camera:
    """Single-frame still capture. .capture_jpeg() returns JPEG bytes or None (no camera). The actual
    capture backend (Picamera2 on the Pi, GStreamer/V4L2 on the Jetson, ...) is chosen by the board HAL;
    this class holds the enable/dry-run policy and delegates to it."""

    def __init__(self):
        self.enabled = os.environ.get("ROBOT_CAMERA_ENABLE", "1") == "1"
        self.dry_run = dry_run_enabled()
        try:
            self.size = int(os.environ.get("ROBOT_CAMERA_SIZE", "512") or 512)
        except ValueError:
            self.size = 512
        self._backend = None
        self.available = False

        if not self.enabled:
            log.info("camera disabled (ROBOT_CAMERA_ENABLE=0)")
            return
        if self.dry_run:
            log.info("camera: DRY RUN (no capture; vision uses ROBOT_FAKE_VISION or 'not visible')")
            return
        # Build the board's camera backend. Its own __init__ catches hardware failures and reports
        # available=False (so vision degrades to 'no frame'); we still guard against an unexpected raise.
        try:
            import hal
            self._backend = hal.current().make_camera(self.size)
            self.available = bool(getattr(self._backend, "available", False))
        except Exception as e:
            log.warning("camera unavailable (%s) -- vision will report no frame", e)
            self._backend = None
            self.available = False

    def capture_jpeg(self):
        """Capture one frame as preprocessed JPEG bytes, or None if no camera is available."""
        if not self.available or self._backend is None:
            return None
        return self._backend.capture_jpeg()

    def close(self):
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                pass
            self._backend = None
            self.available = False
