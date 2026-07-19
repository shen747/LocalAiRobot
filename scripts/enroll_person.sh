#!/usr/bin/env bash
# enroll_person.sh -- add a person to Concierge Mode's RAG db FROM PHOTO FILES (no live camera needed).
#
# This is the offline / bulk counterpart to the two existing "teach a person" paths:
#   * ./scripts/enroll_face.sh "Name"        -- captures from the LIVE webcam, stores ONLY a face embedding.
#   * "Jarvis, remember this is <Name>..."   -- the voice REMEMBER flow: live photo + VLM description, stores
#                                               a fact but NO face embedding (so it can't greet them by name).
# enroll_person.sh does BOTH, from image files you already have:
#   1. stores a face embedding  -> the concierge greets them BY NAME on a motion wake (table `faces`), and
#   2. stores a name+description fact -> it goes into the LLM prompt context (table `facts`).
# Feed several photos (different angles / lighting) for better recognition; the first one with a detectable
# face is also saved as the reference photo.
#
#   ./scripts/enroll_person.sh "Ramesh" "my creator, wears glasses" ~/pics/ramesh1.jpg ~/pics/ramesh2.jpg
#   ./scripts/enroll_person.sh "Shani"  ""                          shani.png     # empty description is fine
#
# Reads FILES, not the camera, so it is SAFE to run WHILE the robot brain is live (unlike enroll_face.sh,
# which needs the camera). Accepts any format OpenCV can decode (jpg/png/...).
# Prereq: the face model is installed (scripts/provision_face_id.sh or
#         scripts/patches/common/5_face_id_insightface.sh).
# Remove someone later:  ./scripts/rag.sh --forget "Name"   and   ./scripts/rag.sh --query "DELETE FROM faces WHERE name='Name'"
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"

NAME="${1:-}"
DESC="${2:-}"
if [ "$#" -lt 3 ] || [ -z "$NAME" ]; then
  echo "usage: $(basename "$0") \"Name\" \"description\" <image> [image ...]" >&2
  echo "  (pass \"\" for an empty description; give several photos for better matching)" >&2
  exit 2
fi
shift 2
[ -x "$PY" ] || { echo "brain venv not found ($PY) -- run scripts/install_stack_pi5.sh first." >&2; exit 1; }

cd "$REPO/brain"
exec "$PY" - "$NAME" "$DESC" "$@" <<'PYEOF'
import os, sys
from memory import Memory
from face_id import FaceID

name, desc, images = sys.argv[1], sys.argv[2], sys.argv[3:]
mem = Memory()
fid = FaceID(mem)
if not fid.ensure_loaded():   # loads cv2 + onnxruntime + the ArcFace model; clear message if not installed
    print("face model not ready -- run ./scripts/provision_face_id.sh (or "
          "scripts/patches/common/5_face_id_insightface.sh) first.", file=sys.stderr)
    mem.close(); sys.exit(1)

print(f">> Enrolling '{name}' from {len(images)} photo(s)...")
enrolled, first_bytes = 0, None
for path in images:
    try:
        with open(os.path.expanduser(path), "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"   [skip] {path}: {e}", file=sys.stderr); continue
    if fid.enroll(data, name):
        enrolled += 1
        if first_bytes is None:
            first_bytes = data
        print(f"   [ok]   {path}: face embedded")
    else:
        print(f"   [skip] {path}: no face detected (crop tighter / better lighting and retry)", file=sys.stderr)

# Always store the name + description as a RAG fact (prompt context), with a saved copy of the first good
# photo as the reference image -- even if no face embedded, so the robot still "knows of" them.
img_path = mem.save_photo(first_bytes, name) if first_bytes else None
mem.remember_fact(name, "", desc, img_path)
mem.close()

if enrolled:
    tail = f" with description: {desc}" if desc else ""
    print(f">> Done: enrolled {enrolled} face shot(s) for '{name}'{tail}.")
    print("   With ROBOT_CONCIERGE_MODE=1, the concierge will greet them by name on a motion wake.")
    sys.exit(0)
print(">> No face was detected in ANY photo -- the description WAS still saved as a fact, but the concierge "
      "can't recognise them by sight yet. Use clearer, front-facing photos.", file=sys.stderr)
sys.exit(1)
PYEOF
