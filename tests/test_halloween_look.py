"""Halloween doorway check: a spook needs the PIR AND the camera (a face, or movement in frame)."""
import os
import sys

sys.path.insert(0, os.environ.get("BRAIN_DIR") or os.getcwd())
import cv2
import numpy as np

import halloween

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


def jpeg(person_at=None, brightness=0, speck=False):
    """A 768px letterboxed porch: a textured background, optionally a 'visitor' (a dark 120x260 block) at
    x=person_at, a whole-frame exposure shift, or a 6px speck of noise."""
    img = np.zeros((768, 768, 3), np.uint8)
    yy, xx = np.mgrid[0:432, 0:768]
    porch = (60 + (xx * 90) // 768 + ((yy // 24) % 2) * 25).astype(np.int16)
    if person_at is not None:
        porch[120:380, person_at:person_at + 120] = 20
    if speck:
        porch[200:206, 400:406] = 250
    porch = np.clip(porch + brightness, 0, 255).astype(np.uint8)
    img[168:600] = porch[..., None]
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buf.tobytes()


EMPTY = jpeg()

print("\n=== real frames: an empty porch never counts as movement ===")
m = halloween.movement_detector()
check("frame 1 (no reference yet)", m(EMPTY), False)
check("frame 2, same empty porch", m(EMPTY), False)
check("frame 3, same empty porch", m(EMPTY), False)
check("a whole-frame exposure shift is not movement", m(jpeg(brightness=30)), False)
check("a 6px speck is not movement", m(jpeg(speck=True)), False)

print("\n=== real frames: a visitor walking in IS movement ===")
m = halloween.movement_detector()
m(EMPTY)
check("visitor appears (vs the empty frame)", m(jpeg(person_at=300)), True)
m = halloween.movement_detector()
m(jpeg(person_at=200))
m(jpeg(person_at=200))
check("visitor stepped 100px since two frames back", m(jpeg(person_at=300)), True)

print("\n=== the reference is TWO frames back (V4L2 hands back-to-back frames) ===")
seen = []
m = halloween.movement_detector(fraction=0.5, prepare=lambda f: f)
for f in (np.zeros(4, np.int16), np.zeros(4, np.int16), np.full(4, 100, np.int16), np.full(4, 100, np.int16),
          np.full(4, 100, np.int16)):
    seen.append(m(f))
check("f1 none, f2 vs f1, f3 vs f1 (moved), f4 vs f2 (moved), f5 vs f3 (still)", seen,
      [False, False, True, True, False])
check("a frame that won't decode doesn't count", halloween.movement_detector()(b"not a jpeg"), False)


def doorway(frames, faces=(), held=lambda n: False):
    """Run check_doorway over a scripted camera. frames: list of JPEGs (None = capture failed); the last
    one repeats. faces: attempt numbers where the face detector fires. held(n): sensor still HIGH?"""
    NOW[0] = 0.0
    n = [0]

    def capture():
        n[0] += 1
        return frames[min(n[0], len(frames)) - 1]

    return halloween.check_doorway(capture, lambda _j: n[0] in faces, still_present=lambda: held(n[0]),
                                   sleep=sleep, clock=clock)


print("\n=== check_doorway: only 'seen' may spook ===")
v, look = doorway([EMPTY])
check("empty porch, sensor released -> nobody", v, "nobody")
check("...after exactly the LOOK_FRAMES floor", look["attempts"], halloween.LOOK_FRAMES)
v, look = doorway([EMPTY, EMPTY, jpeg(person_at=300)])
check("visitor walks in on frame 3 -> seen", (v, look["attempts"]), ("seen", 3))
v, look = doorway([EMPTY], faces=(1,))
check("a face on frame 1 -> seen at once (someone standing still)", (v, look["attempts"]), ("seen", 1))
v, look = doorway([None])
check("camera returns nothing -> blind, NOT seen (no spook without the camera)", v, "blind")
v, look = doorway([EMPTY], held=lambda n: True)
check("PIR stuck HIGH at an empty porch -> still nobody", v, "nobody")
check("...and the look is bounded by LOOK_MAX_S", NOW[0] <= halloween.LOOK_MAX_S + 1.0, True)
v, look = doorway([EMPTY] * 6 + [jpeg(person_at=300)], held=lambda n: n < 7)
check("visitor reaches the frame while the PIR still asserts -> seen", (v, look["attempts"]), ("seen", 7))

print("\n=== env ===")
saved = {k: os.environ.pop(k, None) for k in ("ROBOT_HALLOWEEN_CAMERA_CHECK", "ROBOT_HALLOWEEN_MOVE_FRACTION",
                                              "ROBOT_CAMERA_WARM_WINDOW_S", "ROBOT_DETECT_CAPTURE",
                                              "ROBOT_DETECT_CAPTURE_DIR")}
import tempfile
import captures
with tempfile.TemporaryDirectory() as tmp:
    os.environ["ROBOT_DETECT_CAPTURE_DIR"] = tmp
    shots = [os.path.basename(captures.save(EMPTY, "spook") or ""),
             os.path.basename(captures.save(EMPTY, "spook", "reaction") or "")]
    check("deciding frame saved as <time>-spook.jpg", shots[0].endswith("-spook.jpg"), True)
    check("reaction shot saved as <time>-spook-reaction.jpg", shots[1].endswith("-spook-reaction.jpg"), True)
    os.environ.pop("ROBOT_DETECT_CAPTURE_DIR")
check("reaction shot lands just past the 2s camera warm window", halloween.reaction_delay_s(), 2.3)
os.environ["ROBOT_CAMERA_WARM_WINDOW_S"] = "3"
check("...and follows a longer warm window", halloween.reaction_delay_s(), 3.3)
os.environ["ROBOT_CAMERA_WARM_WINDOW_S"] = "junk"
check("...junk falls back to the 2s default", halloween.reaction_delay_s(), 2.3)
check("camera check defaults ON", halloween.camera_check(), True)
check("move fraction defaults to 1%", halloween.move_fraction(), 0.01)
os.environ["ROBOT_HALLOWEEN_CAMERA_CHECK"] = "0"
check("ROBOT_HALLOWEEN_CAMERA_CHECK=0 turns it off", halloween.camera_check(), False)
os.environ["ROBOT_HALLOWEEN_MOVE_FRACTION"] = "junk"
check("junk fraction falls back to 1%", halloween.move_fraction(), 0.01)
os.environ["ROBOT_HALLOWEEN_MOVE_FRACTION"] = "7"
check("fraction is capped at 1.0", halloween.move_fraction(), 1.0)
for k, val in saved.items():
    os.environ.pop(k, None)
    if val is not None:
        os.environ[k] = val

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
