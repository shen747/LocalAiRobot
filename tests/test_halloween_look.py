"""Halloween doorway check: a spook needs the PIR AND a person in front of the camera."""
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


def jpeg(block_at=None):
    """A 768px letterboxed porch like the robot's captures: a textured background, optionally with a dark
    120x260 block in it (a shape, not a person)."""
    img = np.zeros((768, 768, 3), np.uint8)
    yy, xx = np.mgrid[0:432, 0:768]
    porch = (60 + (xx * 90) // 768 + ((yy // 24) % 2) * 25).astype(np.uint8)
    if block_at is not None:
        porch[120:380, block_at:block_at + 120] = 20
    img[168:600] = porch[..., None]
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buf.tobytes()


EMPTY = jpeg()


class FakeNet:
    """Stands in for cv2.dnn: one anchor on one level carries a box. `level` 0/1/2 = stride 8/16/32; `bins`
    = (l, t, r, b) in stride units, encoded as one-hot logits the way NanoDet's distribution head emits them."""

    def __init__(self, level, bins, score, cls=0):
        self.level, self.bins, self.score, self.cls = level, bins, score, cls

    def setInput(self, blob):
        self.blob = blob

    def forward(self, _names):
        outs = []
        for i, s in enumerate((8, 16, 32)):
            n = (416 // s) ** 2
            scores = np.full((1, n, 80), 0.01, np.float32)
            reg = np.zeros((1, n, 32), np.float32)
            if i == self.level:
                scores[0, n // 2, self.cls] = self.score
                for side, b in enumerate(self.bins):
                    reg[0, n // 2, side * 8 + b] = 30.0
            outs += [scores, reg]
        return outs


def faked(net):
    d = halloween.PersonDetector()
    d._cv2, d._np, d._net, d._outs = cv2, np, net, []
    return d


print("\n=== decoding: class 0 only, and only people tall enough to be at the door ===")
tall, small = (2, 7, 2, 7), (1, 2, 1, 2)   # stride 32: 448px of 416 -> whole frame; stride 8: 32px -> 0.08
check("tall person, 0.9 -> person", faked(FakeNet(2, tall, 0.9))(EMPTY), True)
d = faked(FakeNet(2, tall, 0.9))
d(EMPTY)
check("...and its score is reported for the log line", round(d.last_score, 2), 0.9)
check("small figure (a reflection in the glass), 0.9 -> no", faked(FakeNet(0, small, 0.9))(EMPTY), False)
check("tall CAR, 0.9 -> no (only class 0 counts)", faked(FakeNet(2, tall, 0.9, cls=2))(EMPTY), False)
check("tall person but only 0.3 sure -> no", faked(FakeNet(2, tall, 0.3))(EMPTY), False)

print("\n=== the real model: loads, runs, and calls an empty porch empty ===")
real = halloween.PersonDetector()
check("models/person_nanodet.onnx loads", real.load(), True)
check("empty porch -> no person", real(EMPTY), False)
check("a dark block is not a person", real(jpeg(block_at=300)), False)
check("a frame that won't decode -> no person, no crash", real(b"not a jpeg"), False)
missing = halloween.PersonDetector(path=os.path.join(os.path.dirname(halloween.PERSON_MODEL), "nope.onnx"))
check("missing model -> load() says no (and logs why once)", missing.load(), False)
check("...and every frame reads as no person, no crash", missing(EMPTY), False)


def doorway(frames, person=(), faces=(), held=lambda n: False):
    """Run check_doorway over a scripted camera. frames: list of JPEGs (None = capture failed); the last one
    repeats. person / faces: attempt numbers where that detector fires. held(n): sensor still HIGH?"""
    NOW[0] = 0.0
    n = [0]

    def capture():
        n[0] += 1
        return frames[min(n[0], len(frames)) - 1]

    return halloween.check_doorway(capture, lambda _j: n[0] in faces, still_present=lambda: held(n[0]),
                                   has_person=lambda _j: n[0] in person, sleep=sleep, clock=clock)


print("\n=== check_doorway: only 'seen' may spook ===")
v, look = doorway([EMPTY])
check("nobody, sensor released -> nobody", v, "nobody")
check("...after exactly the LOOK_FRAMES floor", look["attempts"], halloween.LOOK_FRAMES)
v, look = doorway([EMPTY], person=(3,))
check("person walks into frame on frame 3 -> seen", (v, look["attempts"]), ("seen", 3))
v, look = doorway([EMPTY], faces=(1,))
check("a face on frame 1 -> seen at once", (v, look["attempts"]), ("seen", 1))
v, look = doorway([None])
check("camera returns nothing -> blind, NOT seen (no spook without the camera)", v, "blind")
v, look = doorway([EMPTY], held=lambda n: True)
check("PIR stuck HIGH at an empty porch -> still nobody", v, "nobody")
check("...and the look is bounded by LOOK_MAX_S", NOW[0] <= halloween.LOOK_MAX_S + 1.0, True)
v, look = doorway([EMPTY], person=(7,), held=lambda n: n < 7)
check("person reaches the frame while the PIR still asserts -> seen", (v, look["attempts"]), ("seen", 7))

print("\n=== env ===")
saved = {k: os.environ.pop(k, None) for k in ("ROBOT_HALLOWEEN_CAMERA_CHECK", "ROBOT_HALLOWEEN_PERSON_SCORE",
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
check("person score defaults to 0.5", halloween.person_score(), 0.5)
os.environ["ROBOT_HALLOWEEN_CAMERA_CHECK"] = "0"
check("ROBOT_HALLOWEEN_CAMERA_CHECK=0 turns it off", halloween.camera_check(), False)
os.environ["ROBOT_HALLOWEEN_PERSON_SCORE"] = "junk"
check("junk score falls back to 0.5", halloween.person_score(), 0.5)
os.environ["ROBOT_HALLOWEEN_PERSON_SCORE"] = "7"
check("score is capped at 1.0", halloween.person_score(), 1.0)
os.environ["ROBOT_HALLOWEEN_PERSON_SCORE"] = "0.3"
check("a lower score lets the 0.3 box through", faked(FakeNet(2, tall, 0.35))(EMPTY), True)
for k, val in saved.items():
    os.environ.pop(k, None)
    if val is not None:
        os.environ[k] = val

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
