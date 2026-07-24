#!/usr/bin/env bash
# generate_halloween_faces.sh -- draw the 10 Halloween "spook" faces into brain/faces/halloween/01..10.png.
# These are shown (one at random per trick-or-treater) when ROBOT_HALLOWEEN_MODE=1 (a Concierge sub-mode).
# Run this ON YOUR PC after ./scripts/host_setup.sh (it installs the Pillow this needs), then commit the PNGs.
#
#   ./scripts/generate_halloween_faces.sh                 # 480x320 (USB-C panel, landscape); skips existing
#   ./scripts/generate_halloween_faces.sh --size 800x480  # Freenove 5" DSI panel
#   ./scripts/generate_halloween_faces.sh --force         # overwrite existing frames
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Pick a Python with Pillow (PC authoring venv, then the Pi brain venv, then system) -- same as generate_faces.sh.
PY=""
for cand in "$HOME/.localairobot-tools/venv/bin/python" "$REPO/brain/.venv/bin/python" \
            "$(command -v python3 || true)" "$(command -v python || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c 'import PIL' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "No Python with Pillow found. Run  ./scripts/host_setup.sh  first (it installs it)." >&2
  exit 1
fi

LAR_REPO="$REPO" "$PY" - "$@" <<'PYEOF'
import argparse, math, os, random
from PIL import Image, ImageDraw

repo = os.environ["LAR_REPO"]
ap = argparse.ArgumentParser()
ap.add_argument("--size", default="480x320", help="WxH, e.g. 480x320 (USB-C) or 800x480 (DSI)")
ap.add_argument("--force", action="store_true", help="overwrite existing frames")
ap.add_argument("--out", default=os.path.join(repo, "brain", "faces", "halloween"))
a = ap.parse_args()
w, h = (int(v) for v in a.size.lower().split("x"))
os.makedirs(a.out, exist_ok=True)
random.seed(31)   # deterministic frames (the glitch noise is reproducible run-to-run)

def _bg(d, color):
    d.rectangle([0, 0, w, h], fill=color)

def _jagged(d, cx, my, mw, depth, color, fill=True, teeth=6):
    top = my - depth * 0.25
    pts = [(cx - mw, top)]
    for k in range(teeth * 2 + 1):
        x = cx - mw + (2 * mw) * k / (teeth * 2)
        pts.append((x, my + (depth if k % 2 else 0)))
    pts.append((cx + mw, top))
    if fill:
        d.polygon(pts, fill=color)
    else:
        d.line(pts, fill=color, width=max(2, int(h * 0.011)))

# ---- the 10 monsters (each draws its own themed background + creature, scaled to w,h) -------------------
def pumpkin(d):
    _bg(d, (22, 11, 3)); cx, cy = w / 2, h * 0.57; rx, ry = min(w, h) * 0.42, min(w, h) * 0.35
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(240, 130, 22))
    for f in (-0.5, 0.0, 0.5):
        d.ellipse([cx - rx * 0.22 + f * rx, cy - ry, cx + rx * 0.22 + f * rx, cy + ry],
                  outline=(200, 92, 10), width=max(2, int(w * 0.006)))
    d.rectangle([cx - rx * 0.08, cy - ry - h * 0.10, cx + rx * 0.08, cy - ry + h * 0.03], fill=(66, 96, 34))
    ey = cy - ry * 0.28
    for ex in (cx - rx * 0.42, cx + rx * 0.42):
        s = rx * 0.24
        d.polygon([(ex, ey - s), (ex - s, ey + s), (ex + s, ey + s)], fill=(22, 11, 3))
    s = rx * 0.18
    d.polygon([(cx, cy - ry * 0.02), (cx - s, cy + s), (cx + s, cy + s)], fill=(22, 11, 3))   # nose
    _jagged(d, cx, cy + ry * 0.42, rx * 0.78, ry * 0.22, (22, 11, 3), teeth=6)

def ghost(d):
    _bg(d, (18, 20, 34)); cx = w / 2; top = h * 0.16; rx = min(w, h) * 0.33; bot = h * 0.9
    d.pieslice([cx - rx, top, cx + rx, top + 2 * rx], 180, 360, fill=(238, 240, 248))
    d.rectangle([cx - rx, top + rx, cx + rx, bot - rx * 0.4], fill=(238, 240, 248))
    n = 5; ww = 2 * rx / n
    for k in range(n):
        x0 = cx - rx + k * ww
        d.pieslice([x0, bot - rx * 0.8, x0 + ww, bot], 0, 180, fill=(18, 20, 34))
    er = rx * 0.17
    for ex in (cx - rx * 0.4, cx + rx * 0.4):
        d.ellipse([ex - er, top + rx * 0.55 - er, ex + er, top + rx * 0.55 + er * 1.3], fill=(18, 20, 34))
    d.ellipse([cx - er * 0.8, top + rx * 1.15 - er, cx + er * 0.8, top + rx * 1.15 + er * 1.5], fill=(18, 20, 34))

def skull(d):
    _bg(d, (14, 14, 18)); cx, cy = w / 2, h * 0.44; rx, ry = min(w, h) * 0.32, min(w, h) * 0.34
    bone = (236, 233, 222)
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry * 0.7], fill=bone)
    d.ellipse([cx - rx * 0.62, cy + ry * 0.15, cx + rx * 0.62, cy + ry * 1.05], fill=bone)
    er = rx * 0.32
    for ex in (cx - rx * 0.42, cx + rx * 0.42):
        d.ellipse([ex - er, cy - er * 0.7, ex + er, cy + er * 1.1], fill=(10, 10, 14))
    d.polygon([(cx, cy + ry * 0.12), (cx - rx * 0.12, cy + ry * 0.45), (cx + rx * 0.12, cy + ry * 0.45)], fill=(10, 10, 14))
    ty0, ty1 = cy + ry * 0.58, cy + ry * 0.95
    for k in range(6):
        x0 = cx - rx * 0.45 + k * (rx * 0.9 / 6)
        d.rectangle([x0, ty0, x0 + rx * 0.9 / 6 - 1, ty1], outline=(10, 10, 14), width=max(1, int(w * 0.004)))

def red_eyes(d):
    _bg(d, (4, 3, 6)); cx, cy = w / 2, h * 0.42; er = min(w, h) * 0.1
    for ex in (cx - w * 0.16, cx + w * 0.16):
        for rr, col in ((er * 1.7, (55, 0, 0)), (er, (205, 22, 22)), (er * 0.5, (255, 130, 100))):
            d.ellipse([ex - rr, cy - rr, ex + rr, cy + rr], fill=col)
    _jagged(d, cx, h * 0.66, w * 0.22, h * 0.03, (120, 22, 22), fill=False, teeth=7)

def cheshire(d):
    _bg(d, (24, 8, 30)); cx, cy = w / 2, h * 0.52; gw, gy = w * 0.36, h * 0.6
    d.chord([cx - gw, gy - h * 0.22, cx + gw, gy + h * 0.22], 8, 172, fill=(228, 40, 60))
    n = 8; step = gw * 1.72 / n
    for k in range(n):
        x0 = cx - gw * 0.86 + k * step
        d.polygon([(x0, gy), (x0 + step, gy), (x0 + step / 2, gy - h * 0.06)], fill=(246, 246, 236))
        d.polygon([(x0, gy), (x0 + step, gy), (x0 + step / 2, gy + h * 0.06)], fill=(246, 246, 236))
    er = min(w, h) * 0.12; eyy = cy - h * 0.28
    for ex in (cx - w * 0.16, cx + w * 0.16):
        d.ellipse([ex - er, eyy - er, ex + er, eyy + er], fill=(250, 210, 40))
        d.ellipse([ex - er * 0.32, eyy - er * 0.6, ex + er * 0.32, eyy + er], fill=(24, 8, 30))
        d.rectangle([ex - er, eyy - er, ex + er, eyy - er * 0.25], fill=(24, 8, 30))

def spider(d):
    _bg(d, (10, 10, 14)); cx, cy = w / 2, h * 0.56; body = min(w, h) * 0.2
    d.line([(cx, 0), (cx, cy - body * 1.6)], fill=(90, 90, 102), width=max(1, int(w * 0.004)))
    d.ellipse([cx - body, cy - body * 0.8, cx + body, cy + body * 1.2], fill=(20, 18, 26))
    hr = body * 0.6
    d.ellipse([cx - hr, cy - body * 1.5, cx + hr, cy - body * 0.3], fill=(28, 24, 34))
    for s in (-1, 1):
        for k in range(4):
            ay = cy - body * 0.4 + k * body * 0.42
            knee = (cx + s * body * 1.5, ay - body * 0.35)
            foot = (cx + s * body * 2.3, ay + body * 0.5)
            d.line([(cx + s * hr * 0.5, ay), knee, foot], fill=(20, 18, 26),
                   width=max(2, int(w * 0.006)), joint="curve")
    for ex in (cx - hr * 0.4, cx + hr * 0.4):
        d.ellipse([ex - hr * 0.2, cy - body * 1.15, ex + hr * 0.2, cy - body * 0.8], fill=(230, 40, 40))

def bat(d):
    _bg(d, (12, 10, 22)); cx, cy = w / 2, h * 0.46; br = min(w, h) * 0.15
    for s in (-1, 1):
        wx = cx + s * w * 0.42
        d.polygon([(cx + s * br * 0.6, cy - br * 0.4), (wx, cy - br * 1.2), (cx + s * w * 0.30, cy),
                   (wx, cy + br * 0.5), (cx + s * w * 0.20, cy + br * 0.2), (cx + s * br * 0.6, cy + br)],
                  fill=(16, 14, 22))
    d.ellipse([cx - br * 0.7, cy - br, cx + br * 0.7, cy + br * 1.4], fill=(16, 14, 22))
    d.polygon([(cx - br * 0.5, cy - br), (cx - br * 0.75, cy - br * 1.8), (cx - br * 0.12, cy - br)], fill=(16, 14, 22))
    d.polygon([(cx + br * 0.5, cy - br), (cx + br * 0.75, cy - br * 1.8), (cx + br * 0.12, cy - br)], fill=(16, 14, 22))
    for ex in (cx - br * 0.32, cx + br * 0.32):
        d.ellipse([ex - br * 0.17, cy - br * 0.35, ex + br * 0.17, cy + br * 0.05], fill=(230, 55, 55))
    d.polygon([(cx - br * 0.2, cy + br * 0.6), (cx - br * 0.1, cy + br * 0.95), (cx, cy + br * 0.6)], fill=(246, 246, 236))
    d.polygon([(cx + br * 0.2, cy + br * 0.6), (cx + br * 0.1, cy + br * 0.95), (cx, cy + br * 0.6)], fill=(246, 246, 236))

def frankenstein(d):
    _bg(d, (10, 16, 12)); cx, cy = w / 2, h * 0.53; fw, fh = min(w, h) * 0.33, min(w, h) * 0.4
    d.rectangle([cx - fw, cy - fh, cx + fw, cy + fh], fill=(120, 172, 100))
    d.rectangle([cx - fw, cy - fh, cx + fw, cy - fh * 0.5], fill=(30, 26, 24))
    for s in (-1, 1):
        bx0, bx1 = sorted([cx + s * fw, cx + s * (fw + w * 0.05)])   # left bolt: x1<x0 -> sort (Pillow 12 is strict)
        d.rectangle([bx0, cy - fh * 0.12, bx1, cy + fh * 0.12], fill=(122, 122, 132))
    d.rectangle([cx - fw * 0.72, cy - fh * 0.22, cx + fw * 0.72, cy - fh * 0.12], fill=(30, 26, 24))
    er = fw * 0.16
    for ex in (cx - fw * 0.4, cx + fw * 0.4):
        d.ellipse([ex - er, cy - fh * 0.02, ex + er, cy - fh * 0.02 + 2 * er], fill=(18, 18, 18))
    d.line([(cx - fw * 0.5, cy + fh * 0.46), (cx + fw * 0.5, cy + fh * 0.46)], fill=(30, 26, 24), width=max(2, int(w * 0.006)))
    for k in range(5):
        x = cx - fw * 0.4 + k * (fw * 0.8 / 4)
        d.line([(x, cy + fh * 0.39), (x, cy + fh * 0.53)], fill=(30, 26, 24), width=max(1, int(w * 0.004)))

def vampire(d):
    _bg(d, (14, 10, 18)); cx, cy = w / 2, h * 0.52; fw, fh = min(w, h) * 0.3, min(w, h) * 0.37
    d.ellipse([cx - fw, cy - fh, cx + fw, cy + fh], fill=(226, 222, 228))
    d.pieslice([cx - fw, cy - fh, cx + fw, cy], 180, 360, fill=(18, 16, 22))
    d.polygon([(cx - fw, cy - fh * 0.15), (cx, cy + fh * 0.08), (cx + fw, cy - fh * 0.15),
               (cx + fw, cy - fh), (cx - fw, cy - fh)], fill=(18, 16, 22))
    er = fw * 0.16
    for ex in (cx - fw * 0.42, cx + fw * 0.42):
        d.ellipse([ex - er, cy - er, ex + er, cy + er], fill=(212, 40, 40))
    d.arc([cx - fw * 0.4, cy + fh * 0.22, cx + fw * 0.4, cy + fh * 0.6], 20, 160, fill=(120, 20, 30), width=max(2, int(w * 0.006)))
    d.polygon([(cx - fw * 0.18, cy + fh * 0.4), (cx - fw * 0.1, cy + fh * 0.62), (cx - fw * 0.02, cy + fh * 0.4)], fill=(250, 250, 245))
    d.polygon([(cx + fw * 0.18, cy + fh * 0.4), (cx + fw * 0.1, cy + fh * 0.62), (cx + fw * 0.02, cy + fh * 0.4)], fill=(250, 250, 245))

def glitch(d):
    _bg(d, (6, 8, 10))
    for y in range(0, h, 3):
        if random.random() < 0.35:
            d.line([(0, y), (w, y)], fill=random.choice([(0, 255, 180), (255, 0, 120), (60, 60, 80)]), width=1)
    cx, cy = w / 2, h * 0.42; er = min(w, h) * 0.12
    for ex in (cx - w * 0.15, cx + w * 0.15):
        for off, col in ((-6, (255, 0, 80)), (0, (20, 20, 24)), (6, (0, 220, 255))):
            d.rectangle([ex - er + off, cy - er, ex + er + off, cy + er], outline=col, width=max(2, int(w * 0.006)))
    my, mw, n = h * 0.66, w * 0.22, 8
    pts = [(cx - mw + (2 * mw) * k / n, my + (h * 0.04 if k % 2 else -h * 0.04)) for k in range(n + 1)]
    for off, col in ((-4, (255, 0, 80)), (0, (232, 232, 242)), (4, (0, 220, 255))):
        d.line([(x + off, y) for x, y in pts], fill=col, width=max(2, int(w * 0.006)))

FACES = [pumpkin, ghost, skull, red_eyes, cheshire, spider, bat, frankenstein, vampire, glitch]

made = kept = 0
for i, fn in enumerate(FACES, 1):
    path = os.path.join(a.out, "%02d.png" % i)
    if os.path.exists(path) and not a.force:
        kept += 1; print("  kept   %s  (exists -- --force to overwrite)" % path); continue
    img = Image.new("RGB", (w, h), (8, 6, 12))
    fn(ImageDraw.Draw(img))
    img.save(path); made += 1; print("  made   %s  (%s)" % (path, fn.__name__))
print(">> halloween faces: %d made, %d kept  (%dx%d -> %s)" % (made, kept, w, h, a.out))
PYEOF
