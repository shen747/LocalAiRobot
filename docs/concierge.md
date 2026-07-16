# Concierge Mode

A **stationary desk persona** for Jarvis. Flip `ROBOT_CONCIERGE_MODE=1` (top of `config/robot.env`) and the
robot stops being a crawler car: it **never drives**, introduces itself as *"Jarvis the Concierge"*, and can
be woken by a **PIR motion sensor** as well as the wake word. When someone walks up, it recognises them with
a **strictly-local** face pipeline and greets them by name — or smiles and stays quiet if it doesn't know
them.

## What changes when it's ON

| | Normal robot | Concierge Mode |
|---|---|---|
| Persona | "Jarvis the crawler car" | "Jarvis the Concierge" (stationary) |
| Driving | drives / explores / finds | **refused** — every move/explore/find intent gets a random in-character line |
| Wake | wake word | wake word **+ PIR motion** |
| On motion wake | — | friendly face → local face match → greet by name **or** silent smile |
| Vision for the greeting | remote or local (config) | **strictly local** (cv2 + ArcFace ONNX) |
| Chat after greeting | — | hands off to the normal conversation loop (remote AI if enabled) |

Nothing else changes: STT/TTS, the face screen, memory, and the phone endpoint all work as usual.

## The state machine

```
        (idle, asleep)
             │  wake word ─────────────► normal conversation turn (SAY/LOOK/REMEMBER; movement REFUSED)
             │
             │  PIR motion ───────────►  show FRIENDLY face (happy)
             │                              │
             │                              ▼
             │                        capture webcam frame  ── STRICTLY LOCAL ──┐
             │                              │                                    │
             │                     cv2 detects a face?  ── no ──► smile, stay silent, back to sleep
             │                              │ yes (= a person is present)
             │                              ▼
             │                     ArcFace embed → cosine-match vs enrolled faces (RAG db)
             │                              │
             │                   match? ── no ──► smile, stay silent, back to sleep
             │                              │ yes → name
             │                              ▼
             │                     speak a random greeting ("Welcome back, <Name>!")
             │                              │
             │                              ▼
             └────────────────────  normal conversation loop (remote AI handoff if enabled)
                                            │  … silence …
                                            ▼
                                      back to sleep
```

Implementation: `brain/pir.py` (motion → `wake.cancel_listen()` → `wait_for_trigger` returns `MOTION`),
`brain/face_id.py` (detect + embed + match), `brain/concierge.py` (persona + the two message arrays), and the
`MOTION` branch + movement gate in `brain/main.py`.

## Hardware

- **PIR motion sensor** (e.g. Adafruit PIR, ADA189). 5 V power, **3.3 V digital OUT** → Pi-safe on any free
  GPIO. Set `ROBOT_PIR_PIN` to that BCM pin. Leave it empty to run concierge **wake-word-only** (no PIR).
- **USB webcam** for the face pipeline (`ROBOT_CAMERA_BACKEND=v4l2`; see the USB-webcam patch).

## Setup

```bash
# 1. Model + deps are installed by scripts/install_stack_pi5.sh / _jetson.sh (with the other models).
#    (Re)fetch on demand: ./scripts/provision_face_id.sh  (or ./scripts/patches/common/5_face_id_insightface.sh)

# 2. Enrol the people you want greeted (a few shots each, from slightly different angles)
./scripts/enroll_face.sh "Ramesh"
./scripts/enroll_face.sh "Shani" 8

# 3. Turn it on
sed -i 's/^ROBOT_CONCIERGE_MODE=.*/ROBOT_CONCIERGE_MODE=1/' config/robot.env
sed -i 's/^ROBOT_PIR_PIN=.*/ROBOT_PIR_PIN=17/'            config/robot.env   # your PIR's BCM pin

# 4. Run
./scripts/run_me.sh
```

## Config keys (all under the CONCIERGE MODE section, ignored unless the mode is on)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_CONCIERGE_MODE` | `0` | `1` = enable the whole mode (top of `config/robot.env`). |
| `ROBOT_PIR_PIN` | *(empty)* | PIR OUT pin (BCM). Empty = wake-word-only. |
| `ROBOT_FACE_ID_MODEL` | `~/.localairobot/models/arcface.onnx` *(auto-downloaded)* | ArcFace recognition ONNX. Missing → face match off (smiles at everyone). |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | Cosine match threshold. **Higher = stricter** (fewer false greetings, more misses). |
| `ROBOT_FACE_ID_INPUT` | `112` | Face model input size (px). |
| `ROBOT_FACE_ID_DETECTOR` | *(bundled)* | Optional Haar cascade XML; empty = OpenCV's default frontal-face cascade. |

## Dynamic responses

Both message sets live in `brain/concierge.py` and are picked with `random.choice`:

- **`MOVEMENT_REFUSALS`** (15) — spoken whenever you ask the Concierge to move/drive/explore/find.
- **`GREETINGS`** (26) — spoken on a face match; `[Name]` is replaced with the matched name.

Edit those lists to change the wording.

## Notes & limits

- **Privacy:** enrolled faces are stored **locally** as ArcFace embeddings (not images) in the RAG db
  (`~/.localairobot/memory.db`, table `faces`). Nothing leaves the device for recognition. Remove someone
  with `DELETE FROM faces WHERE name='…'` (or `mem.forget_face()`).
- **Accuracy:** detection uses OpenCV's Haar cascade (a bounding box, not landmark alignment), so matching is
  tuned for a handful of known desk visitors, not a crowd. Enrol several shots and tune
  `ROBOT_FACE_ID_THRESHOLD` if you get false greetings (raise it) or misses (lower it).
- **Kids / cloud:** the greeting pipeline is fully local, so the cloud child-safety filter that blocks remote
  vision (`PROHIBITED_CONTENT`) does **not** apply here — Concierge can recognise anyone you enrol. The
  *post-greeting chat* still uses the remote backend if enabled.
