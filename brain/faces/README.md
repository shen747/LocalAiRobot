# Face frames

The robot has an expressive face. Out of the box it draws a built-in **procedural** face for every state,
so it's expressive on a fresh clone with no image files. To give it a custom look, drop **PNG frames** in
`brain/faces/<state>/NN.png` — `brain/face.py` then animates yours instead of drawing the procedural one,
per state. You author the PNGs **on your PC** (instructions below) and commit them, so they ship with the
repo.

```
brain/faces/
  idle/        01.png ...        # 1 frame = static still; >=2 frames = animation
  listening/   01.png 02.png
  thinking/    01.png ... 04.png
  talking/     01.png ... 03.png # fast + random-frame mouth (looks like speech)
  exploring/   ...
  happy/  confused/  alarmed/    # brief transient moods (a still or short loop is plenty)
  warmup/                        # shown while the model loads at boot
  capturing/                    # shown during a camera grab before a vision look
```

Rules (`brain/face.py`):
- Frames are sorted by the number in the filename — both `04.png` and `thinking 04.png` work.
- A state with **≥2 frames animates**; **1 frame** is a static image; **0 frames** falls back to the
  procedural drawing for that state. So you can theme one state at a time; the rest stay procedural.
- Frames are **resized to fit the panel**, so you can author at any size: the USB-C panel is **320×480
  portrait**, a DSI/HDMI panel like the **Freenove 5"** is **800×480 landscape** — both just work (the
  framebuffer backend auto-reads its own size).
- The USB-C (turing) panel is serial-bound, so its effective frame rate is clamped; the DSI/HDMI
  framebuffer can go faster.

## Generating the frames (on your PC)

Author face PNGs **on your PC** (WSL / macOS / Linux), not the Pi — the Pi only ever runs
`scripts/install_stack.sh` and reads `config/robot.env`. Match the panel size to avoid upscaling:
**320×480** (USB-C panel) or **800×480** (Freenove 5" DSI); any size is resized to fit.

**1. One-time: install the tools.**
```bash
./scripts/host_setup.sh        # plan -> confirm -> installs ImageMagick + ffmpeg + a Pillow venv (+ Piper for the earcons)
```

**2. Make the frames** — pick whichever is easiest:

- **On-model templates (`scripts/generate_faces.py`)** — renders the *actual* procedural face for every
  state to `<state>/01.png`, a ready starting point to edit or hand to an image generator:
  ```bash
  ~/.localairobot-tools/venv/bin/python scripts/generate_faces.py --size 800x480
  ```
- **AI image generator** — one prompt per state, e.g. *"robot face, happy expression, glowing eyes, flat
  dark background, centered, 800×480, simple, high contrast."* Make 2–4 variations per state (reuse the
  prompt + seed so frames don't jump) and save them `01.png`, `02.png`, …
- **From a clip** — `ffmpeg -i talk.mp4 -vf "fps=8,scale=800:480" brain/faces/talking/%02d.png`.
- **Resize/pad any image** — `magick in.png -resize 800x480^ -gravity center -extent 800x480 out.png`
  (ImageMagick 6 uses `convert` instead of `magick`).

**3. Test, then commit.** Preview without a panel by dumping each rendered state to PNG —
`ROBOT_FACE_DUMP=/tmp/faces ./scripts/run_me.sh --dry` (on a machine with the brain venv) — then open
`/tmp/faces/face_<state>.png`. When they look right:
```bash
git add brain/faces/ && git commit -m "Custom face frames"
```
`*.png` isn't gitignored, so the frames ship with your clone and the robot shows them the moment a state
has ≥2 of them. **This is the exact process used to make the shipped set** — follow it for your own art.

## Tuning (on the Pi, via config)

In [`config/robot.env`](../../config/robot.env) (reference: [`config/README.md`](../../config/README.md)):
`ROBOT_FACE_FPS` (default rate for looping states; `talking` runs faster), `ROBOT_FACE_FRAMES_DIR`
(defaults to this folder), `ROBOT_FACE_DUMP` (write `face_<state>.png` on each change, to inspect with no
panel), and the backend/panel settings (`ROBOT_FACE_BACKEND`, geometry).

> The procedural face already covers all 10 states, so the robot is expressive with no PNGs at all — these
> frames are a nicer skin when you have the art.
