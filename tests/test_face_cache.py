"""The enrolment cache must be faster AND never stale, and must score identically to the old loop.

identify() used to re-SELECT and re-decode every enrolled embedding on EVERY frame of a recognition
window. The cache replaces that with one stacked (N, D) matmul, rebuilt only when Memory.faces_version()
moves. The risk it introduces is staleness -- enrol somebody while the robot runs and never see them -- so
that is what most of this pins. Skips itself if numpy is absent.
"""
import os
import sys

sys.path.insert(0, os.environ["BRAIN_DIR"])

try:
    import numpy as np
except Exception as e:
    print(f"  SKIP  numpy not available ({e}) -- the enrolment cache cannot be exercised here")
    sys.exit(0)

import face_id as F

FAILS = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        FAILS.append(n)


class FakeMem:
    """Memory's enrolment surface, counting reads so caching is observable."""

    def __init__(self):
        self.rows, self._v, self.reads = [], 0, 0

    def faces_version(self):
        return self._v

    def all_faces(self):
        self.reads += 1
        return list(self.rows)

    def add(self, name, vec):
        self.rows.append((name, vec.astype(np.float32).tobytes()))
        self._v += 1


class NoCounterMem(FakeMem):
    """An older Memory (or a test double) with no faces_version at all."""

    def __getattribute__(self, n):
        if n == "faces_version":
            raise AttributeError(n)
        return object.__getattribute__(self, n)


def make(mem, aggregate="max"):
    fid = F.FaceID.__new__(F.FaceID)          # bypass __init__: no models, no env, no ONNX
    fid.mem, fid._np = mem, np
    fid._faces_version, fid._faces_names, fid._faces_mat, fid._faces_by_name = object(), [], None, {}
    fid._aggregate = aggregate
    return fid


def unit(rng):
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


rng = np.random.default_rng(1)

print("=== built once, then served from cache ===")
mem = FakeMem()
vecs = {}
for i in range(6):
    name = f"p{i // 2}"
    v = unit(rng)
    mem.add(name, v)
    vecs.setdefault(name, []).append(v)
fid = make(mem)
names, mat, by_name = fid._enrollments()
ck("one read to build", mem.reads, 1)
ck("all rows present", len(names), 6)
ck("stacked into one matrix", mat.shape, (6, 512))
ck("grouped by person", sorted(by_name), ["p0", "p1", "p2"])
for _ in range(50):
    fid._enrollments()
ck("50 more frames cost no reads at all", mem.reads, 1)

print("\n=== an enrolment mid-run is picked up on the NEXT frame (the staleness risk) ===")
mem.add("newbie", unit(rng))
names, mat, by_name = fid._enrollments()
ck("rebuilt", mem.reads, 2)
ck("the new person is in the matrix", len(names), 7)
ck("...and grouped", "newbie" in by_name, True)

print("\n=== scores are IDENTICAL to the old per-row python loop ===")
probe = vecs["p1"][0]
new = mat @ probe
old = [float(np.dot(probe, np.frombuffer(b, dtype=np.float32))) for _, b in mem.all_faces()]
ck("matmul == loop", bool(np.allclose(new, old, atol=1e-6)), True)
ck("a self-match still scores 1.0", abs(float(max(new)) - 1.0) < 1e-5, True)

print("\n=== a Memory with no faces_version degrades to rebuild-every-time, never stale ===")
nm = NoCounterMem()
for i in range(3):
    nm.add(f"q{i}", unit(rng))
f2 = make(nm)
for _ in range(3):
    f2._enrollments()
ck("read every call (correct, just not cached)", nm.reads, 3)
nm.add("late", unit(rng))
names2, _m2, _b2 = f2._enrollments()
ck("...so a late enrolment is still seen", "late" in names2, True)

print("\n=== nobody enrolled, and mismatched dimensions ===")
f3 = make(FakeMem())
n3, m3, b3 = f3._enrollments()
ck("no names", n3, [])
ck("no matrix (identify() guards on this)", m3, None)
ck("no groups", b3, {})

mixed = FakeMem()
mixed.add("ok", unit(rng))
mixed.rows.append(("wrongdim", np.zeros(128, dtype=np.float32).tobytes()))   # a row from another model
mixed._v += 1
f4 = make(mixed)
n4, m4, _b4 = f4._enrollments()
ck("the odd-sized row is skipped, not fatal", n4, ["ok"])
ck("matrix keeps the good row only", m4.shape, (1, 512))

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + str(len(FAILS))}")
print("=" * 58)
sys.exit(1 if FAILS else 0)
