#!/usr/bin/env bash
# slice_image.sh -- slice grid / contact-sheet image(s) (e.g. people/people_all_01.png) into individual cell
# images (1.jpg, 2.jpg, ...) under people/people_individual/. Then rename each to the person's name and enrol
# them with ./scripts/enroll_person.sh. Run on YOUR PC -- it needs Pillow, same as generate_faces.sh
# (./scripts/host_setup.sh installs it). Cells are numbered left-to-right, top-to-bottom and CONTINUE across
# sheets/runs after the highest number already in the output folder -- so several sheets share ONE sequence
# (1..32, 33..64, ...) instead of overwriting each other. Pass --start 1 to restart from the top + overwrite.
#
#   ./scripts/slice_image.sh                                       # ALL people/people_all*.png (sorted), 8x4
#   ./scripts/slice_image.sh people/people_all_01.png              # just one sheet
#   ./scripts/slice_image.sh people/people_all_*.png --rows 8 --cols 4   # several sheets, one continuous numbering
#   ./scripts/slice_image.sh people/people_all_01.png --start 1    # restart numbering, overwrite from the top
#   ./scripts/slice_image.sh sheet.png --rows 6 --cols 5 --out people/people_individual --ext png
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Pick a Python that has Pillow (same order as generate_faces.sh): PC authoring venv, Pi brain venv, system.
PY=""
for cand in "$HOME/.localairobot-tools/venv/bin/python" "$REPO/brain/.venv/bin/python" \
            "$(command -v python3 || true)" "$(command -v python || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c 'import PIL' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "No Python with Pillow found. Run  ./scripts/host_setup.sh  first (it installs it)." >&2
  exit 1
fi

# Python is fed on stdin (the user runs only this .sh); args pass straight through to argparse.
exec "$PY" - "$@" <<'PYEOF'
import argparse, glob, os, re, sys
from PIL import Image

ap = argparse.ArgumentParser(description="Slice grid contact-sheet image(s) into individual cell images.")
ap.add_argument("images", nargs="*",
                help="grid image(s) to slice (default: people/people_all*.png, sorted)")
ap.add_argument("--rows", type=int, default=8, help="rows in each grid (default 8)")
ap.add_argument("--cols", type=int, default=4, help="columns in each grid (default 4)")
ap.add_argument("--out", default="", help="output dir (default: <first image's dir>/people_individual)")
ap.add_argument("--ext", default="jpg", choices=["jpg", "png"], help="output format (default jpg)")
ap.add_argument("--quality", type=int, default=95, help="JPEG quality 1-100 (default 95; keeps faces crisp)")
ap.add_argument("--start", default="auto",
                help="first output number: 'auto' (default) continues AFTER the highest existing N.ext in "
                     "--out so sheets/runs share one sequence; an integer forces it (--start 1 = overwrite from top)")
a = ap.parse_args()

if a.rows < 1 or a.cols < 1:
    sys.exit("--rows and --cols must both be >= 1")

# Images: the ones given, else every people/people_all*.png (sorted, so _01/_02/_03 slice in order).
images = [os.path.expanduser(p) for p in a.images] or sorted(glob.glob("people/people_all*.png"))
if not images:
    sys.exit("no image given and none match people/people_all*.png -- pass a path "
             "(e.g. ./scripts/slice_image.sh people/people_all_01.png).")

out = os.path.expanduser(a.out) if a.out else os.path.join(os.path.dirname(images[0]) or ".", "people_individual")
os.makedirs(out, exist_ok=True)

# Where to begin numbering. 'auto' = one past the highest existing N.ext already in the folder, so more
# sheets/runs CONTINUE the sequence (renamed files, which no longer match \d+\.ext, are ignored + kept).
def _next_index(folder):
    hi = 0
    for f in os.listdir(folder):
        m = re.match(r"(\d+)\.(jpg|jpeg|png)$", f, re.IGNORECASE)
        if m:
            hi = max(hi, int(m.group(1)))
    return hi + 1

if str(a.start).lower() == "auto":
    n = _next_index(out) - 1
else:
    try:
        n = int(a.start) - 1
    except ValueError:
        sys.exit("--start must be 'auto' or an integer")
    if n < 0:
        sys.exit("--start must be >= 1")

first = n + 1                      # the first number we'll actually write
if first > 1:
    print(f"   continuing at {first} (existing numbered images in {out} are kept; pass --start 1 to overwrite).")

for path in images:
    try:
        img = Image.open(path)
    except (FileNotFoundError, OSError) as e:
        print(f"   [skip] cannot open {path!r}: {e}", file=sys.stderr); continue
    if a.ext == "jpg" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")      # JPEG has no alpha channel
    width, height = img.size
    w_step, h_step = width // a.cols, height // a.rows
    if w_step == 0 or h_step == 0:
        print(f"   [skip] {path} ({width}x{height}) too small for a {a.rows}x{a.cols} grid", file=sys.stderr); continue
    sheet_first = n + 1
    for r in range(a.rows):
        for c in range(a.cols):
            left, top = c * w_step, r * h_step
            # last column/row absorbs leftover pixels (size not evenly divisible), so an edge face isn't clipped
            right = width if c == a.cols - 1 else left + w_step
            bottom = height if r == a.rows - 1 else top + h_step
            cell = img.crop((left, top, right, bottom))
            n += 1
            dest = os.path.join(out, f"{n}.{a.ext}")
            if a.ext == "jpg":
                cell.save(dest, "JPEG", quality=a.quality)
            else:
                cell.save(dest)
    print(f"   {os.path.basename(path)} ({width}x{height}) -> {sheet_first}.{a.ext} .. {n}.{a.ext}  ({a.rows}x{a.cols})")

if n < first:
    sys.exit("nothing sliced -- no readable images.")
print(f">> done: {n - first + 1} cell(s) from {len(images)} sheet(s) -> {out}/{first}.{a.ext} .. {n}.{a.ext}")
print(f"   Next: rename each to the person, then enrol -- ./scripts/enroll_person.sh \"Ramesh\" \"my creator\" {out}/{first}.{a.ext}")
PYEOF
