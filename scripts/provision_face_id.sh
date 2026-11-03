#!/usr/bin/env bash
# provision_face_id.sh -- one-time download of the LOCAL face models for Concierge Mode (brain/face_id.py).
# TWO models, both tiny, both needed for good recognition:
#   1. arcface.onnx              the RECOGNISER  (InsightFace w600k, ~16MB) -- "who is this?"
#   2. face_detection_yunet.onnx the DETECTOR    (OpenCV Zoo YuNet, ~230KB) -- "where is the face, and where
#                                are its eyes/nose/mouth?"
#
# Why the detector matters as much as the recogniser: ArcFace is trained on 112x112 crops that have been
# affine-warped so 5 landmarks land on fixed pixels. YuNet returns those landmarks; the old Haar cascade
# returns only a box. Without landmarks brain/face_id.py cannot align the crop, ArcFace sees out-of-
# distribution input, and every similarity score collapses toward the same small number -- a 19-hour field
# run recognised 6 of 257 face frames (2.3%) that way. With alignment, same-vs-different-person separation
# on this repo's own enrolment photos goes 0.334 -> 0.491 and by-name recognition 75% -> 88%.
# Missing YuNet is NOT fatal: face_id.py falls back to Haar (old accuracy) and warns at startup.
#
# ==> AFTER INSTALLING YuNet FOR THE FIRST TIME, RE-ENROL EVERYONE:  bash enroll_all.sh
#     Stored embeddings are made by this same code path, so ones created before alignment are unaligned.
#     Aligning only the live side compares apples to noise and is WORSE than not aligning at all.
#
# The Python deps these need (OpenCV + onnxruntime + numpy) are installed by the stack installers as BASE
# dependencies -- opencv-python-headless<5 in brain/requirements.txt on the Pi, apt python3-opencv on the
# Jetson -- so this script only fetches the MODELS. Concierge Mode is ready the moment you flip
# ROBOT_CONCIERGE_MODE on (and it's harmless when off), with no extra install step.
#
# IDEMPOTENT + BEST-EFFORT: a model already present is left alone (atomic write => a present file is always
# complete); any failure (no network) NEVER blocks the robot -- crawler mode doesn't need it, and Concierge
# degrades to "smile silently" until the models are there. Called by install_stack_pi5.sh /
# install_stack_jetson.sh and scripts/patches/common/5_face_id_insightface.sh. Re-run anytime:
#   ./scripts/provision_face_id.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"
MODELS="$HOME/.localairobot/models"
MODEL="${ROBOT_FACE_ID_MODEL:-$MODELS/arcface.onnx}"
YUNET="${ROBOT_FACE_ID_YUNET_MODEL:-$MODELS/face_detection_yunet.onnx}"

# Fast path: BOTH already provisioned -> nothing to do. (Checked together so an existing robot that predates
# YuNet still picks the detector up on the next install/patch run instead of silently staying on Haar.)
[ -f "$MODEL" ] && [ -f "$YUNET" ] && exit 0
[ -x "$PY" ] || { echo "provision_face_id: venv python not found ($PY) -- skipping (run install_stack first)."; exit 0; }
mkdir -p "$MODELS"

# ArcFace recognition ONNX (112x112 -> 512-d). Direct-URL override first, else the InsightFace buffalo packs
# (small then large) from the pinned v0.7 release; extract the w600k recogniser to $MODEL.
if [ -f "$MODEL" ]; then
  echo ">> Concierge face-recognition: ArcFace model already present ($MODEL)"
else
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
fi

# YuNet DETECTOR ONNX (~230KB) -- the landmarks that make ArcFace's scores mean something. It lives in the
# opencv_zoo repo under Git LFS, so the plain raw.githubusercontent URL returns a 130-byte POINTER FILE, not
# the model (which then fails deep inside cv2.dnn with a confusing "Can't read ONNX file"). The
# media.githubusercontent.com/media/... endpoint serves the real LFS object; we verify the SHA-256 so a
# pointer file, a truncated download or a captive-portal HTML page can never be written as a "model".
if [ -f "$YUNET" ]; then
  echo ">> Concierge face-detection: YuNet already present ($YUNET)"
else
echo ">> Concierge face-detection: downloading the YuNet landmark detector..."
"$PY" - "$YUNET" <<'PYEOF' || true
import hashlib, os, sys, urllib.request
target = sys.argv[1]
tmp = target + ".tmp"
# Pinned release + its known digest. Update BOTH together if you ever bump the model.
SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
urls = [os.environ.get("ROBOT_FACE_ID_YUNET_URL", "").strip(),
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx"]
for url in [u for u in urls if u]:
    try:
        print("   downloading", url, "...")
        data = urllib.request.urlopen(url, timeout=180).read()
    except Exception as e:
        print("   (failed:", e, ")"); continue
    got = hashlib.sha256(data).hexdigest()
    # Only enforce the digest for the PINNED url -- an explicit ROBOT_FACE_ID_YUNET_URL override is the
    # operator deliberately supplying their own model, so we sanity-check the format instead.
    if url == urls[-1] and got != SHA256:
        print("   (checksum mismatch: got %s, want %s -- refusing; an LFS pointer or a captive portal?)"
              % (got[:16], SHA256[:16]))
        continue
    if len(data) < 10000 or data[:2] == b"ve":     # 've'rsion https://git-lfs... = an LFS pointer file
        print("   (that is not an ONNX model -- %d bytes, looks like an LFS pointer)" % len(data)); continue
    with open(tmp, "wb") as out:
        out.write(data); out.flush(); os.fsync(out.fileno())
    os.replace(tmp, target)          # atomic: a present file is always a COMPLETE file
    print("   saved ->", target)
    sys.exit(0)
if os.path.exists(tmp):
    os.remove(tmp)
print("   could not fetch YuNet -- face recognition will fall back to Haar (much weaker; see the")
print("   'UNALIGNED crops' warning in the robot log). Set ROBOT_FACE_ID_YUNET_URL and re-run.")
sys.exit(1)
PYEOF
fi

if [ -f "$MODEL" ]; then
  echo ">> Face recogniser ready: $MODEL"
else
  echo ">> Face model NOT installed (network?). Crawler mode is unaffected; Concierge will smile silently."
  echo "   Retry: ./scripts/provision_face_id.sh   (or set ROBOT_FACE_ID_MODEL_URL to a direct .onnx URL)"
fi
if [ -f "$YUNET" ]; then
  echo ">> Face detector ready:   $YUNET  (5-point alignment ON)"
  echo "   *** If this is the FIRST time YuNet has been installed, RE-ENROL everyone so the stored"
  echo "       embeddings are aligned too -- otherwise recognition gets WORSE, not better:"
  echo "         cd $REPO && bash enroll_all.sh && ./scripts/rag.sh --faces"
else
  echo ">> YuNet detector NOT installed -- recognition falls back to Haar with NO landmark alignment,"
  echo "   which is the weak path (a field run recognised 2.3% of face frames). Retry when online:"
  echo "     ./scripts/provision_face_id.sh"
fi
exit 0
