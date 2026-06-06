# brain/face.py -- the robot's face: an expression state machine on a small display.
#
# main.py / behavior.py call set_expression("listening"|"thinking"|"talking"|"exploring"|
# "happy"|"confused"|"alarmed"|"warmup"|"capturing"|"idle") on events. Each state renders either
# a multi-frame PNG ANIMATION (if assets exist for it) or, as a zero-asset fallback, a procedural
# cartoon face drawn with Pillow. Frames are pushed to a backend:
#
#   turing       -> the 3.5" USB-C panel via mathoudebine/turing-smart-screen-python (serial).
#   framebuffer  -> a DSI/HDMI panel, PIL frame written straight to /dev/fb0 as RGB565
#                   (NOT pygame/SDL -- KMSDRM is broken on headless Pi 5).
#   simulated    -> no display: logs "FACE -> <state>" on change (and optionally dumps a PNG).
#   auto         -> try turing, then framebuffer, else simulated.
#
# ANIMATION: drop PNG sequences in <ROBOT_FACE_FRAMES_DIR>/<state>/NN.png (default brain/faces).
# A state with >=2 frames animates on a background daemon thread (talking is fast + random-frame
# to fake a moving mouth, like the reference); 1 frame = a static still; 0 frames = the procedural
# drawing. With NO faces/ directory present, behaviour is identical to the old procedural face.
#
# EVERYTHING here is best-effort and never raises into the caller: a missing panel, missing Pillow,
# or an unreadable asset degrades to simulated / procedural and the robot runs on. The face is
# cosmetic; it must never block driving.

import logging
import os
import random
import re
import threading

from driver import dry_run_enabled

log = logging.getLogger("robot.face")

STATES = ("idle", "listening", "thinking", "talking", "exploring",
          "happy", "confused", "alarmed", "warmup", "capturing")

# Per-mood drawing params: bg tint, eye colour, pupil look (dx,dy in -1..1), mouth style,
# and flags for happy (^^) eyes / wide (startled) eyes / drooped lids.
_MOODS = {
    "idle":      dict(bg=(18, 22, 30),  eye=(120, 200, 255), look=(0.0, 0.05),  mouth="smile_s"),
    "listening": dict(bg=(14, 30, 42),  eye=(120, 220, 255), look=(0.0, 0.0),   mouth="o", wide=True),
    "thinking":  dict(bg=(24, 22, 36),  eye=(150, 170, 255), look=(0.35, -0.4), mouth="flat", lid=0.35),
    "talking":   dict(bg=(14, 28, 40),  eye=(120, 210, 255), look=(0.0, 0.0),   mouth="open"),
    "exploring": dict(bg=(14, 32, 26),  eye=(140, 240, 180), look=(0.6, 0.0),   mouth="flat"),
    "happy":     dict(bg=(20, 34, 22),  eye=(150, 255, 160), look=(0.0, 0.0),   mouth="smile_b", happy=True),
    "confused":  dict(bg=(36, 28, 14),  eye=(255, 210, 120), look=(-0.25, -0.1),mouth="wavy", lid=0.3),
    "alarmed":   dict(bg=(46, 14, 14),  eye=(255, 120, 120), look=(0.0, 0.0),   mouth="open", wide=True),
    # added states: warmup (model loading at boot) + capturing (camera grab before a vision look)
    "warmup":    dict(bg=(20, 20, 34),  eye=(160, 180, 255), look=(0.0, 0.0),   mouth="flat", lid=0.2),
    "capturing": dict(bg=(12, 30, 38),  eye=(150, 230, 255), look=(0.0, -0.1),  mouth="o", wide=True),
}

# Per-state animation timing, used ONLY when a state has >=2 PNG frames: (fps, loop).
# talking is the one state where higher fps earns its keep (it tracks speech); the others are a
# cheap slow loop. loop=False holds the last frame (good for brief transient states).
_TIMING = {
    "talking":   (15.0, True),
    "thinking":  (3.0, True),
    "listening": (3.0, True),
    "exploring": (3.0, True),
    "warmup":    (2.0, True),
    "capturing": (2.0, False),
    "happy":     (4.0, False),
    "confused":  (3.0, False),
    "alarmed":   (4.0, True),
}
_DEFAULT_TIMING = (2.0, False)


def _frame_key(name):
    """Sort frames by the integer in the filename: tolerates 'thinking 04.png' and '04.png'."""
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 0, name)


def _render(state, w, h):
    """Procedural cartoon face -> PIL.Image, or None if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    m = _MOODS.get(state, _MOODS["idle"])
    img = Image.new("RGB", (w, h), m["bg"])
    d = ImageDraw.Draw(img)
    eye = m["eye"]
    wide = m.get("wide", False)
    er = h * (0.16 if wide else 0.13)        # eye radius
    cy = h * 0.36                            # eye centre y
    lx, rx = w * 0.33, w * 0.67              # eye centre x (left/right)
    dx, dy = m["look"]

    def draw_eye(cx):
        if m.get("happy"):                   # ^^ happy eyes = upward arcs
            d.arc([cx - er, cy - er, cx + er, cy + er], start=200, end=340, fill=eye, width=max(3, int(er * 0.35)))
            return
        d.ellipse([cx - er, cy - er, cx + er, cy + er], fill=eye)          # eyeball
        pr = er * 0.45                                                      # pupil
        px, py = cx + dx * er * 0.5, cy + dy * er * 0.5
        d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(12, 14, 20))
        lid = m.get("lid", 0.0)                                            # drooped/raised lid
        if lid:
            d.rectangle([cx - er, cy - er, cx + er, cy - er + 2 * er * lid], fill=m["bg"])

    draw_eye(lx)
    draw_eye(rx)

    # mouth
    mx, my = w * 0.5, h * 0.66
    mw = w * 0.24
    style = m["mouth"]
    line = max(3, int(h * 0.012))
    if style == "smile_s":
        d.arc([mx - mw, my - mw * 0.5, mx + mw, my + mw * 0.9], 20, 160, fill=eye, width=line)
    elif style == "smile_b":
        d.arc([mx - mw, my - mw * 0.7, mx + mw, my + mw * 1.1], 10, 170, fill=eye, width=line + 2)
    elif style == "flat":
        d.line([mx - mw * 0.7, my, mx + mw * 0.7, my], fill=eye, width=line)
    elif style == "o":
        r = mw * 0.45
        d.ellipse([mx - r, my - r, mx + r, my + r], outline=eye, width=line)
    elif style == "open":
        d.ellipse([mx - mw * 0.6, my - mw * 0.5, mx + mw * 0.6, my + mw * 0.7], fill=(20, 12, 16), outline=eye, width=line)
    elif style == "wavy":
        pts, n = [], 6
        for i in range(n + 1):
            xx = mx - mw + (2 * mw) * i / n
            yy = my + (mw * 0.18 if i % 2 else -mw * 0.18)
            pts.append((xx, yy))
        d.line(pts, fill=eye, width=line)

    d.text((6, h - 16), state, fill=(150, 150, 160))   # tiny label (handy on the bench)
    return img


class _SimulatedBackend:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self._dump = os.environ.get("ROBOT_FACE_DUMP", "").strip()  # optional dir to save PNGs
        self._last = None
        log.info("face backend: simulated (no panel; logs expressions on change%s)",
                 f", dumps PNGs to {self._dump}" if self._dump else "")

    def show(self, state, img):
        # Log + dump only on a STATE CHANGE so animation frames don't flood the console / disk.
        if state == self._last:
            return
        self._last = state
        log.info("FACE -> %s", state)
        if self._dump and img is not None:
            try:
                os.makedirs(self._dump, exist_ok=True)
                img.save(os.path.join(self._dump, f"face_{state}.png"))
            except Exception:
                pass

    def close(self):
        pass


class _TuringBackend:
    """USB-C panel via mathoudebine/turing-smart-screen-python (serial protocol)."""

    def __init__(self, w, h, port, revision, brightness, libdir):
        import sys
        sys.path.insert(0, os.path.expanduser(libdir))
        rev = (revision or "A").upper()
        if rev == "B":
            from library.lcd.lcd_comm_rev_b import LcdCommRevB as Lcd
        else:
            from library.lcd.lcd_comm_rev_a import LcdCommRevA as Lcd
        # Native portrait orientation (width <= height); render to that.
        self.width, self.height = min(w, h), max(w, h)
        self._lcd = Lcd(com_port=(port or "AUTO"), display_width=self.width, display_height=self.height)
        self._lcd.Reset()
        self._lcd.InitializeComm()
        try:
            self._lcd.SetBrightness(level=brightness)
        except Exception:
            pass
        log.info("face backend: turing USB-C panel rev%s (%dx%d, port=%s)",
                 rev, self.width, self.height, port or "AUTO")

    def show(self, state, img):
        if img is not None:
            self._lcd.DisplayPILImage(img)

    def close(self):
        try:
            self._lcd.closeSerial()
        except Exception:
            pass


class _FramebufferBackend:
    """DSI/HDMI panel: write the PIL frame to /dev/fb0 as RGB565 (no X, no pygame)."""

    def __init__(self, w, h, dev="/dev/fb0"):
        import numpy as np
        self.dev = dev
        try:
            with open("/sys/class/graphics/fb0/virtual_size") as f:
                vw, vh = (int(x) for x in f.read().strip().split(","))
            self.width, self.height = vw, vh
        except Exception:
            self.width, self.height = w, h
        # Probe writability early so auto() can fall back if it fails.
        open(self.dev, "r+b").close()
        # Map the framebuffer ONCE and reuse it -- under animation, re-opening a memmap every
        # frame (the old behaviour) is needless churn. Per-frame show() is now just pack + write.
        self._np = np
        self._fb = np.memmap(self.dev, dtype="uint16", mode="r+", shape=(self.height, self.width))
        log.info("face backend: framebuffer %s (%dx%d RGB565, persistent map)",
                 self.dev, self.width, self.height)

    def show(self, state, img):
        if img is None or self._fb is None:
            return
        np = self._np
        a = np.asarray(img.resize((self.width, self.height)).convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
        self._fb[:] = rgb565

    def close(self):
        self._fb = None


class Face:
    """Expression state machine with optional PNG-sequence animation. set_expression() is
    best-effort and never raises; the animator runs on a daemon thread."""

    def __init__(self):
        self.enabled = os.environ.get("ROBOT_FACE_ENABLE", "1") == "1"
        self.dry_run = dry_run_enabled()
        self.backend = None
        self._state = None
        # animation state (guarded by _anim_lock; _wake interrupts the frame-wait on a change)
        self._anim_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._frame_index = 0
        self._frame_cache = {}
        self._max_fps = 30.0
        self._frames_dir = os.path.expanduser(
            os.environ.get("ROBOT_FACE_FRAMES_DIR", "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "faces"))
        try:
            self._fps = float(os.environ.get("ROBOT_FACE_FPS", "4") or 4)
        except ValueError:
            self._fps = 4.0

        if not self.enabled:
            log.info("face disabled (ROBOT_FACE_ENABLE=0)")
            return

        w = int(os.environ.get("ROBOT_FACE_WIDTH", "320") or 320)
        h = int(os.environ.get("ROBOT_FACE_HEIGHT", "480") or 480)
        want = os.environ.get("ROBOT_FACE_BACKEND", "auto").lower()
        port = os.environ.get("ROBOT_FACE_PORT", "AUTO")
        rev = os.environ.get("ROBOT_FACE_REVISION", "A")
        libdir = os.environ.get("ROBOT_FACE_LIB", "~/turing-smart-screen-python")
        try:
            bright = int(os.environ.get("ROBOT_FACE_BRIGHTNESS", "80") or 80)
        except ValueError:
            bright = 80

        if self.dry_run or want == "simulated":
            self.backend = _SimulatedBackend(w, h)
        elif want == "turing":
            self.backend = self._try(lambda: _TuringBackend(w, h, port, rev, bright, libdir)) \
                or _SimulatedBackend(w, h)
        elif want == "framebuffer":
            self.backend = self._try(lambda: _FramebufferBackend(w, h)) or _SimulatedBackend(w, h)
        else:  # auto
            self.backend = (self._try(lambda: _TuringBackend(w, h, port, rev, bright, libdir))
                            or self._try(lambda: _FramebufferBackend(w, h))
                            or _SimulatedBackend(w, h))

        # Serial-bound turing panel can't sustain high fps; clamp it. Framebuffer/simulated are fine.
        self._max_fps = 8.0 if isinstance(self.backend, _TuringBackend) else 30.0

        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()
        self.set_expression("idle")

    @staticmethod
    def _try(make):
        try:
            return make()
        except Exception as e:
            log.warning("face backend init failed (%s) -- trying next", e)
            return None

    def set_expression(self, state):
        if not self.enabled or self.backend is None:
            return
        if state not in _MOODS:
            state = "idle"
        with self._anim_lock:
            if state == self._state:
                return            # same state -> no restart, no flicker
            self._state = state
            self._frame_index = 0
        self._wake.set()          # interrupt the animator's wait so it picks up the new state now

    # ---------- animation ----------
    def _animate(self):
        while not self._stop.is_set():
            with self._anim_lock:
                state = self._state
                idx = self._frame_index
            if state is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            frames = self._frames_for(state)
            if frames and len(frames) >= 2:
                self._show(state, frames[idx % len(frames)])
                fps, loop = self._timing(state)
                with self._anim_lock:
                    if self._state == state:   # don't clobber a state change that just landed
                        self._frame_index = self._next_index(state, idx, len(frames), loop)
                if self._wake.wait(1.0 / max(0.5, fps)):
                    self._wake.clear()
            else:
                # single frame -> static still; no frames -> procedural drawing. Either way, render
                # once and block until the next state change (no busy spin, no log/serial spam).
                self._show(state, frames[0] if frames else _render(state, self.backend.width, self.backend.height))
                self._wake.wait()
                self._wake.clear()
        self._stop.clear()

    def _next_index(self, state, idx, n, loop):
        if state == "talking" and n > 1:
            return random.randint(1, n - 1)   # random mouth frame -> looks like speech
        nxt = idx + 1
        if nxt >= n:
            return 0 if loop else n - 1        # loop, or hold the last frame
        return nxt

    def _timing(self, state):
        tbl_fps, loop = _TIMING.get(state, _DEFAULT_TIMING)
        fps = tbl_fps if state == "talking" else self._fps   # global ROBOT_FACE_FPS; talking faster
        return min(fps, self._max_fps), loop

    def _frames_for(self, state):
        if state not in self._frame_cache:
            self._frame_cache[state] = self._load_frames(state)
        return self._frame_cache[state]

    def _load_frames(self, state):
        d = os.path.join(self._frames_dir, state)
        if not os.path.isdir(d):
            return []
        try:
            from PIL import Image
            files = sorted([f for f in os.listdir(d) if f.lower().endswith(".png")], key=_frame_key)
            frames = []
            for f in files:
                img = Image.open(os.path.join(d, f)).convert("RGB").resize(
                    (self.backend.width, self.backend.height))
                frames.append(img)
            if frames:
                log.info("face: loaded %d frame(s) for '%s' from %s", len(frames), state, d)
            return frames
        except Exception as e:
            log.warning("face: frame load failed for '%s' (%s) -- using procedural", state, e)
            return []

    def _show(self, state, img):
        try:
            self.backend.show(state, img)
        except Exception as e:
            log.warning("face render/show failed (%s)", e)

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self.backend is not None:
            try:
                self.backend.show("idle", _render("idle", self.backend.width, self.backend.height))
            except Exception:
                pass
            try:
                self.backend.close()
            except Exception:
                pass
