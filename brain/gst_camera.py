# brain/gst_camera.py -- Jetson camera backend (OpenCV, USB/V4L2 or CSI/GStreamer-Argus).
#
# Picamera2 is Raspberry-Pi-only, so on the Jetson the camera is captured through OpenCV:
#   * v4l2      -- a USB webcam (cv2.VideoCapture(index)); the simplest first bring-up.
#   * gstreamer -- a CSI camera through NVIDIA's Argus (nvarguscamerasrc) via cv2.CAP_GSTREAMER.
# Exposes the SAME .capture_jpeg()/.close()/.available surface as the Pi's Picamera2 backend, so the rest
# of the vision pipeline is unchanged. Errors are caught internally -> available=False (vision degrades to
# 'no frame'), mirroring picamera2_backend.py.
#
# cv2 is imported LAZILY (inside __init__) so this module imports fine off-Jetson; only the jetson board's
# make_camera(), on an actual Jetson, ever constructs one. The venv must be --system-site-packages so it
# sees JetPack's system OpenCV + GStreamer plugins.

import logging
import os
from io import BytesIO

from camera import _letterbox_square   # shared letterbox/JPEG helper stays in camera.py

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
            log.info("camera: OpenCV %s backend started (still capture -> %dpx square JPEG)",
                     self.backend, self.size)
        except Exception as e:
            # No camera / no cv2 / bad pipeline: degrade to 'no frame' rather than crash the brain.
            log.warning("camera unavailable (%s) -- vision will report no frame (USB: `v4l2-ctl "
                        "--list-devices`; CSI: test `gst-launch-1.0 nvarguscamerasrc ! nvvidconv ! "
                        "fakesink`)", e)
            self.available = False

    def _gst(self, sensor_id=0):
        return (f"nvarguscamerasrc sensor-id={sensor_id} ! "
                f"video/x-raw(memory:NVMM),width={_CSI_W},height={_CSI_H},framerate=30/1 ! "
                f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
                f"video/x-raw,format=BGR ! appsink drop=1 max-buffers=1")

    def capture_jpeg(self):
        """Capture one frame as preprocessed JPEG bytes, or None if no camera is available."""
        if not self.available or self._cap is None:
            return None
        try:
            frame = None
            for _ in range(5):                   # discard a few frames so AE/AWB settle, keep the last good
                ok, f = self._cap.read()
                if ok and f is not None:
                    frame = f
            if frame is None:
                log.warning("capture failed: no frame from the camera")
                return None
            from PIL import Image
            rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
            img = _letterbox_square(Image.fromarray(rgb), self.size)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            data = buf.getvalue()
            log.info("captured frame: %d bytes JPEG", len(data))
            return data
        except Exception as e:
            log.warning("capture failed: %s", e)
            return None

    def close(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
            self.available = False
