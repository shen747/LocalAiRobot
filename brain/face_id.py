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
# Enrol faces with:  ./scripts/enroll_person.sh "Ramesh" "my creator" "Ruh-mesh" photo.jpg  (files and/or --camera).
# Accuracy note: Haar gives a bounding box, not 5-point landmarks, so faces aren't affine-aligned before
# embedding -- fine for greeting a handful of known visitors; raise/lower ROBOT_FACE_ID_THRESHOLD to
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
        # How to combine a person's MULTIPLE enrolled photos when matching (see identify()). Default 'max' is
        # exactly the old best-single-view behaviour, so single-photo enrolments are completely unaffected.
        self._aggregate = (os.environ.get("ROBOT_FACE_ID_AGGREGATE", "") or "max").strip().lower()
        if self._aggregate not in ("max", "mean", "topk", "centroid"):
            log.warning("face_id: unknown ROBOT_FACE_ID_AGGREGATE=%r -- using 'max'", self._aggregate)
            self._aggregate = "max"
        self._topk = max(1, int(_envf("ROBOT_FACE_ID_TOPK", 3)))
        # Haar detector strictness. These are the knobs that trade GHOST greetings against MISSED ones, so they
        # are configurable rather than buried: minNeighbors is how many overlapping detections a box needs to
        # be believed (raise it -- 6..8 -- when scenery like a wall texture or picture frame keeps registering
        # as a face), and min_face is the smallest box in pixels (raise it to ignore faces too far away to
        # recognise anyway; the frame is ROBOT_CAMERA_SIZE px square, default 512).
        self.min_neighbors = max(1, int(_envf("ROBOT_FACE_ID_MIN_NEIGHBORS", 5)))
        self.min_face = max(16, int(_envf("ROBOT_FACE_ID_MIN_FACE_PX", 60)))
        # Diagnostics: the largest face box (px) seen by the LAST detect()/embed() call, or 0 for none. Logged
        # on a no-match so you can tell a real-but-unrecognised visitor from a tiny Haar fluke on scenery.
        self.last_face_px = 0
        # Top-3 [(name, score)] from the LAST identify(), best first. The runner-up's score is what turns a
        # bare "matched 0.398" into something you can act on -- see the ranking note in identify().
        self.last_ranked = []
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
                            "name. Enrol + install: ./scripts/enroll_person.sh", e)
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
    def _boxes(self, bgr):
        """Haar-detect faces and return the boxes, recording the largest one's size for diagnostics. Shared by
        detect() and _largest_face() so both use the SAME strictness settings (they used to hard-code them
        separately, which meant tuning one silently left the other alone)."""
        cv2 = self._cv2
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=self.min_neighbors,
                                               minSize=(self.min_face, self.min_face))
        self.last_face_px = max((max(f[2], f[3]) for f in faces), default=0)
        return faces

    def _largest_face(self, bgr):
        """Return a preprocessed NCHW float32 blob for the LARGEST detected face, or None if no face."""
        cv2, np = self._cv2, self._np
        faces = self._boxes(bgr)
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

    def detect(self, jpeg_bytes):
        """True if a HUMAN FACE is PRESENT in the frame -- Haar detection ONLY, recognition NOT required, so an
        un-enrolled stranger (a trick-or-treater) counts. This is Halloween Mode's 'someone is at the door'
        signal (vs identify(), which only fires for enrolled people). Cheaper than embed() (no ArcFace pass).
        100% local (OpenCV Haar); never raises. Returns False if cv2/model aren't set up, or no face."""
        if not self._load() or not jpeg_bytes:
            return False
        cv2, np = self._cv2, self._np
        try:
            bgr = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                return False
            return len(self._boxes(bgr)) > 0
        except Exception as e:
            log.debug("face_id detect failed: %s", e)
            return False

    def _combine(self, person_sims, person_embs, vec):
        """Reduce ONE person's per-photo cosine sims to a single score, per ROBOT_FACE_ID_AGGREGATE.
        With a single enrolled photo every strategy returns that one sim (so single-photo is unchanged)."""
        np = self._np
        if self._aggregate == "mean":
            return float(sum(person_sims) / len(person_sims))
        if self._aggregate == "topk":
            top = sorted(person_sims, reverse=True)[:self._topk]
            return float(sum(top) / len(top))
        if self._aggregate == "centroid" and person_embs:
            proto = np.mean(np.stack(person_embs), axis=0)   # average the enrolled embeddings -> one prototype
            nrm = float(np.linalg.norm(proto))
            if nrm > 0:
                return float(np.dot(vec, proto / nrm))        # cosine vs the (re-normalised) prototype
        return max(person_sims)                               # 'max' (default): best-matching enrolled photo wins

    def identify(self, jpeg_bytes):
        """JPEG bytes -> (name, score) of the best enrolled match at/above the threshold, else (None, score).
        (None, 0.0) means: no face / not set up -> caller smiles silently. Never raises.

        A person may have SEVERAL enrolled photos (one embedding row each); they're combined PER PERSON by
        ROBOT_FACE_ID_AGGREGATE so extra images sharpen recognition instead of just adding rows:
          max      best-matching enrolled photo wins   (default; most tolerant of pose/light, best recall)
          topk     mean of the person's top-K photos    (K=ROBOT_FACE_ID_TOPK; robust middle ground)
          mean     average over ALL their photos        (strictest; the visitor must resemble every shot)
          centroid cosine vs their averaged 'prototype' (smooths per-photo noise into one template)
        """
        vec = self.embed(jpeg_bytes)
        if vec is None:
            return None, 0.0
        np = self._np
        # Group every enrolled row by person: their per-photo cosine sims (+ raw embeddings for 'centroid').
        sims, embs = {}, {}
        for name, emb_bytes in self.mem.all_faces():
            other = np.frombuffer(emb_bytes, dtype=np.float32)
            if other.shape != vec.shape:
                continue
            sims.setdefault(name, []).append(float(np.dot(vec, other)))   # both L2-normalised -> dot == cosine
            if self._aggregate == "centroid":
                embs.setdefault(name, []).append(other)
        if not sims:
            return None, 0.0
        # RANK everyone, don't just track the winner. The runner-up's score is the number that says whether a
        # match was DECISIVE or a coin flip: 'matched Ramesh 0.398' told us nothing about whether Shanika
        # scored 0.11 (safe) or 0.39 (one bad frame from greeting the wrong person by name). Without the
        # margin there is no way to tune ROBOT_FACE_ID_THRESHOLD from a run log, or to notice that two
        # enrolled people have become confusable. Cheap: the per-person scores are already computed.
        ranked = sorted(((self._combine(person_sims, embs.get(name), vec), name)
                         for name, person_sims in sims.items()), reverse=True)
        self.last_ranked = [(n, s) for s, n in ranked[:3]]   # diagnostics for callers
        best, best_name = ranked[0]
        runner = f", runner-up {ranked[1][1]!r} {ranked[1][0]:.3f} (margin {best - ranked[1][0]:+.3f})" \
                 if len(ranked) > 1 else " (only one person enrolled)"
        if best_name is not None and best >= self.threshold:
            log.info("face_id: matched %r (%s %.3f >= %.2f over %d photo(s); face box %dpx)%s",
                     best_name, self._aggregate, best, self.threshold, len(sims[best_name]),
                     self.last_face_px, runner)
            return best_name, best
        # The face box size is in the message on purpose. A no-match with a BIG box is a real person the
        # enrolments don't cover well (re-enrol them, or lower ROBOT_FACE_ID_THRESHOLD); a no-match with a box
        # barely over ROBOT_FACE_ID_MIN_FACE_PX is usually Haar firing on scenery, or a visitor too far away to
        # recognise -- raise ROBOT_FACE_ID_MIN_NEIGHBORS / _MIN_FACE_PX for that. Without the size the two look
        # identical in the log, and they need opposite fixes.
        # Name the CLOSEST person even though we're rejecting them: "closest Ramesh 0.31" means he was nearly
        # recognised (widen his enrolment, or lower the threshold), while a spread of low scores means the crop
        # isn't a real face at all. The old line said only "best 0.31" -- the same number for both cases.
        log.info("face_id: no match (closest %r %s %.3f < %.2f; face box %dpx)%s",
                 best_name, self._aggregate, max(best, 0.0), self.threshold, self.last_face_px, runner)
        return None, max(best, 0.0)

    def enroll(self, jpeg_bytes, name):
        """Store a face embedding under `name`. Returns True if a face was found + stored, else False."""
        vec = self.embed(jpeg_bytes)
        if vec is None:
            return False
        self.mem.add_face(name.strip(), vec.tobytes())
        log.info("face_id: enrolled %r", name)
        return True
