"""identify_group: answer "who can you see" over a burst -- the UNION of everyone, not the first match."""
import os
import sys

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())
from concierge import identify_group

FAILS = []
NOW = [0.0]


def clock():
    return NOW[0]


def sleep(s):
    NOW[0] += s


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def frame(*people, count=None, truncated=False):
    """people = (name|None, score) pairs -> one identify_all() result."""
    faces = [{"name": n, "score": s, "box": (0, 0, 10, 10), "reason": "match" if n else "below"}
             for n, s in people]
    known, seen = [], set()
    for n, _ in people:
        if n and n not in seen:
            seen.add(n)
            known.append(n)
    return {"faces": faces, "face_count": len(people) if count is None else count,
            "known": known, "truncated": truncated}


def run(results, frames=3):
    """results = one identify_all() dict per frame; captures never fail."""
    NOW[0] = 0.0
    it = iter(results)
    last = [None]

    def identify_all(_jpeg):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    n = [0]

    def capture():
        n[0] += 1
        return f"jpeg{n[0]}".encode()

    return identify_group(capture, identify_all, frames=frames, interval_s=0.2,
                          clock=clock, sleep=sleep)


print("\n=== THE POINT: a second person who only appears in frame 2 is still named ===")
r = run([frame(("Ramesh", 0.41)),
         frame(("Ramesh", 0.72), ("Shanika", 0.60)),
         frame(("Ramesh", 0.20))])
check("both people found", r["known"], ["Ramesh", "Shanika"])
check("kept each person's BEST score, not the last", r["scores"], {"Ramesh": 0.72, "Shanika": 0.60})
check("reports the fullest frame's face count", r["face_count"], 2)

print("\n=== ...which is exactly what stopping at the first match would have missed ===")
check("frame 1 alone would have said only this", run([frame(("Ramesh", 0.41))], frames=1)["known"],
      ["Ramesh"])

print("\n=== a stable, COMPLETE answer stops the burst early (cost, not correctness) ===")
one = frame(("Ramesh", 0.5))
r = run([one, one, one])
check("one person, good light -> confirm once and stop", r["attempts"], 2)

print("\n=== an UNRECOGNISED face keeps trying: the answer is not complete yet ===")
mixed = frame(("Ramesh", 0.5), (None, 0.1))
r = run([mixed, mixed, mixed])
check("used the whole budget", r["attempts"], 3)
check("named who it knew", r["known"], ["Ramesh"])
check("and counted who it didn't", r["unknown"], 1)

print("\n=== nobody enrolled: faces seen, no names, still bounded ===")
r = run([frame((None, 0.0), (None, 0.0))] * 3)
check("no names", r["known"], [])
check("but it saw them", r["face_count"], 2)
check("all unrecognised", r["unknown"], 2)
check("bounded by the frame budget", r["attempts"], 3)

print("\n=== best-scoring person is named FIRST ===")
r = run([frame(("Ada", 0.40), ("Bo", 0.90))])
check("Bo outscored Ada so Bo leads", r["known"], ["Bo", "Ada"])

print("\n=== the per-frame face cap is reported, never silently swallowed ===")
r = run([frame(("Ada", 0.9), ("Bo", 0.8), count=7, truncated=True)] * 2)
check("truncation surfaced to the caller", r["truncated"], True)
check("the TRUE face count survives the cap", r["face_count"], 7)

print("\n=== NEVER RAISES, and a broken recogniser retries instead of giving up ===")


def boom(_):
    raise RuntimeError("onnxruntime fell over")


NOW[0] = 0.0
r = identify_group(lambda: b"jpeg", boom, frames=3, interval_s=0.0, clock=clock, sleep=sleep)
check("survived a throwing recogniser", r["known"], [])
check("retried rather than treating the error as an answer", r["attempts"], 3)

NOW[0] = 0.0
r = identify_group(lambda: None, boom, frames=3, interval_s=0.0, clock=clock, sleep=sleep)
check("a dead camera costs no attempts", r["attempts"], 0)
check("...and no frame to keep", r["jpeg"], None)


def cam_boom():
    raise RuntimeError("v4l2 gone")


NOW[0] = 0.0
r = identify_group(cam_boom, boom, frames=2, interval_s=0.0, clock=clock, sleep=sleep)
check("survived a throwing camera", r["known"], [])

print("\n=== the decisive frame comes back for review ===")
r = run([frame((None, 0.0)), frame(("Ramesh", 0.8))])
check("kept the frame that recognised somebody", r["jpeg"], b"jpeg2")

print("\n" + "=" * 62)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
print("=" * 62)
sys.exit(1 if FAILS else 0)
