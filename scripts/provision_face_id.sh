#!/usr/bin/env bash
# provision_face_id.sh -- one-time download of the LOCAL ArcFace recognition model for Concierge Mode
# (brain/face_id.py). The Python deps it needs (OpenCV + onnxruntime + numpy) are installed by the stack
# installers as BASE dependencies -- opencv-python-headless<5 in brain/requirements.txt on the Pi, apt
# python3-opencv on the Jetson -- so this script only fetches the MODEL. That means Concierge Mode is ready
# the moment you flip ROBOT_CONCIERGE_MODE on (and it's harmless when off), with no extra install step.
#
# IDEMPOTENT + BEST-EFFORT: if the model is already present it exits 0 immediately (atomic write => a present
# file is always complete); any failure (no network) NEVER blocks the robot -- crawler mode doesn't need it,
# and Concierge degrades to "smile silently" until the model is there. Called by install_stack_pi5.sh /
# install_stack_jetson.sh and scripts/patches/common/5_face_id_insightface.sh. Re-run anytime:
#   ./scripts/provision_face_id.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"
MODELS="$HOME/.localairobot/models"
MODEL="${ROBOT_FACE_ID_MODEL:-$MODELS/arcface.onnx}"

# Fast path: already provisioned -> nothing to do.
[ -f "$MODEL" ] && exit 0
[ -x "$PY" ] || { echo "provision_face_id: venv python not found ($PY) -- skipping (run install_stack first)."; exit 0; }
mkdir -p "$MODELS"

# ArcFace recognition ONNX (112x112 -> 512-d). Direct-URL override first, else the InsightFace buffalo packs
# (small then large) from the pinned v0.7 release; extract the w600k recogniser to $MODEL.
echo ">> Concierge face-recognition: downloading the ArcFace model..."
"$PY" - "$MODEL" <<'PYEOF' || true
import io, os, sys, urllib.request, zipfile
target = sys.argv[1]
tmp = target + ".tmp"
direct = os.environ.get("ROBOT_FACE_ID_MODEL_URL", "").strip()
zips = ["https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"]
def fetch(url, timeout=240):
    print("   downloading", url, "...")
    return urllib.request.urlopen(url, timeout=timeout).read()
def commit(data):
    # Step 24: write to a .tmp + fsync, then atomically rename -- a power cut / ENOSPC can never leave a
    # TRUNCATED $MODEL that the [ -f ] fast path would then treat as "already provisioned" forever.
    with open(tmp, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, target)          # same-dir rename: atomic even under a ROBOT_FACE_ID_MODEL override
try:
    if direct:
        commit(fetch(direct))
        print("   saved ->", target); sys.exit(0)
    for url in zips:
        try:
            z = zipfile.ZipFile(io.BytesIO(fetch(url)))
        except Exception as e:
            print("   (failed:", e, ")"); continue
        rec = [n for n in z.namelist() if n.lower().endswith(".onnx") and "w600k" in n.lower()]
        if rec:
            commit(z.open(rec[0]).read())
            print("   extracted", rec[0], "->", target); sys.exit(0)
    print("   could not fetch a model -- set ROBOT_FACE_ID_MODEL_URL to a direct .onnx URL and re-run.")
    sys.exit(1)
except Exception as e:
    print("   model provisioning failed:", e); sys.exit(1)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)               # don't leave a stale .tmp on a failed/ENOSPC write
PYEOF

if [ -f "$MODEL" ]; then
  echo ">> Face model ready: $MODEL"
else
  echo ">> Face model NOT installed (network?). Crawler mode is unaffected; Concierge will smile silently."
  echo "   Retry: ./scripts/provision_face_id.sh   (or set ROBOT_FACE_ID_MODEL_URL to a direct .onnx URL)"
fi
exit 0
