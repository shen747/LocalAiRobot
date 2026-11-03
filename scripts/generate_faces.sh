#!/usr/bin/env bash
# generate_faces.sh -- render the robot's built-in procedural face to editable PNG *starting templates*
# (one per state) under brain/faces/<state>/01.png. Edit them, add 02.png+ to animate, then commit.
# Run this ON YOUR PC after ./scripts/host_setup.sh (it installs the Pillow it needs).
#
#   ./scripts/generate_faces.sh                  # 480x320 (USB-C panel, landscape); skips frames you already have
#   ./scripts/generate_faces.sh --size 800x480   # Freenove 5" DSI panel
#   ./scripts/generate_faces.sh --force          # overwrite existing 01.png
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Pick a Python that has Pillow -- the user never has to. Prefer the PC authoring venv (host_setup.sh),
# then the Pi brain venv, then a system python. No Pillow anywhere -> tell them to run host_setup.sh.
PY=""
for cand in "$HOME/.localairobot-tools/venv/bin/python" "$REPO/brain/.venv/bin/python" \
            "$(command -v python3 || true)" "$(command -v python || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c 'import PIL' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "No Python with Pillow found. Run  ./scripts/host_setup.sh  first (it installs it)." >&2
  exit 1
fi

# The renderer lives in brain/face.py; we reuse it so templates are exactly on-model. Python is fed on
# stdin (the user runs only this .sh), with the repo path passed via the environment.
LAR_REPO="$REPO" "$PY" - "$@" <<'PYEOF'
import argparse, os, sys
repo = os.environ["LAR_REPO"]
ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(repo, "brain", "faces"))
ap.add_argument("--size", default="480x320", help="WxH: 480x320 USB-C panel (landscape), 800x480 Freenove DSI")
ap.add_argument("--force", action="store_true")
a = ap.parse_args()
try:
    w, h = (int(x) for x in a.size.lower().split("x"))
except ValueError:
    sys.exit("--size must be WxH, e.g. 320x480")
sys.path.insert(0, os.path.join(repo, "brain"))
import face                                    # imports cleanly: no hardware deps at import time
made = skipped = 0
for state in face.STATES:
    d = os.path.join(a.out, state)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "01.png")
    if os.path.exists(p) and not a.force:
        skipped += 1
        continue
    img = face._render(state, w, h)
    if img is None:
        sys.exit("Pillow unavailable -- run ./scripts/host_setup.sh")
    img.save(p)
    made += 1
    print("  wrote", os.path.relpath(p, a.out))
print(f">> {made} written, {skipped} kept ({w}x{h}). Edit them / add 02.png+ to animate, then commit.")
PYEOF
