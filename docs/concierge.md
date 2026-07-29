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
| On motion wake | — | friendly face **held ~5s** while it retries recognition → greet by name, **or** a generic hello if it can't place you (then chat), **or** silent if nobody's actually there |
| Vision for the greeting | remote or local (config) | **strictly local** (cv2 + ArcFace ONNX) |
| Chat after greeting | — | hands off to the normal conversation loop (remote AI if enabled) |

Nothing else changes: STT/TTS, the face screen, memory, and the phone endpoint all work as usual.

## The state machine

```
        (idle, asleep)
             │  wake word ───────► normal conversation turn (SAY/LOOK/REMEMBER; movement REFUSED)
             │
             │  PIR motion ─────►  show FRIENDLY face (happy) -- HELD, never flashes
             │                        │
             │                        ▼
             │     ┌── RECOGNITION WINDOW: ROBOT_CONCIERGE_RECOGNIZE_S (default 5s) ──┐
             │     │  keep capturing frames + local ArcFace match until a match OR    │
             │     │  the window elapses (one bad frame is no longer a missed greet)  │
             │     └──────────────────────────────┬─────────────────────────────────┘
             │                                     │
             │          ┌──────────────────────────┼──────────────────────────┐
             │     recognised                  a face was seen             no face at all
             │     (by name)                   but never matched           the whole window
             │          │                           │                           │
             │          ▼                           ▼                           ▼
             │   greet BY NAME             generic name-less            smile off, STAY SILENT,
             │   "Welcome back, X!"        "Hello there! Welcome."      back to sleep (a cat /
             │          │                           │                    curtain isn't greeted)
             │          └─────────────┬─────────────┘                           │
             │                        ▼                                         │
             └──────────  normal conversation loop  ◄──────────────────────────┘ (silent path skips it)
                                      │  … silence …
                                      ▼
                                back to sleep
```

Implementation: `brain/pir.py` (motion → `wake.cancel_listen()` → `wait_for_trigger` returns `MOTION`),
`brain/face_id.py` (detect + embed + match), `brain/concierge.py` (persona + greeting arrays +
`recognize_within()`, the retry loop), and the `MOTION` branch (recognition window) + movement gate in
`brain/main.py`.

## Hardware

- **PIR motion sensor** (e.g. Adafruit PIR, ADA189). **5 V power (⚠️ not 3.3 V — it won't trigger reliably
  on 3.3 V)**, **3.3 V digital OUT** → Pi-safe, no divider. Default wiring: **OUT → GPIO4 (pin 7), VCC → 5 V
  (pin 2), GND → GND (pin 6)** (`ROBOT_PIR_PIN=4`). Full
  pinout + tuning: **[docs/wiring/pir-sensor-wiring.md](wiring/pir-sensor-wiring.md)**; test it with
  `./scripts/pir_sensor_probe.sh`. Set `ROBOT_PIR_PIN=` empty for a wake-word-only concierge.
- **USB webcam** for the face pipeline (`ROBOT_CAMERA_BACKEND=v4l2`; see the USB-webcam patch).

## Setup

```bash
# 1. Model + deps are installed by scripts/install_stack_pi5.sh / _jetson.sh (with the other models).
#    (Re)fetch on demand: ./scripts/provision_face_id.sh  (or ./scripts/patches/common/5_face_id_insightface.sh)

# 2. Enrol the people you want greeted. enroll_person.sh stores a face embedding + a description + a "say-as"
#    (a phonetic respelling SPOKEN in the greeting; "" = greet with the real name) and REPLACES that person on
#    a re-run (idempotent). Args: "Name" "description" "say-as" [images]. From PHOTO FILES you already have:
./scripts/enroll_person.sh "Ramesh" "my creator, wears glasses" "Ruh-mesh" ~/people_named/Ramesh.jpg ~/people_named/Ramesh_02.jpg
#    ...or capture live from the webcam (stop the brain first -- it holds the camera):
./scripts/enroll_person.sh --camera 8 "Ramesh" "my creator" "Ruh-mesh"   # 8 shots; combine with files to enrol both

#    A crowd? name photos <Name>.jpg (+ <Name>_02.jpg for more) and generate one batch script on your PC:
#      ./scripts/create_enrollments.sh   ->  edit descriptions in enroll_all.sh  ->  scp + run on the Pi

# 3. Turn it on  (ROBOT_PIR_PIN already defaults to 4 = GPIO4/pin 7; only change it if you wired OUT elsewhere)
sed -i 's/^ROBOT_CONCIERGE_MODE=.*/ROBOT_CONCIERGE_MODE=1/' config/robot.env

# 4. Run
./scripts/run_me.sh
```

## Config keys (all under the CONCIERGE MODE section, ignored unless the mode is on)

| Key | Default | What it does |
|---|---|---|
| `ROBOT_CONCIERGE_MODE` | `0` | `1` = enable the whole mode (top of `config/robot.env`). |
| `ROBOT_PIR_PIN` | *(empty)* | PIR OUT pin (BCM). Empty = wake-word-only. |
| `ROBOT_CONCIERGE_RECOGNIZE_S` | `5` | On a motion wake, how long to **hold the smile and keep retrying** recognition before greeting. Recognised → by name; face seen but unmatched → generic greeting (then chat); no face at all → silent. `0` = single shot (old one-frame behavior). Raise it if people move fast past the camera. |
| `ROBOT_FACE_ID_MODEL` | `~/.localairobot/models/arcface.onnx` *(auto-downloaded)* | ArcFace recognition ONNX. Missing → face match off (smiles at everyone). |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | Cosine match threshold. **Higher = stricter** (fewer false greetings, more misses). |
| `ROBOT_FACE_ID_INPUT` | `112` | Face model input size (px). |
| `ROBOT_FACE_ID_DETECTOR` | *(bundled)* | Optional Haar cascade XML; empty = OpenCV's default frontal-face cascade. |
| `ROBOT_FACE_ID_AGGREGATE` | `max` | How to combine a person's **multiple enrolled photos** when matching (only matters for 2+ images). `max` = best-matching photo wins (best recall). `topk` = mean of their top `ROBOT_FACE_ID_TOPK` photos. `mean` = average of all. `centroid` = match vs their averaged prototype. |
| `ROBOT_FACE_ID_TOPK` | `3` | For `ROBOT_FACE_ID_AGGREGATE=topk`: how many best-matching photos to average. |

## Dynamic responses

The message sets live in `brain/concierge.py` and are picked with `random.choice`:

- **`MOVEMENT_REFUSALS`** (15) — spoken whenever you ask the Concierge to move/drive/explore/find.
- **`GREETINGS`** (26) — spoken on a face **match**; `[Name]` is replaced with the matched name.
- **`UNKNOWN_GREETINGS`** (50) — name-less greetings spoken to a visitor who's **present but not recognised**
  after the recognition window, so an unenrolled guest still gets a warm hello (and can then chat).

Edit those lists to change the wording.

## How the robot remembers (RAG)

The memory DB (`~/.localairobot/memory.db`, stdlib SQLite — inspect with `./scripts/rag.sh`) holds four
things: **enrolled faces** (embeddings), **taught facts** (people/things you told it about — "*this is my
wife Shani*"), **explore scenes** (rich descriptions from agentic exploration), and **recently seen
objects**. Before every reply, a short summary is prepended to the model's prompt so it isn't starting
from scratch.

That summary is now **relevance-ranked, not just "the newest"**: the taught facts and explore scenes are
scored against *what was actually asked* (SQLite **FTS5 / BM25**) — so "*who is Shani?*" leads with Shani,
and "*what did you see in the garage?*" surfaces the garage scene even if it's old. The best matches lead,
the newest fill any remaining room, and if nothing matches (or a SQLite build lacks FTS5) it falls back to
plain newest-first — identical to before. This keeps the prompt **on-topic and tight**, which matters most
for the small local model's context window.

- **See what it would retrieve:** `./scripts/rag.sh --search "the garage"` (BM25-ranked facts + scenes).
- **After raw sqlite edits:** `./scripts/rag.sh --reindex` rebuilds the search index (writes via the app
  keep it in sync automatically).
- **Explore scenes are protected:** routine "look" churn is pruned to `ROBOT_MEMORY_RETAIN` (5000) while
  scenes prune on their own budget `ROBOT_MEMORY_RETAIN_SCENES` (500), so a busy session never throws away
  the costly explore summaries a later offline run reuses.

This applies in **every** mode (crawler too); it's just most visible here, where taught facts drive the
greetings.

## Notes & limits

- **Privacy:** enrolled faces are stored **locally** as ArcFace embeddings (not images) in the RAG db
  (`~/.localairobot/memory.db`, table `faces`). Nothing leaves the device for recognition. Remove someone
  with `DELETE FROM faces WHERE name='…'` (or `mem.forget_face()`). Note that `enroll_person.sh` and the voice
  *"remember this is…"* flow additionally keep a **reference JPEG** under `~/.localairobot/photos/` and a text
  fact in table `facts` — clear those with `./scripts/rag.sh --forget "Name"` and by deleting the JPEG.
- **Accuracy:** detection uses OpenCV's Haar cascade (a bounding box, not landmark alignment), so matching is
  tuned for a handful of known desk visitors, not a crowd. Enrol several shots and tune
  `ROBOT_FACE_ID_THRESHOLD` if you get false greetings (raise it) or misses (lower it).
- **Recognition window:** a motion wake retries recognition for `ROBOT_CONCIERGE_RECOGNIZE_S` (default 5s)
  instead of judging one frame — the fix for *sporadic* greetings that used to "flash" the smile and give
  up. Each wake logs the attempt count and the **best score** seen, so you can tell how close a miss was and
  tune `ROBOT_FACE_ID_THRESHOLD` accordingly (set the window to `0` to return to a single frame).
- **Multiple photos per person:** give `enroll_person.sh` several photos (files) and/or `--camera N` webcam
  shots in one call — each becomes an embedding row under their name. Re-running REPLACES that person (so the
  args are their complete enrolment; no duplicates). On a match, all of a person's photos are combined per
  `ROBOT_FACE_ID_AGGREGATE` (default `max` = the best-matching photo wins, so more photos = better recall;
  switch to `topk`/`centroid` if a single stray shot causes false greetings). See who's enrolled and with how
  many photos: `./scripts/rag.sh --faces`.
- **Kids / cloud:** the greeting pipeline is fully local, so the cloud child-safety filter that blocks remote
  vision (`PROHIBITED_CONTENT`) does **not** apply here — Concierge can recognise anyone you enrol. The
  *post-greeting chat* still uses the remote backend if enabled.
