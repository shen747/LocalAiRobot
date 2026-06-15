# brain/camera.py -- IMX219 camera capture for the multimodal vision pipeline. Works with any
# libcamera-supported camera; the build uses an IMX219 module -- a Pi Camera V2 NoIR OR an Arducam
# IMX219 8MP (same sensor, same driver, interchangeable; no code change to swap).
#
# Grabs a single still frame, letterbox-pads it toward a square ~512px JPEG (so the VLM's vision
# encoder uses a single crop -> fewer image tokens -> less CPU on the Pi 5), and hands
# the JPEG bytes to vision.py. picamera2 is imported LAZILY inside the live branch so this
# module imports fine on a laptop / unwired Pi; in dry run there is no capture at all and
# vision.py falls back to ROBOT_FAKE_VISION (so the explore loop is testable with no camera).
#
# Install on the Pi (NOT pip):  sudo apt install -y python3-picamera2 --no-install-recommends
# and create the venv with --system-site-packages so it can see libcamera (see install_stack.sh).

import logging
import os
from io import BytesIO

from driver import dry_run_enabled

log = logging.getLogger("robot.cam")


def _letterbox_square(img, size):
    from PIL import ImageOps
    return ImageOps.pad(img.convert("RGB"), (size, size), color=(0, 0, 0))


class Camera:
    """Single-frame still capture. .capture_jpeg() returns JPEG bytes or None (no camera)."""

    def __init__(self):
        self.enabled = os.environ.get("ROBOT_CAMERA_ENABLE", "1") == "1"
        self.dry_run = dry_run_enabled()
        try:
            self.size = int(os.environ.get("ROBOT_CAMERA_SIZE", "512") or 512)
        except ValueError:
            self.size = 512
        try:
            self.warmup = float(os.environ.get("ROBOT_CAMERA_WARMUP_S", "1.0") or 1.0)
        except ValueError:
            self.warmup = 1.0
        self._picam = None
        self.available = False

        if not self.enabled:
            log.info("camera disabled (ROBOT_CAMERA_ENABLE=0)")
            return
        if self.dry_run:
            log.info("camera: DRY RUN (no capture; vision uses ROBOT_FAKE_VISION or 'not visible')")
            return
        try:
            import time
            from picamera2 import Picamera2
            self._picam = Picamera2()
            self._picam.configure(self._picam.create_still_configuration(main={"size": (1280, 720)}))
            self._picam.start()
            time.sleep(self.warmup)   # let AE/AWB settle
            self.available = True
            log.info("camera: Picamera2 started (still capture -> %dpx square JPEG)", self.size)
        except Exception as e:
            # "list index out of range" here = libcamera saw NO camera. If one IS connected, confirm
            # `rpicam-hello --list-cameras` detects it (reseat the CSI ribbon; on the Pi 5 the camera
            # board's 15-pin cable needs the 22->15 adapter -- the Arducam kit's "Pi Zero cable" is it).
            log.warning("camera unavailable (%s) -- vision will report no frame "
                        "(if a camera is connected, check `rpicam-hello --list-cameras`)", e)
            self._picam = None
            self.available = False

    def capture_jpeg(self):
        """Capture one frame as preprocessed JPEG bytes, or None if no camera is available."""
        if not self.available or self._picam is None:
            return None
        try:
            buf = BytesIO()
            try:
                # capture_image() returns a correctly-converted PIL.Image -- avoids the
                # capture_array()+Image.fromarray channel-order trap (the default XBGR8888 main
                # stream gets misread as RGBA, swapping colours / pinning a constant plane).
                img = _letterbox_square(self._picam.capture_image("main"), self.size)
                img.save(buf, format="JPEG", quality=80)
            except Exception:
                # Pillow/letterbox unavailable: send the raw still; Ollama resizes it itself.
                buf = BytesIO()
                self._picam.capture_file(buf, format="jpeg")
            data = buf.getvalue()
            log.info("captured frame: %d bytes JPEG", len(data))
            return data
        except Exception as e:
            log.warning("capture failed: %s", e)
            return None

    def close(self):
        if self._picam is not None:
            try:
                self._picam.stop()
            except Exception:
                pass
            self._picam = None
            self.available = False
