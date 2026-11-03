# brain/face_id.py -- STRICTLY LOCAL face recognition for Concierge Mode.
#
# Pipeline (all on-device, no cloud): a webcam JPEG -> detect the largest face -> ALIGN it -> embed it with a
# local ArcFace ONNX model (via onnxruntime, the SAME runtime openWakeWord already uses) -> compare (cosine)
# against the enrolled faces in the RAG db (memory.faces). A detected face IS the "is a person present?"
# signal, so no cloud VLM is involved. Everything degrades GRACEFULLY: if OpenCV / onnxruntime / numpy or the
# model file is missing, ensure_loaded() returns False (and .available stays False) and identify() returns
# (None, 0.0, False) -> the concierge simply smiles and stays silent, as the "no match" branch requires.
# NOTE: .available is a CHEAP flag that never triggers the load; call ensure_loaded() to load.
#
# Enrol faces with:  ./scripts/enroll_person.sh "Ramesh" "my creator" "Ruh-mesh" photo.jpg  (files and/or --camera).
# All heavy imports are LAZY so importing this module is safe on any machine.
#
# WHY ALIGNMENT IS THE WHOLE BALLGAME (and why this file no longer just crops a Haar box).
# ArcFace/w600k -- the model scripts/provision_face_id.sh downloads out of the InsightFace buffalo pack -- is
# trained EXCLUSIVELY on 112x112 crops that have been affine-warped so the eyes, nose tip and mouth corners
# land on fixed canonical pixels. Feed it a raw detector box instead and the input is outside the
# distribution it learned: the embeddings it returns are near-arbitrary, so every cosine similarity collapses
# toward the same small number and identity information is simply gone.
#
# That is not theory, it is what a 19-hour field log showed. Of 257 frames that genuinely contained a face,
# exactly 6 matched anybody -- 2.3%. The failures all looked like this:
#     no match (closest 'Person A' 0.171 < 0.38), runner-up 'Person B' 0.168 (margin +0.003)
#     no match (closest 'Person B' 0.137 < 0.38), runner-up 'Person C' 0.136 (margin +0.001)
# Every score squashed into 0.13-0.21, every margin ~0.00, and the "closest" name spread evenly across all 35
# enrolled people (Person C 37 times, Person D 34, Person E 33, Person B 28...) -- a lottery, not a measurement.
# One person stood squarely in front of the camera for 14 consecutive frames and scored 0.142.
#
# So detection now prefers YuNet (cv2.FaceDetectorYN, an ONNX detector OpenCV bundles the API for), which
# returns the 5 landmarks ArcFace needs, and _align() warps them onto the canonical template before
# embedding. Haar stays as an automatic fallback for a robot that never got the YuNet model -- it keeps the
# OLD (unaligned, poor) accuracy rather than breaking, and the startup line says which one you are on.
#
# ==> RE-ENROL AFTER TURNING ALIGNMENT ON. Stored embeddings were computed by this same code path, so an
#     enrolment made before alignment is itself unaligned. Aligning only the QUERY side compares apples to
#     noise and is WORSE than not aligning at all. On the Pi:  bash enroll_all.sh   (file-based, idempotent,
#     safe while the robot is live), then check with ./scripts/rag.sh --faces.

import logging
import os

log = logging.getLogger("robot.faceid")

_DEFAULT_MODEL = "~/.localairobot/models/arcface.onnx"   # written by scripts/provision_face_id.sh (InsightFace w600k)
_DEFAULT_YUNET = "~/.localairobot/models/face_detection_yunet.onnx"   # ditto (OpenCV Zoo YuNet)

# The canonical ArcFace 5-point template: where the right eye, left eye, nose tip, right mouth corner and
# left mouth corner must land in a 112x112 crop. These are InsightFace's `arcface_dst` constants, and they
# are what w600k was trained against -- they are NOT tunable, and changing them silently degrades matching.
# Order matches cv2.FaceDetectorYN's landmark order exactly (see _boxes_yunet).
_ARCFACE_DST_112 = (
    (38.2946, 51.6963),    # right eye
    (73.5318, 51.5014),    # left eye
    (56.0252, 71.7366),    # nose tip
    (41.5493, 92.3655),    # right mouth corner
    (70.7299, 92.2041),    # left mouth corner
)


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
        # How far clear of the RUNNER-UP the winner must be before we'll say a name out loud. Passing the
        # threshold says "this looks like an enrolled person"; the margin says "and it's clearly THIS one".
        # With 35 people enrolled those are different questions, and being called someone else's name is a
        # worse failure than being greeted without one -- so a photo finish falls through to the generic
        # greeting instead of guessing. 0 disables the check (threshold only, the old behaviour).
        self.min_margin = max(0.0, _envf("ROBOT_FACE_ID_MIN_MARGIN", 0.08))
        # Haar detector strictness. These are the knobs that trade GHOST greetings against MISSED ones, so they
        # are configurable rather than buried: minNeighbors is how many overlapping detections a box needs to
        # be believed (raise it -- 6..8 -- when scenery like a wall texture or picture frame keeps registering
        # as a face), and min_face is the smallest box in pixels (raise it to ignore faces too far away to
        # recognise anyway; the frame is ROBOT_CAMERA_SIZE px square, default 512).
        self.min_neighbors = max(1, int(_envf("ROBOT_FACE_ID_MIN_NEIGHBORS", 5)))
        self.min_face = max(16, int(_envf("ROBOT_FACE_ID_MIN_FACE_PX", 60)))
        # ENROLMENT uses a LOWER floor than live detection, and deliberately so. The live floor exists to
        # throw away distant passers-by and small spurious boxes on scenery -- decisions made 20 times a
        # second with nobody watching. An enrolment is the opposite: a human deliberately chose that photo,
        # once, offline. Judging it by the live floor silently drops people -- two of this repo's 73
        # enrolment photos have ~53px faces and were being discarded without a word.
        self.min_face_enroll = max(16, int(_envf("ROBOT_FACE_ID_MIN_FACE_PX_ENROLL", 40)))
        # Detector choice. 'auto' = YuNet when its model is present, else Haar. Explicit 'yunet' still falls
        # back (with a warning) rather than leaving the concierge blind -- a missing 340KB file must never be
        # the difference between a working robot and a silent one.
        self._detector_pref = (os.environ.get("ROBOT_FACE_ID_DETECTOR_BACKEND", "") or "auto").strip().lower()
        if self._detector_pref not in ("auto", "yunet", "haar"):
            log.warning("face_id: unknown ROBOT_FACE_ID_DETECTOR_BACKEND=%r -- using 'auto'",
                        self._detector_pref)
            self._detector_pref = "auto"
        self.yunet_path = os.path.expanduser(
            os.environ.get("ROBOT_FACE_ID_YUNET_MODEL", "") or _DEFAULT_YUNET)
        self.yunet_score = _envf("ROBOT_FACE_ID_YUNET_SCORE", 0.6)    # detector confidence, 0..1
        self.detector = "none"       # 'yunet' | 'haar' | 'none' -- resolved by _load(), reported at startup
        self.aligned = False         # True once landmark alignment is actually in play
        self._yunet = None
        self._yunet_size = None      # YuNet needs setInputSize() whenever the frame dimensions change
        # Diagnostics: the last frame's 5 landmarks (or None), so a bad alignment is inspectable.
        self._last_landmarks = None
        # Diagnostics: the largest face box (px) seen by the LAST detect()/embed() call, or 0 for none. Logged
        # on a no-match so you can tell a real-but-unrecognised visitor from a tiny Haar fluke on scenery.
        self.last_face_px = 0
        # The largest box the SIZE FLOOR threw away on the last detect, or 0. Separates "nobody was there"
        # from "somebody was there but too far back to recognise" -- two outcomes that otherwise log
        # identically as a silent look. Read by main.py's silent-outcome line.
        self.last_rejected_px = 0
        # How many faces the LAST detect pass accepted, before identify_all's cap. Separate from
        # last_face_px, which describes only the biggest one: "who is in the room" is a COUNT question and
        # a 3-face frame reported as one 180px box cannot answer it.
        self.last_face_count = 0
        # Top-3 [(name, score)] from the LAST identify(), best first. The runner-up's score is what turns a
        # bare "matched 0.398" into something you can act on -- see the ranking note in identify().
        self.last_ranked = []
        # Cost cap for identify_all(): ArcFace runs PER FACE, so a group photo held up to the lens would
        # otherwise turn one question into dozens of inferences on a Pi 5 CPU. Faces are scored nearest
        # first, so the cap drops the people furthest away -- the ones least likely to be who was meant.
        self._max_faces = max(1, int(_envf("ROBOT_IDENTIFY_MAX_FACES", 5)))
        self._sess = None            # onnxruntime session (lazy)
        self._cascade = None         # cv2 Haar detector (lazy)
        self._np = None              # numpy (lazy)
        self._ready = None           # None = not tried yet; True/False after _load()
        self._warned = False
        # Enrolment cache (see _enrollments): the decoded embeddings, valid while mem.faces_version() is
        # unchanged. Sentinel version is deliberately an object no counter can equal, so the first frame
        # always builds.
        self._faces_version = object()
        self._faces_names = []
        self._faces_mat = None
        self._faces_by_name = {}

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
            self._load_detector(cv2)
            if self.detector == "none":
                raise RuntimeError("no usable face detector (neither YuNet nor a Haar cascade loaded)")
            self._sess = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            self._inp = self._sess.get_inputs()[0].name
            self._ready = True
            # State the detector AND whether alignment is live, the same way the camera line always states
            # its focus mode: "recognition is poor" and "recognition is running unaligned" look identical
            # from the outside, and they need completely different fixes.
            log.info("face_id: ready (model=%s, detector=%s, %s, threshold=%.2f)",
                     os.path.basename(self.model_path), self.detector,
                     "5-point ALIGNED" if self.aligned else "UNALIGNED crops",
                     self.threshold)
            if not self.aligned:
                log.warning(
                    "face_id: running on Haar boxes with NO landmark alignment -- ArcFace was trained on "
                    "5-point-aligned crops, so expect scores to bunch up near 0.15-0.20 and most visitors "
                    "to go unrecognised (a field run matched 6 of 257 face frames this way). Install the "
                    "YuNet detector to fix it:  ./scripts/provision_face_id.sh   then RE-ENROL everyone "
                    "(bash enroll_all.sh) so the stored embeddings are aligned too.")
            # The other half of that story: the detector says how we make embeddings NOW, this says how the
            # STORED ones were made. Aligning only the live side is worse than not aligning at all.
            self.check_enrolment_provenance()
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

    def _load_detector(self, cv2):
        """Pick and open the face detector: YuNet when we can, Haar otherwise. Sets .detector/.aligned.
        Never raises -- a detector we can't open just means we try the next one down."""
        want = self._detector_pref
        if want in ("auto", "yunet"):
            try:
                if not hasattr(cv2, "FaceDetectorYN"):
                    raise RuntimeError(f"this OpenCV build ({cv2.__version__}) has no FaceDetectorYN "
                                       "(needs >= 4.5.4)")
                if not os.path.isfile(self.yunet_path):
                    raise FileNotFoundError(f"YuNet model not found: {self.yunet_path} -- run "
                                            "./scripts/provision_face_id.sh to download it")
                # Input size is a placeholder; _boxes_yunet() calls setInputSize() with the real frame size.
                self._yunet = cv2.FaceDetectorYN.create(self.yunet_path, "", (320, 320),
                                                        float(self.yunet_score), 0.3, 5000)
                self.detector, self.aligned = "yunet", True
                return
            except Exception as e:
                self._yunet = None
                # An EXPLICIT 'yunet' that fails is worth a warning; 'auto' falling back is routine on a
                # robot that simply hasn't downloaded the model yet -- the startup line below still says so.
                (log.warning if want == "yunet" else log.info)(
                    "face_id: YuNet unavailable (%s) -- falling back to the Haar cascade", e)
        try:
            cascade_path = os.environ.get("ROBOT_FACE_ID_DETECTOR", "") or \
                os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            self._cascade = cv2.CascadeClassifier(cascade_path)
            if self._cascade.empty():
                raise RuntimeError(f"could not load face detector cascade: {cascade_path}")
            self.detector, self.aligned = "haar", False
        except Exception as e:
            log.warning("face_id: no Haar cascade either (%s)", e)
            self._cascade = None
            self.detector, self.aligned = "none", False

    # -- core --------------------------------------------------------------------------------------------
    def _boxes_yunet(self, bgr):
        """YuNet detection -> list of (x, y, w, h, landmarks) with landmarks as 5 (x, y) pairs.
        YuNet wants the input size declared before every detect() on a differently-sized frame."""
        cv2, np = self._cv2, self._np
        h, w = bgr.shape[:2]
        if self._yunet_size != (w, h):
            self._yunet.setInputSize((w, h))
            self._yunet_size = (w, h)
        _, faces = self._yunet.detect(bgr)
        if faces is None:
            return []
        out = []
        for f in faces:
            # Row layout (OpenCV 4.x): 0-3 bbox x,y,w,h | 4-13 the 5 landmarks as x,y pairs in the order
            # right eye, left eye, nose tip, right mouth corner, left mouth corner | 14 score.
            bw, bh = float(f[2]), float(f[3])
            if max(bw, bh) < self.min_face:
                # TOO FAR AWAY, not absent -- remember the biggest one we threw away. Without this the two
                # cases are indistinguishable downstream: an empty hallway and a visitor standing a metre
                # too far back both arrive as "motion but no face", so the log cannot tell you whether to
                # lower ROBOT_FACE_ID_MIN_FACE_PX or go looking for a false PIR trigger. This is exactly
                # the ambiguity behind the unexplained no-face wakes.
                self.last_rejected_px = max(self.last_rejected_px, int(max(bw, bh)))
                continue
            pts = np.array(f[4:14], dtype=np.float32).reshape(5, 2)
            out.append((float(f[0]), float(f[1]), bw, bh, pts))
        return out

    def _boxes(self, bgr):
        """Detect faces and return the boxes, recording the largest one's size for diagnostics. Shared by
        detect() and _largest_face() so both use the SAME strictness settings (they used to hard-code them
        separately, which meant tuning one silently left the other alone).

        Returns a list of (x, y, w, h, landmarks|None) -- landmarks are present only on the YuNet path."""
        cv2 = self._cv2
        self.last_rejected_px = 0    # truthful for THIS call, like last_face_px below
        if self._yunet is not None:
            faces = self._boxes_yunet(bgr)
        elif self._cascade is not None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            faces = [(float(x), float(y), float(w), float(h), None) for (x, y, w, h)
                     in self._cascade.detectMultiScale(gray, scaleFactor=1.1,
                                                       minNeighbors=self.min_neighbors,
                                                       minSize=(self.min_face, self.min_face))]
        else:
            faces = []
        self.last_face_px = int(max((max(f[2], f[3]) for f in faces), default=0))
        self.last_face_count = len(faces)
        return faces

    def _align(self, bgr, pts):
        """Warp a face onto the canonical ArcFace 5-point template -> an input_size square BGR crop, or None.

        This is the step that makes the ArcFace scores mean anything (see the module header). A similarity
        transform (rotate + uniform scale + translate, via estimateAffinePartial2D) is exactly right here:
        it removes head roll, distance and framing -- the nuisance variation -- without the shear/stretch a
        full affine would introduce, which would distort the face itself."""
        cv2, np = self._cv2, self._np
        dst = np.array(_ARCFACE_DST_112, dtype=np.float32)
        if self.input_size != 112:
            dst = dst * (float(self.input_size) / 112.0)   # the template is defined at 112px
        m, _ = cv2.estimateAffinePartial2D(np.asarray(pts, dtype=np.float32), dst,
                                           method=cv2.LMEDS)
        if m is None:
            return None
        return cv2.warpAffine(bgr, m, (self.input_size, self.input_size), flags=cv2.INTER_LINEAR)

    def _crop_box(self, bgr, x, y, w, h):
        """Fallback crop when there are no landmarks (Haar): the plain box plus a small margin. Keeps the
        pre-alignment behaviour byte-for-byte, so a Haar-only robot is unchanged by this refactor."""
        cv2 = self._cv2
        x, y, w, h = int(x), int(y), int(w), int(h)
        m = int(0.15 * max(w, h))                            # small margin so the crop isn't too tight
        y0, y1 = max(0, y - m), min(bgr.shape[0], y + h + m)
        x0, x1 = max(0, x - m), min(bgr.shape[1], x + w + m)
        crop = bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        return cv2.resize(crop, (self.input_size, self.input_size))

    def _blob(self, bgr, x, y, w, h, pts):
        """ONE detected box -> a preprocessed NCHW float32 blob, or None when the crop is unusable.

        Lifted verbatim out of _largest_face so _face_blobs can apply it to every face in the frame. The
        landmark-then-fallback order and the ArcFace normalisation are unchanged, which is what keeps a
        Haar-only robot and the tuned concierge path identical across this refactor."""
        cv2, np = self._cv2, self._np
        crop = None
        if pts is not None:
            crop = self._align(bgr, pts)     # the good path: landmarks -> canonical template
        if crop is None:
            crop = self._crop_box(bgr, x, y, w, h)   # no landmarks, or a degenerate transform
        if crop is None:
            return None
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 127.5                          # ArcFace normalisation -> [-1, 1]
        return np.transpose(rgb, (2, 0, 1))[np.newaxis, :]   # NCHW, batch 1

    def _face_blobs(self, bgr, limit=None):
        """Every detected face as [(blob, (x, y, w, h)), ...], NEAREST FIRST (largest box first).

        Nearest-first is the whole ordering contract: it makes `limit` drop the people furthest away
        rather than an arbitrary subset, and it is the order a person naming a room would use. Sorting is
        stable, so ties keep detector order -- the same face _largest_face used to pick with max().

        `limit` caps how many faces are CROPPED AND RETURNED, not how many are detected: self.last_face_count
        is set by _boxes() and stays truthful about the whole frame, so a caller can still say "and two
        others further back". Faces whose crop fails are dropped from the list rather than returned as
        holes."""
        faces = self._boxes(bgr)
        if not faces:
            self._last_landmarks = None
            return []
        ordered = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        self._last_landmarks = ordered[0][4]     # diagnostics stay about the PRIMARY face, as before
        if limit is not None and limit > 0:
            ordered = ordered[:limit]
        out = []
        for x, y, w, h, pts in ordered:
            blob = self._blob(bgr, x, y, w, h, pts)
            if blob is not None:
                out.append((blob, (int(x), int(y), int(w), int(h))))
        return out

    def _largest_face(self, bgr):
        """Return a preprocessed NCHW float32 blob for the LARGEST detected face, or None if no face.

        Now expressed as _face_blobs(bgr, 1) with the box dropped. Behaviourally identical to the hand-rolled
        version it replaces -- same box chosen, same crop path, same None on an unusable crop -- so embed()
        and identify(), and through them the tuned concierge greeting, are untouched by multi-face support."""
        blobs = self._face_blobs(bgr, 1)
        return blobs[0][0] if blobs else None

    def embed(self, jpeg_bytes):
        """JPEG bytes -> L2-normalised face embedding (np.float32), or None (no libs / no face / error).

        ALWAYS leaves `last_face_px` truthful for this call -- 0 when no face was found (or we never got far
        enough to look), >0 when one was. identify() reports that to its caller as `face_seen`, which is what
        lets the recognition loop skip a second, redundant detector pass over the same bytes."""
        self.last_face_px = 0        # reset FIRST: a bail-out below must not leave the previous call's box
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
        """True if a HUMAN FACE is PRESENT in the frame -- detection ONLY, recognition NOT required, so an
        un-enrolled stranger (a trick-or-treater) counts. This is Halloween Mode's 'someone is at the door'
        signal AND the concierge's cheap Stage-A presence probe (one frame, one detector pass, no ArcFace).
        100% local; never raises. Returns False if cv2/model aren't set up, or no face."""
        self.last_face_px = 0        # same contract as embed(): truthful for THIS call, even on a bail-out
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

    def _enrollments(self):
        """The enrolment set as `(names, (N, D) matrix, {name: [row, ...]})`, cached across frames.

        WHY. identify() used to call mem.all_faces() on EVERY frame -- a SELECT returning one BLOB per
        enrolled photo, then np.frombuffer per row, then a python loop of N dot products. A recognition
        window runs that 2-4 times a second for up to 45s, so a single visitor cost thousands of identical
        reads of a table that changes only when somebody is enrolled.

        Measured honestly, this is a SMALL win: at 73 enrolments the whole read+decode+score is ~0.18ms
        against a ~390ms frame (capture + YuNet + ArcFace dominate), so it is ~0.05% of a frame, not the
        hot-path rescue it looks like. It is here because it is also SIMPLER -- one matmul instead of a
        loop -- and because the cost grows with the enrolment count while the benefit of re-reading is
        exactly zero.

        Correctness rests on mem.faces_version(), which add_face/forget_face bump: enrol somebody while the
        robot is running and the very next frame rebuilds. A Memory without the counter (an older stub, or
        a test double) falls back to reading every time -- never stale, just slower."""
        np = self._np
        try:
            version = self.mem.faces_version()
        except AttributeError:
            version = None                       # no counter -> always rebuild (correct, just not cached)
        if version is None or version != self._faces_version or self._faces_mat is None:
            names, rows, by_name = [], [], {}
            dim = None
            for name, emb_bytes in self.mem.all_faces():
                row = np.frombuffer(emb_bytes, dtype=np.float32)
                if dim is None:
                    dim = row.shape
                if row.shape != dim:
                    continue                     # a row from a different model -- skip, as before
                names.append(name)
                rows.append(row)
                by_name.setdefault(name, []).append(row)
            self._faces_names = names
            self._faces_mat = np.stack(rows) if rows else None
            self._faces_by_name = by_name
            self._faces_version = version
            if names:
                log.debug("face_id: enrolment cache built -- %d embedding(s) for %d person(s)",
                          len(names), len(by_name))
        return self._faces_names, self._faces_mat, self._faces_by_name

    def _match(self, vec):
        """Score ONE embedding against the enrolment set -> a decision dict. No logging, no state changes.

        Split out of identify() so identify_all() can apply the SAME threshold and margin rules to every
        face in a frame without either duplicating them (two copies of a safety rule is one copy too many)
        or emitting identify()'s per-face INFO line N times per frame. identify() keeps its own logging and
        its own 3-tuple return, so its behaviour across this split is unchanged.

          name    the accepted match, or None -- below threshold, too close to call, or nobody enrolled
          score   the best similarity seen (0.0 when there was nothing to score)
          ranked  [(score, name), ...] best first; the runner-up is what says whether it was decisive
          sims    {name: [per-photo sim, ...]}, for the photo count in identify()'s log line
          margin  best minus runner-up (inf when only one person is enrolled)
          reason  'match' | 'ambiguous' (over the bar but neck and neck) | 'below' | 'nobody' (none enrolled)
        """
        # Group every enrolled row by person: their per-photo cosine sims (+ raw embeddings for 'centroid').
        # The enrolment set is read ONCE and kept as a stacked (N, D) matrix -- see _enrollments() -- so a
        # frame costs one matmul instead of a SELECT plus a python loop of N dots. Enrol/forget bump
        # mem.faces_version(), so a person added mid-run is picked up on the very next frame.
        names, mat, embs_by_name = self._enrollments()
        sims, embs = {}, {}
        if mat is not None and mat.shape[1] == vec.shape[0]:
            scores = mat @ vec                       # both L2-normalised -> dot == cosine, all rows at once
            for name, score in zip(names, scores):
                sims.setdefault(name, []).append(float(score))
            if self._aggregate == "centroid":
                embs = embs_by_name
        if not sims:
            return {"name": None, "score": 0.0, "ranked": [], "sims": {},
                    "margin": float("inf"), "reason": "nobody"}
        # RANK everyone, don't just track the winner. The runner-up's score is the number that says whether a
        # match was DECISIVE or a coin flip: 'matched Ramesh 0.398' told us nothing about whether Shanika
        # scored 0.11 (safe) or 0.39 (one bad frame from greeting the wrong person by name). Without the
        # margin there is no way to tune ROBOT_FACE_ID_THRESHOLD from a run log, or to notice that two
        # enrolled people have become confusable. Cheap: the per-person scores are already computed.
        ranked = sorted(((self._combine(person_sims, embs.get(name), vec), name)
                         for name, person_sims in sims.items()), reverse=True)
        best, best_name = ranked[0]
        margin = (best - ranked[1][0]) if len(ranked) > 1 else float("inf")
        if best_name is not None and best >= self.threshold:
            reason = "match" if margin >= self.min_margin else "ambiguous"
        else:
            reason = "below"
        return {"name": best_name if reason == "match" else None, "score": best,
                "ranked": ranked, "sims": sims, "margin": margin, "reason": reason}

    def identify_all(self, jpeg_bytes, limit=None):
        """JPEG bytes -> EVERY face in the frame, recognised where possible. Never raises.

        identify() answers "who is at the door", which is a one-face question and rightly resolves only the
        largest box. This answers "who can you see", which is not: naming the nearest person and silently
        ignoring the two beside them is a wrong answer, not a partial one.

          {"faces": [{"name": str|None, "score": float, "box": (x, y, w, h), "reason": str}, ...],
           "face_count": int,     # faces DETECTED in the frame, before the cap
           "known": [str, ...],   # accepted names, nearest first, de-duplicated
           "truncated": bool}     # True when the cap left faces unscored

        Faces come back NEAREST FIRST, so a caller can name them in the order a person would. `reason` is
        _match's, plus 'error' for a face whose embedding failed -- which is why an unrecognised face and a
        face we never managed to score stay distinguishable in the answer.

        Cost is one detector pass plus ONE ArcFace inference PER FACE, capped by ROBOT_IDENTIFY_MAX_FACES.
        The enrolment matrix is fetched once for the whole frame, not once per face."""
        out = {"faces": [], "face_count": 0, "known": [], "truncated": False}
        if not self._load() or not jpeg_bytes:
            self.last_face_px = 0
            self.last_face_count = 0
            return out
        cap = self._max_faces if limit is None else max(1, int(limit))
        cv2, np = self._cv2, self._np
        try:
            bgr = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                self.last_face_px = 0
                self.last_face_count = 0
                return out
            blobs = self._face_blobs(bgr, cap)          # sets last_face_count for the WHOLE frame
            out["face_count"] = self.last_face_count
            out["truncated"] = self.last_face_count > cap
            for blob, box in blobs:
                try:
                    vec = self._sess.run(None, {self._inp: blob})[0][0].astype(np.float32)
                    n = float(np.linalg.norm(vec))
                    vec = vec / n if n > 0 else None
                except Exception as e:
                    log.debug("face_id identify_all: embedding one face failed: %s", e)
                    vec = None
                if vec is None:
                    out["faces"].append({"name": None, "score": 0.0, "box": box, "reason": "error"})
                    continue
                m = self._match(vec)
                out["faces"].append({"name": m["name"], "score": float(m["score"]),
                                     "box": box, "reason": m["reason"]})
                log.debug("face_id: face at %s -> %s (%.3f, %s)", box, m["name"] or "unknown",
                          m["score"], m["reason"])
            seen = set()
            for f in out["faces"]:
                if f["name"] and f["name"] not in seen:
                    seen.add(f["name"])
                    out["known"].append(f["name"])
            # ONE line per call, not one per face: this runs up to ROBOT_IDENTIFY_FRAMES times per question.
            log.info("face_id: %d face(s)%s -> %s", out["face_count"],
                     f" (capped at {cap})" if out["truncated"] else "",
                     ", ".join(out["known"]) if out["known"] else "nobody recognised")
        except Exception as e:
            log.debug("face_id identify_all failed: %s", e)
        return out

    def identify(self, jpeg_bytes):
        """JPEG bytes -> (name, score, face_seen).

          name       the best enrolled match at/above the threshold, else None
          score      that match's similarity (0.0 when there was nothing to score)
          face_seen  whether a FACE WAS DETECTED at all -- independent of whether it matched anyone

        `face_seen` is the third element on purpose. Without it a caller could not tell "an empty room"
        (score 0, no face) from "a visitor nobody has enrolled" (score 0, face right there), so it had to run
        a second, redundant detector pass over the same bytes to find out. Now identify() just says.
        Never raises.

        A person may have SEVERAL enrolled photos (one embedding row each); they're combined PER PERSON by
        ROBOT_FACE_ID_AGGREGATE so extra images sharpen recognition instead of just adding rows:
          max      best-matching enrolled photo wins   (default; most tolerant of pose/light, best recall)
          topk     mean of the person's top-K photos    (K=ROBOT_FACE_ID_TOPK; robust middle ground)
          mean     average over ALL their photos        (strictest; the visitor must resemble every shot)
          centroid cosine vs their averaged 'prototype' (smooths per-photo noise into one template)
        """
        vec = self.embed(jpeg_bytes)
        # embed() has just refreshed last_face_px for THIS frame, so it is the authoritative presence answer
        # even when embedding itself failed further down (a zero-size crop, an ONNX hiccup).
        face_seen = self.last_face_px > 0
        if vec is None:
            return None, 0.0, face_seen
        m = self._match(vec)
        if m["reason"] == "nobody":
            # A real face, but nobody enrolled (or every stored row is a different dimension). face_seen is
            # True here -- that is exactly the case the old detect() fallback existed to catch.
            return None, 0.0, face_seen
        ranked, sims = m["ranked"], m["sims"]
        self.last_ranked = [(n, s) for s, n in ranked[:3]]   # diagnostics for callers
        best, best_name = ranked[0]
        runner = f", runner-up {ranked[1][1]!r} {ranked[1][0]:.3f} (margin {best - ranked[1][0]:+.3f})" \
                 if len(ranked) > 1 else " (only one person enrolled)"
        margin = m["margin"]
        if m["reason"] in ("match", "ambiguous"):
            if m["reason"] == "ambiguous":
                # Over the bar, but two people are neck and neck -- don't gamble a name on it.
                log.info("face_id: %r cleared the threshold (%.3f >= %.2f) but only by %+.3f over %r "
                         "(need %+.3f) -- too close to call, greeting without a name%s",
                         best_name, best, self.threshold, margin, ranked[1][1], self.min_margin,
                         f"; face box {self.last_face_px}px")
                return None, best, True
            log.info("face_id: matched %r (%s %.3f >= %.2f over %d photo(s); face box %dpx)%s",
                     best_name, self._aggregate, best, self.threshold, len(sims[best_name]),
                     self.last_face_px, runner)
            return best_name, best, True
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
        return None, max(best, 0.0), face_seen

    def provenance(self):
        """How THIS process makes embeddings: 'yunet+aligned', 'haar+unaligned', etc.

        Stamped on every row enroll() writes, and compared against the stored set at startup. The two are
        not interchangeable: ArcFace is trained on 5-point-aligned crops, so an unaligned embedding scored
        against an aligned one lands in the same narrow noise band as a total stranger. A field run on the
        Haar (unaligned) path recognised 6 of 257 face frames with every score squashed into 0.13-0.21 and
        the 'closest' name spread evenly over all 35 enrolled people -- a lottery that looked exactly like
        a tuning problem. It was a provenance problem, and nothing in the database could say so."""
        detector = "yunet" if self._yunet is not None else ("haar" if self._cascade is not None else "none")
        return f"{detector}+{'aligned' if detector == 'yunet' else 'unaligned'}"

    def check_enrolment_provenance(self):
        """Log once at startup if the stored embeddings were not all made the way this process makes them.

        Never raises and never blocks anything -- it is a diagnosis, not a gate. Returns True when the set
        is consistent with this pipeline."""
        try:
            counts = self.mem.face_provenance()
            if not counts:
                return True
            mine = self.provenance()
            foreign = {p: n for p, n in counts.items() if p != mine}
            if not foreign:
                log.info("face_id: all %d enrolled embedding(s) match this pipeline (%s)",
                         sum(counts.values()), mine)
                return True
            log.warning(
                "face_id: %d of %d enrolled embedding(s) were NOT made by this pipeline (%s) -- found %s. "
                "ArcFace is trained on 5-point ALIGNED crops, so an embedding made without alignment scored "
                "against an aligned live crop lands in the same noise band as a stranger: scores squash "
                "together, margins vanish, and the 'closest' name becomes a lottery. This looks exactly "
                "like a threshold problem and is not one. FIX: re-enrol everyone with the current "
                "pipeline -- bash enroll_all.sh",
                sum(foreign.values()), sum(counts.values()), mine,
                ", ".join(f"{n}x {p}" for p, n in sorted(foreign.items())))
            return False
        except Exception as e:
            log.debug("face_id: provenance check failed (%s)", e)
            return True

    def enroll(self, jpeg_bytes, name):
        """Store a face embedding under `name`. Returns True if a face was found + stored, else False.
        Uses the RELAXED enrolment floor (see min_face_enroll) so a deliberately-chosen photo with a smallish
        face still counts -- and says so when it accepts one the LIVE detector would have skipped, because
        that person will be harder to recognise from across a room."""
        live_floor = self.min_face
        self.min_face = min(self.min_face, self.min_face_enroll)
        try:
            vec = self.embed(jpeg_bytes)
            found_px = self.last_face_px
        finally:
            self.min_face = live_floor
        if vec is None:
            log.warning("face_id: no usable face in the photo for %r -- NOT enrolled (need a face of at "
                        "least %dpx; use a closer/sharper shot)", name, self.min_face_enroll)
            return False
        self.mem.add_face(name.strip(), vec.tobytes(), provenance=self.provenance())
        if found_px < live_floor:
            log.info("face_id: enrolled %r (face only %dpx, below the live %dpx floor -- this shot will "
                     "help less at a distance; a closer photo would enrol better)", name, found_px, live_floor)
        else:
            log.info("face_id: enrolled %r", name)
        return True
