#!/usr/bin/env bash
# camera_face_probe.sh -- live "does the camera actually SEE a face, and how big is it?" monitor.
#
# The concierge's Stage-A probe is a yes/no: a face was found, or it wasn't. When the answer is always NO but
# a person is definitely standing there, the log can't tell you WHY -- the frame isn't kept unless it produced
# a greeting (ROBOT_DETECT_CAPTURE_MODE=greeted), and "no face" covers four completely different faults:
#     * the camera is aimed somewhere else                -> box is 0px, and the SAVED FRAME shows the wrong view
#     * the visitor is too far away                       -> box is non-zero but under ROBOT_FACE_ID_MIN_FACE_PX
#     * the frame is too dark / blurred to detect in      -> box 0px and a small JPEG (<8KB is a night frame)
#     * the detector never loaded                         -> says so on the first line
# This prints the face box size in pixels, live, and saves the frames -- so you stand where a visitor stands
# and read off the number. That turns "it doesn't detect me" into "my face is 38px and the floor is 60px".
#
#   ./scripts/camera_face_probe.sh              # live read-out, saves every frame to ~/face-probe/
#   ./scripts/camera_face_probe.sh --no-save    # read-out only
#   ./scripts/camera_face_probe.sh 0.5          # capture every 0.5s (default 1.0)
#
# STOP THE BRAIN FIRST -- it holds the camera:  sudo systemctl stop robot.service
# (and start it again afterwards:               sudo systemctl start robot.service)
set -uo pipefail
REPO="$(cd "$(dirname "$0")" && cd .. && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }

PY="$REPO/brain/.venv/bin/python"
[ -x "$PY" ] || { echo "brain venv not found ($PY) -- run ./scripts/install_stack_pi5.sh first." >&2; exit 1; }

if pgrep -f 'python.*brain/main\.py' >/dev/null 2>&1; then
  echo "!! The brain is running and holding the camera. Stop it first:" >&2
  echo "     sudo systemctl stop robot.service     (then: sudo systemctl start robot.service)" >&2
  exit 1
fi

cd "$REPO/brain"
exec "$PY" - "$@" <<'PYEOF'
import os, sys, time
sys.path.insert(0, os.getcwd())
from camera import Camera
from memory import Memory
from face_id import FaceID

save, interval = True, 1.0
for a in sys.argv[1:]:
    if a == "--no-save":
        save = False
    elif a in ("-h", "--help"):
        print("usage: camera_face_probe.sh [seconds_between_frames] [--no-save]"); sys.exit(0)
    else:
        try: interval = max(0.1, float(a))
        except ValueError: print(f"ignoring {a!r}", file=sys.stderr)

outdir = os.path.expanduser("~/face-probe")
if save:
    os.makedirs(outdir, exist_ok=True)

cam = Camera()
if not cam.available:
    print("camera UNAVAILABLE -- nothing to probe (is the brain still running and holding it?)"); sys.exit(1)

mem = Memory()
fid = FaceID(mem)
ready = fid.ensure_loaded()
live_floor = fid.min_face
print(f"face_id: {'ready' if ready else 'NOT LOADED -- every frame will read 0px'} "
      f"(detector={fid.detector}, {'ALIGNED' if fid.aligned else 'UNALIGNED'})")
print(f"live floor = ROBOT_FACE_ID_MIN_FACE_PX = {live_floor}px  "
      f"(a face SMALLER than this is ignored by the concierge)")
print(f"frames -> {outdir}" if save else "not saving frames (--no-save)")
print("\nStand where a visitor would stand. Ctrl-C to stop.\n")
print(f"  {'time':8}  {'jpeg':>7}  {'face box':>9}   verdict")

n = ok = 0
try:
    while True:
        jpeg = cam.capture_jpeg()
        n += 1
        ts = time.strftime("%H:%M:%S")
        if not jpeg:
            print(f"  {ts}  {'--':>7}  {'--':>9}   NO FRAME (capture failed)")
            time.sleep(interval); continue
        seen = fid.detect(jpeg)            # same call Stage A makes
        px = fid.last_face_px
        if seen:
            ok += 1
            verdict = f"FACE OK -- the concierge would look closer ({px}px >= {live_floor})"
        elif px:
            verdict = f"too small -- {px}px < {live_floor}px floor: step closer, or lower the floor"
        else:
            verdict = "no face found -- wrong aim? too dark? too far to resolve at all?"
        print(f"  {ts}  {len(jpeg)//1024:5d}KB  {px:7d}px   {verdict}")
        if save:
            tag = "face" if seen else ("small" if px else "none")
            with open(os.path.join(outdir, f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}-{px}px.jpg"), "wb") as f:
                f.write(jpeg)
        time.sleep(interval)
except KeyboardInterrupt:
    print(f"\nstopped. {ok}/{n} frame(s) had a usable face.")
    if save:
        print(f"Look at the frames:  ls -l {outdir}    (scp them off to view)")
finally:
    try: cam.close()
    except Exception: pass
PYEOF
