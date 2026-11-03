# Crawler mode

The default mode: a 1/10 RC crawler that drives on voice command, stops for obstacles and looks around for
things you name. Build it with [assembly.md](assembly.md) and set it up with [install.md](install.md).

## Talking to it

Say "hey jarvis", wait for "Yes?", then speak. After it answers you can keep talking without the wake word
until you've been quiet for `ROBOT_FOLLOWUP_SILENCE_S` (12 s).

| Say | It does |
|---|---|
| "go forward", "back up slowly" | drives for a few seconds, then stops |
| "go forward and bear left" | drives while steering (it can only turn while moving) |
| "stop" | stops at once |
| "look around" | explores: looks, says what it sees, takes small steps |
| "find the red ball" | looks, then steps toward it |
| "this is my wife Shani" | photographs her and remembers the name |
| "what's the capital of France?" | just answers |

## What it understands

The language model turns what you said into one small JSON command, and the Pi checks every field before
acting on it:

| Field | Values | Notes |
|---|---|---|
| `action` | `DRIVE`, `EXPLORE`, `FIND`, `STOP`, `SAY`, `REMEMBER` | `EXPLORE` and `FIND` only run if you used an explore phrase; otherwise it drives |
| `drive` | `FORWARD`, `REVERSE`, `STOP` | anything else means stop |
| `steer` | `LEFT`, `CENTER`, `RIGHT` | anything else means straight |
| `speed` | 0 to 100 | mapped onto your ESC's range, see [calibration](calibration.md) |
| `duration_ms` | 300 to 5000 | then the motor stops by itself |
| `target`, `note` | what to find, or the name and description to remember | |
| `reply` | short text | spoken by Piper |

The explore phrases are listed in `ROBOT_EXPLORE_TRIGGERS`. Checking them against what you actually said
means a misheard word can't send it wandering.

## Exploring and finding

"Look around" starts a loop. Each step, the model sees the camera frame, what it remembers and the distance
readings, picks the next move and decides when it's done. Limits on steps and time keep it bounded (twice as
generous with the faster Gemini brain), the front sensor gates every forward step, it backs away from walls,
and it holds still while it looks. Stop it any time with the wake word, the phone remote or Ctrl-C.

"Find the X" is a simpler stop, look, step loop toward a sighting. On the Pi's CPU each look takes tens of
seconds, so this is patient searching, not self-driving: there's no map.

Each look's description is saved, so what a Gemini run learned is still there offline.

## Memory

Sightings and the people and things you teach it go into a small SQLite database in `~/.localairobot/`. Say
"this is my wife Shani" and it photographs her, asks the vision model for a description, and stores the name,
relationship, description and photo. Each turn it adds what it knows to the prompt, so it remembers across
reboots. Look inside with `./scripts/rag.sh` (`--seen`, `--all`, `--forget <name>`).

## Safety

- It stops the motor on "stop", on any error, when something is close to the front sensor, and when a move's
  time runs out.
- **That is software only. Keep a physical kill switch on the motor battery**: if the program is killed, the
  PCA9685 keeps sending its last pulse.
- First drives: wheels off the ground, then slowly on the floor.
- A missing board or sensor is logged by name, and the robot carries on without it.

## Settings worth knowing

| Setting | What it changes |
|---|---|
| `ROBOT_MIN_FORWARD_SPEED`, `ROBOT_MAX_FORWARD_SPEED` and the reverse pair | the throttle range (your ESC patch sets them) |
| `ROBOT_STEER_CENTER`, `ROBOT_STEER_LEFT`, `ROBOT_STEER_RIGHT` | steering angles, from [calibration](calibration.md) |
| `ROBOT_EXPLORE_MAX_ITERS`, `ROBOT_EXPLORE_MAX_SECONDS` | how long exploring may go on |
| `ROBOT_EXPLORE_SPEED`, `ROBOT_EXPLORE_STEP_MS` | how fast and how far each explore step goes |
| `ROBOT_AI_BACKEND` | `local` (default), `gemini` or `mixed`, see [gemini-setup.md](gemini-setup.md) |

Every setting: [config/README.md](../config/README.md).
