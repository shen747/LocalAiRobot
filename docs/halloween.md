# 🎃 Halloween mode

An extra for [Concierge mode](concierge.md). Instead of a friendly hello, a visitor gets a loud jump-scare
sound, one of 50 kid-friendly spooky lines and a monster face, and then a chat with a spooky character. It
never moves. A scare needs the motion sensor and the camera to agree that a person is there, so a costumed kid
gets scared but an empty doorway doesn't.

## Turn it on

On the robot, turn on both modes and restart:

```bash
cd ~/LocalAiRobot
sed -i -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=1|' \
       -e 's|^ROBOT_HALLOWEEN_MODE=.*|ROBOT_HALLOWEEN_MODE=1|' config/robot.env
./scripts/run_me.sh --reload
```

`ROBOT_HALLOWEEN_MODE=0` goes back to normal greetings; `ROBOT_CONCIERGE_MODE=0` back to the crawler.

You need what the concierge needs: a [PIR motion sensor](wiring/pir-sensor-wiring.md), a USB camera and a
speaker, turned up. The camera matters: with no picture there are no scares at all, and the log says
`the camera gave no picture -- NOT spooking`. Set `ROBOT_HALLOWEEN_CAMERA_CHECK=0` to scare on motion alone.

## What happens

1. Motion wakes the robot, and the camera takes about a second of frames (up to 6 s while the sensor still
   sees someone). A person detector (OpenCV Zoo's NanoDet-Plus, `models/person_nanodet.onnx`) checks each one.
   It finds bodies, so masks don't matter, and only counts people tall enough to be near the door: at least 15%
   of the camera's square frame, about a quarter of a widescreen picture's height. That ignores the street
   reflected in a glass door. A visible face also counts.
2. Nobody there: no scare, and it goes back to sleep. That's what stops a twitchy sensor or moving sunlight
   from scaring an empty doorway.
3. Someone there: a random monster face, a loud stinger, a spooky line, then a normal conversation in
   character. Ask it to drive and it refuses, spookily.
4. It's ready for the next group `ROBOT_HALLOWEEN_COOLDOWN_S` (10 s) later.

The log says what it saw: `halloween: motion -> spook (person 0.78)` or `(a face)`. Raise
`ROBOT_HALLOWEEN_PERSON_SCORE` (0.5) if a life-size decoration keeps passing; lower it if costumed kids get
missed.

## Photos of every scare

Each scare keeps two pictures in `~/detect-captures`: `<time>-spook.jpg`, the frame that confirmed somebody was
there, and `<time>-spook-reaction.jpg`, their face a couple of seconds into the scare. With
`ROBOT_DETECT_CAPTURE_MODE=all`, empty looks are kept as `<time>-noface.jpg` too. They're deleted after
`ROBOT_DETECT_CAPTURE_KEEP_DAYS` (7); `ROBOT_DETECT_CAPTURE=0` keeps none.

To browse them from a phone on the same Wi-Fi, run this on the robot and open `http://<hostname>.local:8000`.
While it runs, anyone on your Wi-Fi can see the photos, so press Ctrl-C when you're done:

```bash
python3 -m http.server 8000 --directory ~/detect-captures
```

## Make it yours

- **Lines and persona:** `SPOOKY_GREETINGS`, `SPOOKY_REFUSALS` and `PERSONA` in `brain/halloween.py`.
- **Faces:** ten monsters in `brain/faces/halloween/` (01.png to 10.png); each visitor gets one at random. Use
  your own PNGs, or regenerate them for another screen with
  `./scripts/generate_halloween_faces.sh --size 800x480 --force`.
- **Sounds:** the stingers in `sounds/spook_sounds/` are synthetic and as loud as the speaker allows. Drop in
  your own WAVs (normalised loud), or regenerate with `./scripts/generate_spook_stingers.sh --force`.

The lines are playful, a fright and then a wink, but the stinger is genuinely loud: turn the speaker down for
very small visitors.
