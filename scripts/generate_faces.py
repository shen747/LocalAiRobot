#!/usr/bin/env python3
"""generate_faces.py -- render the robot's built-in procedural face to PNG *starting templates* you can
then edit or hand to an image generator.

Reuses the SAME renderer the robot uses (brain/face.py `_render`), so each template is on-model. Writes
brain/faces/<state>/01.png for every expression; add 02.png, 03.png ... to a state to make it animate
(>=2 frames). Skips files that already exist unless --force. The PNGs are git-tracked, so your customized
faces commit and ship to the Pi.

Needs **Pillow** -- on your PC run it with the venv from scripts/host_setup.sh; on the Pi the brain venv
already has it:
  ~/.localairobot-tools/venv/bin/python scripts/generate_faces.py            # 320x480 (USB-C panel)
  ~/.localairobot-tools/venv/bin/python scripts/generate_faces.py --size 800x480 --force   # DSI panel
"""
import argparse
import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="Render procedural face PNG templates (needs Pillow).")
    ap.add_argument("--out", default=os.path.join(repo, "brain", "faces"),
                    help="faces dir (default: brain/faces)")
    ap.add_argument("--size", default="320x480",
                    help="WxH: 320x480 = USB-C panel (default), 800x480 = Freenove 5\" DSI")
    ap.add_argument("--force", action="store_true", help="overwrite existing 01.png (default: skip)")
    args = ap.parse_args()
    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        ap.error("--size must be WxH, e.g. 320x480")

    # Import the real renderer from brain/face.py (it pulls no hardware deps at import time).
    sys.path.insert(0, os.path.join(repo, "brain"))
    try:
        import face
    except Exception as e:
        sys.exit(f"could not import the face renderer ({e}) -- run from the repo clone.")

    made, skipped = 0, 0
    for state in face.STATES:
        d = os.path.join(args.out, state)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "01.png")
        if os.path.exists(path) and not args.force:
            skipped += 1
            continue
        img = face._render(state, w, h)
        if img is None:
            sys.exit("Pillow isn't available -- install it (scripts/host_setup.sh) and retry.")
        img.save(path)
        made += 1
        print(f"  wrote {os.path.relpath(path, args.out)}")
    print(f">> done: {made} written, {skipped} kept ({w}x{h}). Edit them / add 02.png+ to animate, "
          f"then commit. Preview a state with ROBOT_FACE_DUMP, or just run the robot.")


if __name__ == "__main__":
    main()
