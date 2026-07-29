# brain/picamera2_backend.py -- the Raspberry Pi camera backend (Picamera2 / libcamera).
#
# Extracted verbatim from camera.py so the board HAL can select it on the Pi (see brain/boards/pi5.py) and
# a different backend (GStreamer/V4L2) on the Jetson -- both exposing the same .capture_jpeg()/.close()
# surface. Behaviour on the Pi is unchanged.
#
# Works with ANY libcamera-supported camera. The build uses an Arducam IMX519 16MP AUTOFOCUS module; a
# fixed-focus OV5647 (Pi Camera v1) or IMX219 (v2) also works with no code change. On an autofocus sensor
# each capture runs a one-shot focus first; fixed-focus sensors are left untouched. picamera2 is imported
# LAZILY so this module imports fine on a laptop / Jetson.
#
# Install on the Pi (NOT pip):  sudo apt install -y python3-picamera2 --no-install-recommends
# and create the venv with --system-site-packages so it can see libcamera (see install_stack_pi5.sh).

import logging
import os
import threading
from io import BytesIO

from camera import _letterbox_square   # shared letterbox/JPEG helper stays in camera.py

log = logging.getLogger("robot.cam")


class PiCamera2Camera:
    """Single-frame still capture via Picamera2. .capture_jpeg() returns JPEG bytes or None."""

    def __init__(self, size):
        self.size = size
        # Single-flight capture watchdog state -- set FIRST so close()/capture_jpeg are safe even if
        # construction below fails (no camera).
        self._cap_lock = threading.Lock()
        self._cap_worker = None
        try:
            self._cap_timeout = float(os.environ.get("ROBOT_CAMERA_CAPTURE_TIMEOUT_S", "12") or 12)
        except (TypeError, ValueError):
            self._cap_timeout = 12.0
        try:
            self.warmup = float(os.environ.get("ROBOT_CAMERA_WARMUP_S", "1.0") or 1.0)
        except ValueError:
            self.warmup = 1.0
        self.autofocus = os.environ.get("ROBOT_CAMERA_AUTOFOCUS", "1") == "1"
        self._picam = None
        self._has_af = False   # set true below iff the sensor exposes AfMode (IMX519 yes; OV5647/IMX219 no)
        self.available = False

        try:
            import time
            from picamera2 import Picamera2
            self._picam = Picamera2()
            self._picam.configure(self._picam.create_still_configuration(main={"size": (1280, 720)}))
            self._picam.start()
            time.sleep(self.warmup)   # let AE/AWB settle
            self.available = True
            # Autofocus: only AF sensors (e.g. IMX519) expose an 'AfMode' control. Put it in Auto so
            # capture_jpeg() can fire a one-shot focus per frame. Fixed-focus sensors have no AfMode -> skip.
            if self.autofocus:
                try:
                    if "AfMode" in self._picam.camera_controls:
                        from libcamera import controls as _ctrls
                        self._picam.set_controls({"AfMode": _ctrls.AfModeEnum.Auto})
                        self._has_af = True
                except Exception as e:
                    log.info("camera: autofocus not enabled (%s)", e)
            log.info("camera: Picamera2 started (still capture -> %dpx square JPEG%s)",
                     self.size, ", autofocus" if self._has_af else "")
        except Exception as e:
            # "list index out of range" here = libcamera saw NO camera. If one IS connected, confirm
            # `rpicam-hello --list-cameras` detects it (reseat the CSI ribbon; on the Pi 5 the camera
            # board's 15-pin cable needs the 22->15 adapter -- the Arducam kit's "Pi Zero cable" is it).
            log.warning("camera unavailable (%s) -- vision will report no frame "
                        "(if a camera is connected, check `rpicam-hello --list-cameras`)", e)
            self._picam = None
            self.available = False

    def capture_jpeg(self):
        """Capture one frame as preprocessed JPEG bytes, or None if no camera is available. The whole
        blocking AF+capture+fallback block runs in a single-flight daemon worker joined with a deadline, so
        a wedged libcamera autofocus_cycle()/capture_image() can never freeze the explore/command loop."""
        if not self.available or self._picam is None:
            return None
        result = {"data": None}
        with self._cap_lock:
            if self._cap_worker is not None and self._cap_worker.is_alive():
                log.warning("capture still in-flight from a prior timeout -- skipping this frame")
                return None
            picam = self._picam                  # local ref: worker never touches a nulled attribute
            def _grab():
                try:
                    if self._has_af:
                        try:
                            picam.autofocus_cycle()   # one-shot AF scan; blocks until the lens locks (~0.3-1s)
                        except Exception as e:
                            log.debug("autofocus cycle failed (%s) -- capturing anyway", e)
                    buf = BytesIO()
                    try:
                        # capture_image() returns a correctly-converted PIL.Image -- avoids the
                        # capture_array()+Image.fromarray channel-order trap (the default XBGR8888 main
                        # stream gets misread as RGBA, swapping colours / pinning a constant plane).
                        img = _letterbox_square(picam.capture_image("main"), self.size)
                        img.save(buf, format="JPEG", quality=80)
                    except Exception:
                        # Pillow/letterbox unavailable: send the raw still; Ollama resizes it itself.
                        buf = BytesIO()
                        picam.capture_file(buf, format="jpeg")
                    result["data"] = buf.getvalue()
                except Exception as e:
                    log.warning("capture worker failed: %s", e)
            worker = threading.Thread(target=_grab, daemon=True, name="cam-grab")
            self._cap_worker = worker
            worker.start()
        worker.join(self._cap_timeout)           # join the LOCAL ref (see gst_camera note)
        if worker.is_alive():
            log.warning("capture timed out after %.0fs -- returning no frame", self._cap_timeout)
            return None
        data = result["data"]
        if not data:
            log.warning("capture failed: no frame from the camera")
            return None
        # DEBUG, not INFO: the Concierge recognition window grabs a BURST of stills per motion wake, so at INFO
        # this one line was ~500,000 entries (~144MB) in a four-day run log -- drowning every event that
        # mattered. Run with --verbose (ROBOT_LOG_LEVEL=DEBUG) when you want per-frame capture confirmation.
        log.debug("captured frame: %d bytes JPEG", len(data))
        return data

    def close(self):
        self.available = False
        with self._cap_lock:
            w = self._cap_worker
            if w is not None and w.is_alive():
                w.join(timeout=2.0)
            if self._picam is not None:
                if w is not None and w.is_alive():
                    # Worker wedged inside libcamera; stop() from another thread can deadlock/crash.
                    # Leave Picamera2 running -- the daemon worker + process exit clean up.
                    log.warning("camera close: capture worker still wedged -- skipping Picamera2 stop()")
                else:
                    try:
                        self._picam.stop()
                    except Exception:
                        pass
                self._picam = None
