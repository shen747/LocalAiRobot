# Animated face frames (optional)

Drop PNG sequences here and `brain/face.py` will animate them instead of drawing the built-in
procedural face. **With no frames present, the face behaves exactly as before** (procedural), so
this is purely additive.

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
- A state with **≥2 frames animates**; **1 frame** is a static image; **0 frames** falls back to
  the procedural drawing for that state. You can add states/frames incrementally.
- Frames are **resized to fit the panel**, so you can author at any size: the USB-C panel is **320×480
  portrait**, a DSI/HDMI panel like the **Freenove 5"** is **800×480 landscape** — both just work (the
  framebuffer backend auto-reads its own size).
- The USB-C (turing) panel is serial-bound, so its effective frame rate is clamped; the DSI/HDMI
  framebuffer can go faster.

Tuning ([`config/robot.env`](../../config/robot.env)): `ROBOT_FACE_FPS` (default rate for looping
states; `talking` runs faster), `ROBOT_FACE_FRAMES_DIR` (defaults to this folder). Inspect rendered
frames without a panel using `ROBOT_FACE_DUMP=<dir>` (writes `face_<state>.png` on each state change).

## Generating the frames (concise)

PNGs are **resized to fit your panel**, so any size works — but matching it avoids upscaling: **320×480**
(USB-C panel) or **800×480** (Freenove 5" DSI). Pick whichever tool is easiest:

1. **AI image generator** (best for expressive art) — prompt per state, e.g.
   *"robot face, `<happy/thinking/alarmed/…>` expression, glowing eyes, flat dark background, centered,
   320×480 portrait, simple, high contrast."* Generate **2–4 variations per state** for animation, and
   **reuse the same prompt + seed** so frames don't jump. Save as `<state>/01.png`, `02.png`, …
2. **From a short clip/GIF** you already have:
   `ffmpeg -i talk.mp4 -vf "fps=8,scale=320:480" brain/faces/talking/%02d.png`
3. **Resize/pad any image** to the panel — ImageMagick (swap `320x480` for `800x480` on the DSI panel):
   `magick in.png -resize 320x480^ -gravity center -extent 320x480 out.png`
   (or Pillow: `Image.open("in.png").resize((320,480)).save("out.png")`).
4. **Preview without a panel:** `ROBOT_FACE_DUMP=/tmp/faces ./scripts/run_me.sh --dry`, drive through the
   states, then open `/tmp/faces/face_<state>.png`.

Start with one state (e.g. `talking/` with 3 frames) — every other state keeps its procedural look until
you add art.

> The 8 base moods plus `warmup`/`capturing` all have a built-in procedural look, so the robot is
> expressive out of the box — these PNGs are just a nicer skin when you have the art.
