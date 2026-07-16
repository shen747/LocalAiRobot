#!/usr/bin/env bash
# enroll_face.sh -- enrol a person's face for Concierge Mode (ROBOT_CONCIERGE_MODE=1).
#
# Captures a few shots from the (USB) webcam, embeds each detected face with the local ArcFace ONNX model,
# and stores them in the RAG db under a name. On a motion wake, Concierge Mode matches the visitor against
# these and greets them by name. Enrol from a couple of angles / distances for better matching.
#
#   ./scripts/enroll_face.sh "Ramesh"          # 5 shots (default)
#   ./scripts/enroll_face.sh "Ramesh" 8        # 8 shots
#
# Prereqs: the face model is installed (scripts/patches/common/5_face_id_insightface.sh) and the webcam works
# (ROBOT_CAMERA_BACKEND=v4l2 + ROBOT_CAMERA_SOURCE). Remove someone: rag.sh, or DELETE FROM faces WHERE name=.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"
NAME="${1:-}"
N="${2:-5}"
if [ -z "$NAME" ]; then echo "usage: $(basename "$0") \"Name\" [num_shots]" >&2; exit 2; fi
[ -x "$PY" ] || { echo "brain venv not found ($PY) -- run scripts/install_stack_pi5.sh first." >&2; exit 1; }

cd "$REPO/brain"
exec "$PY" - "$NAME" "$N" <<'PYEOF'
import sys, time
from camera import Camera
from memory import Memory
from face_id import FaceID

name, n = sys.argv[1], int(sys.argv[2])
cam, mem = Camera(), Memory()
fid = FaceID(mem)
if not getattr(cam, "available", False):
    print("camera not available -- set ROBOT_CAMERA_BACKEND=v4l2 + ROBOT_CAMERA_SOURCE for the USB webcam.",
          file=sys.stderr); sys.exit(1)
if not fid.available:
    print("face model not ready -- run ./scripts/patches/common/5_face_id_insightface.sh first.",
          file=sys.stderr); sys.exit(1)

print(f">> Enrolling '{name}'. Look at the camera; I'll take {n} shots (move/turn slightly between them).")
ok = 0
for i in range(n):
    jpeg = cam.capture_jpeg()
    if jpeg and fid.enroll(jpeg, name):
        ok += 1; print(f"   [{i+1}/{n}] face captured")
    else:
        print(f"   [{i+1}/{n}] no face detected -- reposition / better lighting")
    time.sleep(0.8)
cam.close(); mem.close()
if ok:
    print(f">> Done: enrolled {ok}/{n} shots for '{name}'. Set ROBOT_CONCIERGE_MODE=1 and walk up to test it.")
    sys.exit(0)
print(">> FAILED: no face captured. Face the camera in good light and try again.")
sys.exit(1)
PYEOF
