# Face

The robot draws a built-in face for each state, so it's expressive with no image files. To give it your own
look, put PNG frames in `brain/faces/<state>/` and commit them; the robot animates yours instead, one state at
a time.

```
brain/faces/
  idle/   listening/   thinking/   talking/   exploring/
  happy/  confused/    alarmed/    warmup/    capturing/
  halloween/                       # Halloween mode's monsters
```

- **Two or more frames animate, one is a still, none uses the built-in face.** Frames play in filename order
  (`01.png`, `02.png`, ...).
- **Size:** frames are scaled to fit, but match the shape. The USB-C panel is drawn landscape, so make them
  480×320; a 5" DSI screen is 800×480.
- `talking` plays faster, with random frames, so it looks like speech.

## Which screen

`ROBOT_FACE_BACKEND=auto` tries the USB-C panel (`turing`), then a DSI or HDMI screen (`framebuffer`), then
just logs (`simulated`). A missing screen never stops the robot.

- **USB-C panel:** set `ROBOT_FACE_REVISION` to `A` (Turing 320×480) or `B` (XuanFang). Mounted upside down?
  `ROBOT_FACE_ORIENTATION=reverse_landscape`.
- **DSI or HDMI screen:** it only shows on a console boot, not the desktop
  (`sudo systemctl set-default multi-user.target`, which the installer does, then reboot).

## Making frames

Make them on your PC. `./scripts/host_setup.sh` installs the tools once (ImageMagick, ffmpeg, Pillow). Then pick
a method:

```bash
./scripts/generate_faces.sh --size 800x480        # draws the built-in face for every state as a starting point
ffmpeg -i talk.mp4 -vf "fps=8,scale=800:480" brain/faces/talking/%02d.png      # frames from a clip
magick in.png -resize 800x480^ -gravity center -extent 800x480 out.png         # fit any image
```

An image generator works too: one prompt per state, such as "robot face, happy expression, glowing eyes, flat
dark background, centered, 800x480". Make 2 to 4 variations per state with the same prompt and seed so the
frames don't jump.

To preview without a screen, run `ROBOT_FACE_DUMP=/tmp/faces ./scripts/run_me.sh --dry` and open
`/tmp/faces/face_<state>.png`. When you like them, `git add brain/faces/` and commit.

Settings: `ROBOT_FACE_FPS` (frame rate), `ROBOT_FACE_FRAMES_DIR` (this folder by default) and the rest in the
[settings reference](../../config/README.md#face-screen).
