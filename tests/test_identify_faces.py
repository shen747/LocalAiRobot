"""identify_all: resolve EVERY face in a frame, not just the largest.

identify() answers "who is at the door" -- a one-face question, rightly settled on the biggest box. This
pins the other one: "who can you see", where naming the nearest person and ignoring the two beside them is
a wrong answer, not a partial one. Also pins that _face_blobs (which identify()/embed() now go through)
still picks exactly the face the old hand-rolled _largest_face did. Skips itself if numpy is absent.
"""
import os
import sys
import types

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())

try:
    import numpy as np
except Exception as e:
    print(f"  SKIP  numpy not available ({e}) -- the recognition pipeline cannot be exercised here")
    sys.exit(0)

import face_id as F

FAILS = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        FAILS.append(n)


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


ADA, BO, STRANGER = unit(1, 0, 0, 0), unit(0, 1, 0, 0), unit(0, 0, 1, 0)


class FakeMem:
    def __init__(self, **people):
        self.rows = [(n, v.tobytes()) for n, v in people.items()]

    def faces_version(self):
        return 1

    def all_faces(self):
        return list(self.rows)


class Stub(F.FaceID):
    """A FaceID with the OpenCV/ONNX layer replaced, so the logic above it is testable on any box.

    Only the three genuinely external things are stubbed -- JPEG decode, face detection, ArcFace inference.
    _face_blobs, _match, identify_all and identify are the real implementations."""

    def __init__(self, mem, boxes, bad=()):
        super().__init__(mem)
        self._boxes_out = boxes          # [(x, y, w, h, vec_or_None), ...] in detector order
        self._bad = set(bad)             # box indices whose ArcFace pass explodes
        self._ready = True
        self._np = np
        self._inp = "in"
        self._cv2 = types.SimpleNamespace(imdecode=lambda *a, **k: "BGR", IMREAD_COLOR=1)
        self._sess = types.SimpleNamespace(run=self._run)

    def _load(self):
        return True

    def _boxes(self, bgr):
        faces = [(float(x), float(y), float(w), float(h), None)
                 for (x, y, w, h, _v) in self._boxes_out]
        self.last_face_px = int(max((max(f[2], f[3]) for f in faces), default=0))
        self.last_face_count = len(faces)
        return faces

    def _blob(self, bgr, x, y, w, h, pts):
        for i, (bx, by, bw, bh, _v) in enumerate(self._boxes_out):
            if (bx, by, bw, bh) == (int(x), int(y), int(w), int(h)):
                return i                 # the blob IS the index -- _run maps it back to a vector
        return None

    def _run(self, _out, feed):
        i = feed["in"]
        if i in self._bad:
            raise RuntimeError("onnxruntime fell over on this crop")
        v = self._boxes_out[i][4]
        if v is None:
            raise RuntimeError("no vector for this face")
        return [[v.astype(np.float32)]]


mem = FakeMem(Ada=ADA, Bo=BO)
os.environ["ROBOT_FACE_ID_THRESHOLD"] = "0.5"
os.environ["ROBOT_FACE_ID_MIN_MARGIN"] = "0.05"

print("\n=== THE POINT: three faces, three answers -- not just the nearest ===")
# w*h: Bo 40x40=1600, Ada 60x60=3600, stranger 20x20=400  -> nearest-first is Ada, Bo, stranger
fid = Stub(mem, [(0, 0, 40, 40, BO), (100, 0, 60, 60, ADA), (200, 0, 20, 20, STRANGER)])
r = fid.identify_all(b"jpeg")
ck("everybody enrolled was named", r["known"], ["Ada", "Bo"])
ck("all three faces reported", len(r["faces"]), 3)
ck("NEAREST FIRST, not detector order", [f["name"] for f in r["faces"]], ["Ada", "Bo", None])
ck("the unenrolled one is present but unnamed", r["faces"][2]["reason"], "below")
ck("face_count counts everyone", r["face_count"], 3)
ck("nothing was hidden", r["truncated"], False)

print("\n=== ...and identify() on that same frame still answers about ONE person ===")
name, score, seen = fid.identify(b"jpeg")
ck("identify() picks the LARGEST face, exactly as before", name, "Ada")
ck("...and says a face was there", seen, True)
ck("...with a real score", round(float(score), 3), 1.0)

print("\n=== the per-frame cap drops the FURTHEST people, and admits it ===")
fid = Stub(mem, [(0, 0, 60, 60, ADA), (100, 0, 40, 40, BO), (200, 0, 20, 20, STRANGER)])
r = fid.identify_all(b"jpeg", limit=1)
ck("only the nearest was scored", [f["name"] for f in r["faces"]], ["Ada"])
ck("but the true count survives", r["face_count"], 3)
ck("and the caller is told faces were skipped", r["truncated"], True)

print("\n=== one bad crop must not lose the rest of the room ===")
fid = Stub(mem, [(0, 0, 60, 60, ADA), (100, 0, 40, 40, BO)], bad={0})
r = fid.identify_all(b"jpeg")
ck("the survivor is still named", r["known"], ["Bo"])
ck("the failed face is reported, not dropped", len(r["faces"]), 2)
ck("...and is distinguishable from 'not recognised'", r["faces"][0]["reason"], "error")

print("\n=== the same person twice in frame is named ONCE ===")
fid = Stub(mem, [(0, 0, 60, 60, ADA), (100, 0, 40, 40, ADA)])
r = fid.identify_all(b"jpeg")
ck("de-duplicated", r["known"], ["Ada"])
ck("but both faces are still counted", r["face_count"], 2)

print("\n=== an empty room, and a frame that will not decode ===")
fid = Stub(mem, [])
r = fid.identify_all(b"jpeg")
ck("no faces", r["faces"], [])
ck("no names", r["known"], [])
ck("count is zero", r["face_count"], 0)
ck("empty bytes are handled", fid.identify_all(b"")["face_count"], 0)
fid._cv2 = types.SimpleNamespace(imdecode=lambda *a, **k: None, IMREAD_COLOR=1)
ck("an undecodable frame never raises", fid.identify_all(b"jpeg")["known"], [])

print("\n=== nobody enrolled at all: faces seen, no names, no crash ===")
fid = Stub(FakeMem(), [(0, 0, 60, 60, ADA)])
r = fid.identify_all(b"jpeg")
ck("no names to give", r["known"], [])
ck("but it did see somebody", r["face_count"], 1)
ck("and says why", r["faces"][0]["reason"], "nobody")

print("\n=== a neck-and-neck pair is refused a name, in identify_all as in identify() ===")
close = FakeMem(Ada=ADA, Twin=unit(0.99, 0.14, 0, 0))
fid = Stub(close, [(0, 0, 60, 60, ADA)])
r = fid.identify_all(b"jpeg")
ck("too close to call -> no name", r["known"], [])
ck("...and it is labelled as ambiguity, not absence", r["faces"][0]["reason"], "ambiguous")

print("\n" + "=" * 62)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
print("=" * 62)
sys.exit(1 if FAILS else 0)
