#!/usr/bin/env bash
# enroll_person.sh -- enrol a person into Concierge Mode's RAG db, from PHOTO FILES and/or the LIVE CAMERA.
#
# Stores, under the person's NAME: a face embedding per usable shot (table `faces` -> the concierge greets
# them BY NAME on a motion wake) AND a name+description fact (table `facts` -> LLM prompt context).
# IDEMPOTENT: each run REPLACES that person -- it clears their prior embeddings + fact FIRST, so the args you
# pass are their COMPLETE enrolment and re-running never stacks duplicates. (scripts/create_enrollments.sh
# preserves the descriptions, so a re-enrol loses nothing.)
#
#   ./scripts/enroll_person.sh "Ramesh" "my creator" ~/people_named/Ramesh.jpg ~/people_named/Ramesh_02.jpg
#   ./scripts/enroll_person.sh "Shani"  ""           shani.png                 # empty description is fine
#   ./scripts/enroll_person.sh --camera "Ramesh" "my creator"                  # 5 LIVE webcam shots (no files)
#   ./scripts/enroll_person.sh --camera 8 "Ramesh" "my creator" ~/people_named/Ramesh.jpg   # file + 8 shots
#
# Photo FILES need no camera, so file-only runs are SAFE while the brain is live. --camera needs the camera,
# so it REFUSES while the brain is running (stop it first: ./scripts/shutdown.sh). Accepts any format OpenCV
# can decode (jpg/png/...). Prereq: the face model is installed (scripts/install_stack_pi5.sh, or
# scripts/provision_face_id.sh). Remove someone: ./scripts/rag.sh --forget "Name".
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"

usage() {
  echo "usage: $(basename "$0") [--camera [N]] \"Name\" \"description\" [image ...]" >&2
  echo "  files and/or --camera (default 5 shots); pass \"\" for an empty description." >&2
  echo "  Re-running REPLACES the person (idempotent) -- the args are their complete enrolment." >&2
  exit 2
}

CAMERA=0; NSHOTS=5
while [ "$#" -gt 0 ]; do
  case "$1" in
    --camera)  CAMERA=1; shift
               if [[ "${1:-}" =~ ^[0-9]+$ ]]; then NSHOTS="$1"; shift; fi ;;
    --replace) shift ;;                 # back-compat no-op: REPLACING is the default now
    --)        shift; break ;;
    -h|--help) usage ;;
    -*)        echo "unknown option: $1" >&2; usage ;;
    *)         break ;;
  esac
done

NAME="${1:-}"
DESC="${2:-}"
{ [ -n "$NAME" ] && [ "$#" -ge 2 ]; } || usage
shift 2
if [ "$CAMERA" = 0 ] && [ "$#" -eq 0 ]; then
  echo "no image files given and --camera not set -- give at least one photo source." >&2; usage
fi
[ -x "$PY" ] || { echo "brain venv not found ($PY) -- run scripts/install_stack_pi5.sh first." >&2; exit 1; }

# --camera needs the camera, which the running brain holds for its whole lifetime -> refuse if it's up
# (patterns lifted from scripts/shutdown.sh). File-only enrolments don't touch the camera, so they're fine.
if [ "$CAMERA" = 1 ] && { pgrep -f 'python.*brain/main\.py' >/dev/null 2>&1 \
   || { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet robot.service 2>/dev/null; }; }; then
  echo "--camera: the robot brain is RUNNING and holds the camera -- stop it first: ./scripts/shutdown.sh" >&2
  echo "(restart afterwards with ./scripts/run_me.sh)" >&2
  exit 1
fi

cd "$REPO/brain"
exec "$PY" - "$CAMERA" "$NSHOTS" "$NAME" "$DESC" "$@" <<'PYEOF'
import os, sys, time
from memory import Memory
from face_id import FaceID

camera = sys.argv[1] == "1"
nshots = int(sys.argv[2])
name, desc, images = sys.argv[3], sys.argv[4], sys.argv[5:]
mem = Memory()
fid = FaceID(mem)
if not fid.ensure_loaded():   # loads cv2 + onnxruntime + the ArcFace model; clear message if not installed
    print("face model not ready -- run ./scripts/provision_face_id.sh (or install_stack_pi5.sh) first.",
          file=sys.stderr)
    mem.close(); sys.exit(1)

# IDEMPOTENT: replace this person -- clear prior embeddings + fact FIRST, so the args below are their COMPLETE
# enrolment (a re-run never piles up duplicates). Descriptions are preserved by create_enrollments.sh.
removed = mem.forget_face(name)
mem.forget_fact(name)
if removed:
    print(f">> replacing '{name}': cleared {removed} prior embedding(s).")

enrolled, first_bytes = 0, None
for path in images:
    try:
        with open(os.path.expanduser(path), "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"   [skip] {path}: {e}", file=sys.stderr); continue
    if fid.enroll(data, name):
        enrolled += 1
        first_bytes = first_bytes or data
        print(f"   [ok]   {path}: face embedded")
    else:
        print(f"   [skip] {path}: no face detected (crop tighter / better lighting)", file=sys.stderr)

if camera:   # folded-in live capture (was scripts/enroll_face.sh): grab N shots from the webcam
    from camera import Camera
    cam = Camera()
    if not getattr(cam, "available", False):
        print("   [camera] not available -- set ROBOT_CAMERA_BACKEND/ROBOT_CAMERA_SOURCE for your webcam.",
              file=sys.stderr)
    else:
        print(f">> camera: taking {nshots} shot(s) -- look at the lens, move/turn slightly between them.")
        for i in range(nshots):
            jpeg = cam.capture_jpeg()
            if jpeg and fid.enroll(jpeg, name):
                enrolled += 1
                first_bytes = first_bytes or jpeg
                print(f"   [{i + 1}/{nshots}] camera: face captured")
            else:
                print(f"   [{i + 1}/{nshots}] camera: no face -- reposition / better lighting", file=sys.stderr)
            time.sleep(0.8)
    try:
        cam.close()
    except Exception:
        pass

# Store the name + description fact + a saved reference photo (first usable shot), even if 0 faces embedded.
img_path = mem.save_photo(first_bytes, name) if first_bytes else None
mem.remember_fact(name, "", desc, img_path)
mem.close()

if enrolled:
    tail = f" with description: {desc}" if desc else ""
    print(f">> Done: enrolled {enrolled} face shot(s) for '{name}'{tail}.")
    print("   With ROBOT_CONCIERGE_MODE=1, the concierge greets them by name on a motion wake.")
    sys.exit(0)
print(">> No face detected in ANY shot -- the description was saved as a fact, but the concierge can't "
      "recognise them by sight yet. Use clearer, front-facing photos (or better light for --camera).",
      file=sys.stderr)
sys.exit(1)
PYEOF
