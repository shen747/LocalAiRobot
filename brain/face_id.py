# brain/face_id.py -- STRICTLY LOCAL face recognition for Concierge Mode.
#
# Pipeline (all on-device, no cloud): a webcam JPEG -> detect the largest face (OpenCV Haar cascade) -> embed
# it with a local ArcFace ONNX model (via onnxruntime, the SAME runtime openWakeWord already uses) -> compare
# (cosine) against the enrolled faces in the RAG db (memory.faces). A detected face IS the "is a person
# present?" signal, so no cloud VLM is involved. Everything degrades GRACEFULLY: if OpenCV / onnxruntime /
# numpy or the model file is missing, ensure_loaded() returns False (and .available stays False) and
# identify() returns (None, 0.0) -> the concierge simply smiles and stays silent, as the "no match" branch
# requires. NOTE: .available is a CHEAP flag that never triggers the load; call ensure_loaded() to load.
#
# Enrol faces with:  ./scripts/enroll_face.sh "Ramesh"   (captures from the webcam and stores the embedding).
# Accuracy note: Haar gives a bounding box, not 5-point landmarks, so faces aren't affine-aligned before
# embedding -- fine for greeting a handful of known desk visitors; raise/lower ROBOT_FACE_ID_THRESHOLD to
# taste. All heavy imports are LAZY so importing this module is safe on any machine.

import logging
import os

log = logging.getLogger("robot.faceid")

_DEFAULT_MODEL = "~/.localairobot/models/arcface.onnx"   # written by scripts/provision_face_id.sh (InsightFace w600k)


def _envf(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


class FaceID:
    """Local face detect + embed + match. Construct once with the Memory instance; reused per motion wake."""

    def __init__(self, mem):
        self.mem = mem
        self.model_path = os.path.expanduser(os.environ.get("ROBOT_FACE_ID_MODEL", "") or _DEFAULT_MODEL)
        self.threshold = _envf("ROBOT_FACE_ID_THRESHOLD", 0.38)   # cosine similarity; higher = stricter
        self.input_size = int(_envf("ROBOT_FACE_ID_INPUT", 112))
        self._sess = None            # onnxruntime session (lazy)
        self._cascade = None         # cv2 Haar detector (lazy)
        self._np = None              # numpy (lazy)
        self._ready = None           # None = not tried yet; True/False after _load()
        self._warned = False

    # -- setup -------------------------------------------------------------------------------------------
    def _load(self):
        """Lazily import cv2/numpy/onnxruntime and open the model + detector. Returns True on success; on any
        failure logs ONCE and returns False so the caller falls back to the silent 'no match' path."""
        if self._ready is not None:
            return self._ready
        try:
            import cv2
            import numpy as np
            import onnxruntime as ort
            if not os.path.isfile(self.model_path):
                raise FileNotFoundError(
                    f"face model not found: {self.model_path} -- run ./scripts/patches/common/"
                    "5_face_id_insightface.sh to download it (or set ROBOT_FACE_ID_MODEL)")
            self._np = np
            self._cv2 = cv2
            cascade_path = os.environ.get("ROBOT_FACE_ID_DETECTOR", "") or \
                os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            self._cascade = cv2.CascadeClassifier(cascade_path)
            if self._cascade.empty():
                raise RuntimeError(f"could not load face detector cascade: {cascade_path}")
            self._sess = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            self._inp = self._sess.get_inputs()[0].name
            self._ready = True
            log.info("face_id: ready (model=%s, detector=Haar, threshold=%.2f)",
                     os.path.basename(self.model_path), self.threshold)
        except Exception as e:
            if not self._warned:
                log.warning("face_id disabled (%s) -- concierge will smile silently instead of greeting by "
                            "name. Enrol + install: ./scripts/enroll_face.sh", e)
                self._warned = True
            self._ready = False
        return self._ready

    def ensure_loaded(self):
        """Explicitly perform the (multi-second, once-only) lazy load. Returns True when usable.
        Call this at an intended warmup point; `available` below stays a cheap flag that never loads."""
        return self._load()

    @property
    def available(self):
        """Cheap readiness flag; NEVER loads. False until ensure_loaded()/embed()/identify() ran _load()."""
        return bool(self._ready)

    # -- core --------------------------------------------------------------------------------------------
    def _largest_face(self, bgr):
        """Return a preprocessed NCHW float32 blob for the LARGEST detected face, or None if no face."""
        cv2, np = self._cv2, self._np
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])   # biggest box = closest/primary person
        m = int(0.15 * max(w, h))                            # small margin so the crop isn't too tight
        y0, y1 = max(0, y - m), min(bgr.shape[0], y + h + m)
        x0, x1 = max(0, x - m), min(bgr.shape[1], x + w + m)
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, (self.input_size, self.input_size))
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 127.5                          # ArcFace normalisation -> [-1, 1]
        return np.transpose(rgb, (2, 0, 1))[np.newaxis, :]   # NCHW, batch 1

    def embed(self, jpeg_bytes):
        """JPEG bytes -> L2-normalised face embedding (np.float32), or None (no libs / no face / error)."""
        if not self._load() or not jpeg_bytes:
            return None
        cv2, np = self._cv2, self._np
        try:
            bgr = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                return None
            blob = self._largest_face(bgr)
            if blob is None:
                return None
            vec = self._sess.run(None, {self._inp: blob})[0][0].astype(np.float32)
            n = float(np.linalg.norm(vec))
            return vec / n if n > 0 else None
        except Exception as e:
            log.debug("face_id embed failed: %s", e)
            return None

    def identify(self, jpeg_bytes):
        """JPEG bytes -> (name, score) of the best enrolled match at/above the threshold, else (None, score).
        (None, 0.0) means: no face / not set up -> caller smiles silently. Never raises."""
        vec = self.embed(jpeg_bytes)
        if vec is None:
            return None, 0.0
        np = self._np
        best_name, best = None, -1.0
        for name, emb_bytes in self.mem.all_faces():
            other = np.frombuffer(emb_bytes, dtype=np.float32)
            if other.shape != vec.shape:
                continue
            score = float(np.dot(vec, other))   # both L2-normalised -> dot == cosine similarity
            if score > best:
                best_name, best = name, score
        if best_name is not None and best >= self.threshold:
            log.info("face_id: matched %r (cosine %.3f >= %.2f)", best_name, best, self.threshold)
            return best_name, best
        log.info("face_id: no match (best cosine %.3f < %.2f)", max(best, 0.0), self.threshold)
        return None, max(best, 0.0)

    def enroll(self, jpeg_bytes, name):
        """Store a face embedding under `name`. Returns True if a face was found + stored, else False."""
        vec = self.embed(jpeg_bytes)
        if vec is None:
            return False
        self.mem.add_face(name.strip(), vec.tobytes())
        log.info("face_id: enrolled %r", name)
        return True
