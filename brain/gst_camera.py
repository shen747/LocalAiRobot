# brain/gst_camera.py -- OpenCV camera backend (USB/V4L2 or CSI/GStreamer-Argus).
#
# Two capture paths, both through OpenCV:
#   * v4l2      -- a USB webcam (cv2.VideoCapture(index)); the simplest bring-up. Board-neutral, so BOTH the
#                  Jetson AND the Raspberry Pi use this for a USB webcam (Pi: ROBOT_CAMERA_BACKEND=v4l2 --
#                  the Pi's CSI default stays Picamera2, see brain/boards/pi5.py / picamera2_backend.py).
#   * gstreamer -- a CSI camera through NVIDIA's Argus (nvarguscamerasrc) via cv2.CAP_GSTREAMER (Jetson-only).
# Exposes the SAME .capture_jpeg()/.close()/.available surface as the Pi's Picamera2 backend, so the rest
# of the vision pipeline is unchanged. Errors are caught internally -> available=False (vision degrades to
# 'no frame'), mirroring picamera2_backend.py.
#
# cv2 is imported LAZILY (inside __init__) so this module imports fine when OpenCV is absent (e.g. a Pi CSI
# build that never installs it); only make_camera() constructing a GstCamera pulls it in. On the Jetson the
# venv must be --system-site-packages so it sees JetPack's system OpenCV + GStreamer plugins; on the Pi the
# USB path needs `pip install opencv-python` (or apt python3-opencv).

import logging
import os
import threading
from io import BytesIO

from PIL import Image                   # this module is imported LAZILY (only on a live camera build), where
from camera import _letterbox_square    # Pillow is required anyway -- so the import is safe at module scope

log = logging.getLogger("robot.cam")

# Capture geometry for the CSI/Argus pipeline (downscaled off the sensor by nvvidconv before it reaches us).
def _envi(key, default):
    try:
        return int(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return int(default)


_CSI_W = _envi("ROBOT_CSI_WIDTH", 1280)
_CSI_H = _envi("ROBOT_CSI_HEIGHT", 720)


class GstCamera:
    """Single-frame still capture on the Jetson via OpenCV. .capture_jpeg() returns JPEG bytes or None."""

    def __init__(self, size, backend):
        self.size = size
        self.backend = backend or "gstreamer"
        self._cv2 = None
        self._cap = None
        self.available = False
        # Single-flight capture watchdog state -- set BEFORE the try so close()/capture_jpeg are safe
        # even if construction below fails (no camera).
        self._cap_lock = threading.Lock()
        self._cap_worker = None
        try:
            self._cap_timeout = float(os.environ.get("ROBOT_CAMERA_CAPTURE_TIMEOUT_S", "12") or 12)
        except (TypeError, ValueError):
            self._cap_timeout = 12.0
        try:
            import cv2
            self._cv2 = cv2
            src = os.environ.get("ROBOT_CAMERA_SOURCE", "").strip()
            if self.backend == "v4l2":
                # digit -> USB webcam index; a /dev/videoN path is used verbatim (v4l2-ctl --list-devices
                # emits paths, and once the CSI driver claims /dev/video0 the USB cam is no longer index 0).
                cap = cv2.VideoCapture(int(src) if src.isdigit() else (src or 0))
            else:                                                          # CSI camera via Argus
                # A bare digit (e.g. a leftover ROBOT_CAMERA_SOURCE=0 from the USB bring-up step) is a SENSOR
                # id, not a pipeline -- build the Argus pipeline for it. Only a non-numeric value is treated
                # as a full GStreamer pipeline string; empty -> the default CSI sensor 0 pipeline.
                pipeline = self._gst(int(src)) if src.isdigit() else (src or self._gst())
                cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                raise RuntimeError("cv2.VideoCapture could not open the camera "
                                   f"(backend={self.backend}, source={src or 'CSI sensor 0'})")
            self._cap = cap
            self.available = True
            log.info("camera: OpenCV %s backend started (still capture -> %dpx square JPEG)%s",
                     self.backend, self.size, self._apply_autofocus())
        except Exception as e:
            # No camera / no cv2 / bad pipeline: degrade to 'no frame' rather than crash the brain.
            log.warning("camera unavailable (%s) -- vision will report no frame (USB: `v4l2-ctl "
                        "--list-devices`; CSI: test `gst-launch-1.0 nvarguscamerasrc ! nvvidconv ! "
                        "fakesink`)", e)
            self.available = False

    def _apply_autofocus(self):
        """Apply ROBOT_CAMERA_AUTOFOCUS to a UVC webcam and return a short suffix for the startup line.

        WHY THIS EXISTS: a UVC webcam's autofocus lives in the CAMERA's OWN FIRMWARE, and this backend used to
        never touch it -- so `ROBOT_CAMERA_AUTOFOCUS` silently did NOTHING on a USB webcam (it was only ever
        wired up in the Picamera2/CSI backend). That left focus in whatever state the firmware defaulted to,
        usually CONTINUOUS AF, which can hunt mid-burst and hand the face matcher soft frames -- with nothing
        in the log to say so. Now the key means the same thing on both backends, and the startup line reports
        what the camera ACTUALLY came back with (cv2's AUTOFOCUS property is optional in UVC, so a set() can
        legitimately fail -- we say so rather than pretend).

        Only meaningful for v4l2; the Argus/CSI pipeline has no such control, so it's skipped there."""
        if self.backend != "v4l2" or self._cap is None:
            return ""
        want_af = os.environ.get("ROBOT_CAMERA_AUTOFOCUS", "1") == "1"
        cv2 = self._cv2
        try:
            ok = bool(self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if want_af else 0))
            state = self._cap.get(cv2.CAP_PROP_AUTOFOCUS)
        except Exception as e:
            log.info("camera: could not set autofocus on this webcam (%s) -- leaving the firmware default", e)
            return ", autofocus=firmware default"
        if not ok:
            # Very common: plenty of UVC cameras expose no software focus control at all. Not a failure --
            # the firmware still does whatever it does; we just can't steer it. Say which, so 'soft frames'
            # can be investigated with v4l2-ctl instead of being blamed on this code.
            log.info("camera: this webcam does not accept a software autofocus setting -- leaving the firmware "
                     "default. Inspect/force it with:  v4l2-ctl -d %s --list-ctrls  (look for "
                     "focus_automatic_continuous / focus_absolute)", os.environ.get("ROBOT_CAMERA_SOURCE", "0"))
            return ", autofocus=firmware default"
        return f", autofocus={'ON' if want_af else 'OFF'} (camera reports {state:g})"

    def _gst(self, sensor_id=0):
        return (f"nvarguscamerasrc sensor-id={sensor_id} ! "
                f"video/x-raw(memory:NVMM),width={_CSI_W},height={_CSI_H},framerate=30/1 ! "
                f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
                f"video/x-raw,format=BGR ! appsink drop=1 max-buffers=1")

    def capture_jpeg(self):
        """Capture one frame as preprocessed JPEG bytes, or None if no camera is available. The blocking
        cv2.read() runs in a single-flight daemon worker joined with a deadline, so a wedged USB/V4L2 read
        can never freeze the explore/command loop (only one worker ever touches the non-thread-safe handle)."""
        if not self.available or self._cap is None:
            return None
        result = {"frame": None}
        with self._cap_lock:
            if self._cap_worker is not None and self._cap_worker.is_alive():
                log.warning("capture still in-flight from a prior timeout -- skipping this frame")
                return None
            cap = self._cap                      # local ref: worker never touches a nulled attribute
            def _grab():
                try:
                    frame = None
                    for _ in range(5):           # discard a few frames so AE/AWB settle, keep the last good
                        ok, f = cap.read()
                        if ok and f is not None:
                            frame = f
                    result["frame"] = frame
                except Exception as e:
                    log.warning("capture worker failed: %s", e)
            worker = threading.Thread(target=_grab, daemon=True, name="cam-grab")
            self._cap_worker = worker
            worker.start()
        worker.join(self._cap_timeout)           # join the LOCAL ref -- a concurrent caller may have
        if worker.is_alive():                    # replaced self._cap_worker by now
            log.warning("capture timed out after %.0fs -- returning no frame", self._cap_timeout)
            return None
        frame = result["frame"]
        if frame is None:
            log.warning("capture failed: no frame from the camera")
            return None
        try:
            rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
            img = _letterbox_square(Image.fromarray(rgb), self.size)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            data = buf.getvalue()
            # DEBUG, not INFO -- see the note in brain/picamera2_backend.py: the Concierge recognition burst
            # made this single line ~144MB of a four-day log. --verbose brings it back.
            log.debug("captured frame: %d bytes JPEG", len(data))
            return data
        except Exception as e:
            log.warning("capture failed: %s", e)
            return None

    def close(self):
        self.available = False
        with self._cap_lock:                     # can't interleave with a new worker spawn
            w = self._cap_worker
            if w is not None and w.is_alive():
                w.join(timeout=2.0)              # grace for a normal in-flight capture
            if self._cap is not None:
                if w is not None and w.is_alive():
                    # Worker still wedged inside cv2 read(); releasing the handle under it can double-free
                    # in native code. Leak it -- the daemon worker + process exit clean up.
                    log.warning("camera close: capture worker still wedged -- leaking the OpenCV handle")
                else:
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                self._cap = None
