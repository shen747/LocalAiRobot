# Concierge mode

A stationary greeter. With `ROBOT_CONCIERGE_MODE=1` the robot never drives, introduces itself as "Jarvis the
Concierge", and wakes on a PIR motion sensor as well as the wake word. When someone walks up it recognises them
on the Pi, with no cloud involved, and greets them by name.

<p align="center">
  <img src="my-final-products/concerge_final_look.jpg" width="55%" alt="The finished Concierge build on a wall: an IR night-vision USB camera in a clear case on a ball-head mount sits above a clear-lid enclosure whose screen shows the three-day forecast card, with a round USB microphone and a small cooling fan inside and a black bar speaker underneath.">
</p>

*The wall-mounted build, with the IR camera on top and the [forecast card](weather.md) as its idle face.*

## What changes

| | Crawler | Concierge |
|---|---|---|
| Driving | drives, explores, finds | refuses, politely and in character |
| Wakes on | the wake word | the wake word or motion |
| On motion | nothing | looks, then greets you by name, says hello, or stays quiet if nobody's there |
| Face recognition | not used | on the Pi only |

Everything else (speech, the face screen, memory, the phone remote) works as usual.

## Setup

You need a PIR motion sensor and a USB camera. Skip the ESC patch and the wheels-off check: the concierge
never drives.

1. **Wire the PIR** with the Pi off: OUT to GPIO 4 (pin 7) and GND to pin 6. Power comes from pin 2 (5 V)
   for an HC-SR501, but **from pin 1 (3.3 V) for a Panasonic PaPIRs, which 5 V destroys**. Details:
   [PIR wiring](wiring/pir-sensor-wiring.md).
2. **Install** with [install.md](install.md), steps 1 to 6. The installer also fetches the face models. Say
   yes to its reboot: that applies the GPIO and camera permissions.
3. **Test the PIR:** run `./scripts/pir_sensor_probe.sh`, wave, and watch for `#### MOTION ####`. Ctrl-C stops it.
4. **Set up the camera and turn the mode on:**
   ```bash
   ./scripts/patches/pi5/3_camera_usb_webcam.sh 0     # or 3_camera_usb_ir_1080p.sh 0 for night vision
   sed -i 's/^ROBOT_CONCIERGE_MODE=.*/ROBOT_CONCIERGE_MODE=1/' config/robot.env
   ```
   Using a PaPIRs? Also run `./scripts/patches/common/6_motion_pir_papirs.sh`. The wall build with a DSI
   screen also sets `ROBOT_FACE_BACKEND=framebuffer`, `ROBOT_CONCIERGE_GREET_ONLY=1` and
   `ROBOT_FOLLOWUP_SILENCE_S=10`.
5. **Enrol the people it should know** (next section).
6. **Run it:** `./scripts/run_me.sh` to try it, then `./scripts/run_me.sh --install-boot` and `sudo reboot`
   to start it on every boot.
7. **Check it:**
   ```bash
   journalctl -u robot.service -n 300 | grep -E 'camera:|PIR motion wake|detect captures|face_id: ready'
   ```
   You want a `camera:` line, `PIR motion wake: armed on BCM GPIO 4`, and `face_id: ready (... 5-point ALIGNED ...)`.
   That last part matters most: without it, recognition barely works. See
   [troubleshooting](troubleshooting.md#face-recognition).

Walk up and it greets enrolled people by name; anyone else gets a friendly hello.

## Enrolling people

`enroll_person.sh` stores a face, a description and a "say-as" spelling under a name. Running it again for the
same name replaces that person.

| From | Command | Notes |
|---|---|---|
| Photos you have | `./scripts/enroll_person.sh "Ramesh" "my creator" "Ruh-mesh" ~/people_named/Ramesh.jpg` | safe while the robot runs |
| The live camera | `./scripts/enroll_person.sh --camera 5 "Ramesh" "my creator" "Ruh-mesh"` | stop the robot first with `./scripts/shutdown.sh` |

- **Say-as** is how the greeting pronounces the name: spell it by sound, joined with hyphens (`Ruh-mesh`, never
  `R uh m e sh`). Pass `""` to use the real name. Hear it first with `./scripts/pronounce_my_name.sh Ruh-mesh`.
- **More photos give better recognition.** Name extra shots `Ramesh_02.jpg` and so on. With an IR camera, add a
  few night shots too: infrared frames don't match daylight photos well.
- **A crowd:** put everyone's photos in a folder as `<Name>.jpg` and run `./scripts/create_enrollments.sh` on
  your PC to make `enroll_all.sh`. Fill in the descriptions, copy the photos to `~/people_named/` and the
  script to `~/LocalAiRobot/` on the Pi, then run `bash enroll_all.sh` there. Re-running the generator after
  adding people keeps your edits. Faces on one contact sheet? `./scripts/slice_image.sh` cuts
  `people/people_all*.png` into single photos.
- **Check and remove:** `./scripts/rag.sh --faces` lists who's enrolled and how many photos each has;
  `./scripts/rag.sh --forget "Name"` removes someone.

## How a visit works

1. The PIR fires and the face smiles straight away.
2. **A quick look:** a couple of frames check whether anyone is there at all. Most PIR triggers are false (98.8%
   in one long field log), so this cheap check keeps the robot responsive. Nobody there: it stays quiet and
   ignores motion for `ROBOT_CONCIERGE_SILENT_COOLDOWN_S` (4 s).
3. **Recognition:** someone is there, so it keeps looking and matching faces for `ROBOT_CONCIERGE_RECOGNIZE_S`
   (7 s, longer while the sensor still sees them). A match gets "Welcome back, Ramesh!"; a face it doesn't know
   gets a friendly hello; if they walked off, it stays quiet.
4. It drops into a normal conversation. After a greeting it ignores motion for
   `ROBOT_CONCIERGE_GREET_COOLDOWN_S` (60 s), or until the sensor sees the person leave.

The code is in `brain/pir.py`, `brain/face_id.py`, `brain/concierge.py` and the `MOTION` branch of
`brain/main.py`.

## Greet only

`ROBOT_CONCIERGE_GREET_ONLY=1` drops the spoken goodbye ("Okay, chat later.") at the end of a conversation.
Conversations work the same; it just goes quiet when the follow-up window runs out. That suits a hallway,
where the window usually expires with nobody left to say goodbye to. Apply it with `./scripts/run_me.sh --reload`.

## Asking "who is this?"

Ask "who is this?" or "who are these people?" mid-conversation and it names everyone it recognises in view,
then knows who it's talking to for the rest of the chat. `ROBOT_IDENTIFY_TRIGGERS` adds your own phrases.

## What it saw

Every motion wake keeps the one frame that decided what happened, in `~/detect-captures`:

```
20260730-074742-known-ramesh.jpg    recognised, greeted by name
20260730-074743-unknown.jpg         a face it didn't know, generic hello
20260730-013120-noface.jpg          nobody there, stayed quiet (only with ROBOT_DETECT_CAPTURE_MODE=all)
```

A daily job deletes captures older than `ROBOT_DETECT_CAPTURE_KEEP_DAYS` (7), and the folder never grows past
`ROBOT_DETECT_CAPTURE_MAX_MB` (2000). `ROBOT_DETECT_CAPTURE=0` keeps nothing. Keep `MODE=all` for chasing false
triggers only: it saves a frame for every empty look.

```bash
ls -lt ~/detect-captures | head                   # the most recent
./scripts/purge_detect_captures.sh                # delete old ones now
```

## A second, wireless sensor

A Zigbee motion sensor can cover a spot the wired PIR can't see: [zigbee-pir.md](zigbee-pir.md).

## Settings

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_PIR_PIN` | `4` | the PIR's GPIO (BCM); empty means wake by voice only |
| `ROBOT_CONCIERGE_SILENT_COOLDOWN_S` | `4` | ignore motion this long after an empty look |
| `ROBOT_CONCIERGE_GREET_COOLDOWN_S` | `60` | ignore motion this long after a greeting |
| `ROBOT_CONCIERGE_RECOGNIZE_S` | `7` | how long it keeps trying to recognise someone |
| `ROBOT_CONCIERGE_SMILE_ON_MOTION` | `1` | `0` waits until it has seen someone before smiling |
| `ROBOT_CONCIERGE_FACE_HITS` | `2` | frames with a face before a stranger gets a hello |
| `ROBOT_CONCIERGE_SUMMARY_S` | `3600` | how often it logs a summary of wakes and greetings |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | how close a face must match; higher is stricter |
| `ROBOT_FACE_ID_MIN_MARGIN` | `0.08` | how far ahead of the runner-up a match must be to use a name |
| `ROBOT_FACE_ID_MIN_FACE_PX` | `60` | ignore faces smaller than this (in pixels) |
| `ROBOT_FACE_ID_AGGREGATE` | `max` | how several photos of one person combine: `max`, `topk`, `mean` or `centroid` |

Every other setting is in [config/README.md](../config/README.md).

## Changing what it says

The greetings live in `brain/concierge.py`: `GREETINGS` for people it knows (`[Name]` becomes their name),
`UNKNOWN_GREETINGS` for strangers, and `MOVEMENT_REFUSALS` for when you ask it to drive. One is picked at random
each time.

## Privacy

Faces are stored on the Pi as numbers (embeddings), in `~/.localairobot/memory.db`, and recognition never leaves
the device. Enrolling also keeps a reference photo in `~/.localairobot/photos/`. `./scripts/rag.sh --forget
"Name"` removes a person's record; delete their photo by hand. If you turn on Gemini, the conversation after
the greeting uses it, but the greeting itself stays local.
