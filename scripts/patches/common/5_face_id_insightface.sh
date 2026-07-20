#!/usr/bin/env bash
# =============================================================================
#   PATCH:   Local face recognition for CONCIERGE MODE (ArcFace ONNX)
#   RUN IF:  you want ROBOT_CONCIERGE_MODE=1 to greet known people by name
#   BOARD:   common  (Pi 5 or Jetson)
#   DOES:    installs the face-recognition deps + ArcFace model (now, on demand)
# =============================================================================
# NOTE: scripts/install_stack_pi5.sh / _jetson.sh already install these (with the other models), so you
# usually do NOT need this patch. It's here to install/refresh on demand and print the enrol + enable steps.
# STRICTLY LOCAL: cv2 detects the face, onnxruntime embeds it, matched against the RAG db -- no cloud.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
if [ -t 1 ]; then GRN=$'\033[1;32m'; RST=$'\033[0m'; else GRN=''; RST=''; fi

# Single source of truth: the shared provisioner (deps + model download; idempotent + best-effort).
bash "$REPO/scripts/provision_face_id.sh" || true

echo
echo ">> NEXT:"
echo "     1) enrol people ->  ./scripts/enroll_face.sh \"Ramesh\""
echo "     2) (optional) PIR ->  set ROBOT_PIR_PIN=<BCM gpio>  (empty = wake-word-only concierge)"
echo "     3) turn it on   ->  set ROBOT_CONCIERGE_MODE=1  (top of config/robot.env)"
echo "   Tune matching with ROBOT_FACE_ID_THRESHOLD (higher = stricter). Docs: docs/concierge.md"
echo
echo "${GRN}=====================================${RST}"
echo "${GRN}  FACE-ID PATCH DONE (Concierge) !!!${RST}"
echo "${GRN}=====================================${RST}"
