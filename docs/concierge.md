# Concierge Mode

A **stationary persona** for Jarvis. Flip `ROBOT_CONCIERGE_MODE=1` (top of `config/robot.env`) and the
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
             │  PIR motion ──► TRIGGER FILTERS (brain/pir.py): drop spikes that fall
             │                 within ROBOT_PIR_HOLD_S, then rate-limit to one wake
             │                 per ROBOT_PIR_COOLDOWN_S. A PIR next to a warm Pi 5
             │                 chatters; without these it wakes the camera all night.
             │                        │  (survives the filters)
             │                        ▼
             │                 show FRIENDLY face (happy) -- HELD, never flashes
             │                        │
             │                        ▼
             │     ┌── RECOGNITION WINDOW: ROBOT_CONCIERGE_RECOGNIZE_S (default 5s) ──┐
             │     │  capture a frame every ROBOT_CONCIERGE_RECOGNIZE_INTERVAL_S +    │
             │     │  local ArcFace match, until a match OR the window elapses        │
             │     │  (one bad frame is no longer a missed greeting)                  │
             │     └──────────────────────────────┬─────────────────────────────────┘
             │                                     │
             │          ┌──────────────────────────┼──────────────────────────┐
             │     recognised                  a face seen in              no face (or fewer
             │     (by name)                   >= FACE_HITS frames         than FACE_HITS)
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

Implementation: `brain/pir.py` (trigger filters, then motion → `wake.cancel_listen()` → `wait_for_trigger`
returns `MOTION`), `brain/face_id.py` (detect + embed + match), `brain/concierge.py` (persona + greeting arrays
+ `recognize_within()`, the paced retry loop), `brain/captures.py` (keeping the deciding frame), and the
`MOTION` branch (recognition window) + movement gate in `brain/main.py`.

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
| `ROBOT_PIR_HOLD_S` | `0.25` | **Noise filter.** Re-read the pin this long after a trigger; if it has already fallen it was an electrical spike, not a person. `0` = trust every edge. |
| `ROBOT_PIR_COOLDOWN_S` | `15` | **Rate limit.** After an accepted wake, ignore triggers for this long. A real visitor lingers (nothing is missed), but a chattering sensor can't monopolise the robot. `0` = no cooldown. |
| `ROBOT_PIR_CHATTER_N` / `_WINDOW_S` | `20` / `600` | **Health warning only.** More than N wakes in the window → one loud `PIR CHATTER` warning naming the likely causes. Never suppresses a wake. `0` = never warn. |
| `ROBOT_CONCIERGE_RECOGNIZE_S` | `5` | On a motion wake, how long to **hold the smile and keep retrying** recognition before greeting. Recognised → by name; face seen but unmatched → generic greeting (then chat); no face at all → silent. `0` = single shot (old one-frame behavior). Raise it if people move fast past the camera. |
| `ROBOT_CONCIERGE_RECOGNIZE_INTERVAL_S` | `0.25` | Pause **between** capture attempts in that window. Unpaced the loop ran the camera flat out (~20 stills/s, ~100 near-identical frames per wake); 0.25 s gives ~20 independent looks for a fifth of the CPU and log volume. `0` = flat out. |
| `ROBOT_CONCIERGE_FACE_HITS` | `2` | **Ghost-greeting guard.** How many frames must show a face before the *generic* greeting fires. Haar false-positives on scenery, and a 1-frame gate let a single fluke greet an empty room. A recognised **match** still greets on its first frame. `1` = old behaviour. |
| `ROBOT_FACE_ID_MODEL` | `~/.localairobot/models/arcface.onnx` *(auto-downloaded)* | ArcFace recognition ONNX. Missing → face match off (smiles at everyone). |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | Cosine match threshold. **Higher = stricter** (fewer false greetings, more misses). |
| `ROBOT_FACE_ID_INPUT` | `112` | Face model input size (px). |
| `ROBOT_FACE_ID_DETECTOR` | *(bundled)* | Optional Haar cascade XML; empty = OpenCV's default frontal-face cascade. |
| `ROBOT_FACE_ID_AGGREGATE` | `max` | How to combine a person's **multiple enrolled photos** when matching (only matters for 2+ images). `max` = best-matching photo wins (best recall). `topk` = mean of their top `ROBOT_FACE_ID_TOPK` photos. `mean` = average of all. `centroid` = match vs their averaged prototype. |
| `ROBOT_FACE_ID_TOPK` | `3` | For `ROBOT_FACE_ID_AGGREGATE=topk`: how many best-matching photos to average. |
| `ROBOT_FACE_ID_MIN_NEIGHBORS` | `5` | Haar strictness: overlapping detections needed to believe a box. **Raise (6–8)** when scenery keeps registering as a face; lower (3–4) if real visitors go undetected in poor light. |
| `ROBOT_FACE_ID_MIN_FACE_PX` | `60` | Smallest accepted face box, in a `ROBOT_CAMERA_SIZE`-px-square frame (default 512). Raise to ignore distant faces and reject small spurious boxes. |
| `ROBOT_FACE_ID_WARMUP` | `1` | Load the face model at **startup** (background) instead of lazily inside the first visitor's recognition window. |

## Detect captures — seeing what Jarvis saw

Every motion wake keeps the **one frame that decided the outcome** in `~/detect-captures`, so a ghost greeting
or a missed friend is something you can *look at* rather than guess about. The filename says which:

```
20260730-074742-known-ramesh.jpg    recognised → greeted by name (this is the matching frame)
20260730-074743-unknown.jpg         a face was seen but not matched → generic greeting
20260730-013120-noface.jpg          motion but no face → stayed silent   (only with MODE=all)
```

Retention is deliberately belt-and-braces, because a 24/7 greeter would otherwise fill the NVMe:

- a **cron job** purges by age daily — `scripts/purge_detect_captures.sh`, scheduled by
  `scripts/install_detect_capture_cron.sh`, which `run_me.sh --install-boot`, `install_stack_pi5.sh` and
  `install_stack_jetson.sh` all call for you (tagged + idempotent; other crontab entries are untouched);
- the **brain itself** prunes the folder at startup against the same limits, so a robot whose cron never got
  installed still stays bounded.

| Key | Default | What it does |
|---|---|---|
| `ROBOT_DETECT_CAPTURE` | `1` | Keep the deciding frame of each motion wake. `0` = keep nothing (and no cron entry is installed). |
| `ROBOT_DETECT_CAPTURE_DIR` | `~/detect-captures` | Where they go. Must be a **dedicated** folder — the purge script refuses to run against `$HOME` or `/`. |
| `ROBOT_DETECT_CAPTURE_MODE` | `greeted` | `greeted` = only frames that produced a greeting (a handful a day). `all` = also every silent look — turn on for a session when chasing false PIR triggers, then off. |
| `ROBOT_DETECT_CAPTURE_KEEP_DAYS` | `7` | Delete captures older than this. `0` = no age limit. |
| `ROBOT_DETECT_CAPTURE_MAX_MB` | `2000` | Hard cap: after the age pass, delete **oldest-first** until under this. `0` = no cap. |
| `ROBOT_DETECT_CAPTURE_CRON` | `1` | Install the daily purge cron entry during install / `--install-boot`. |
| `ROBOT_DETECT_CAPTURE_CRON_SCHEDULE` | `17 4 * * *` | 5-field cron schedule for that purge. |

```bash
ls -lt ~/detect-captures | head                     # what Jarvis saw most recently
./scripts/purge_detect_captures.sh --dry-run        # what the purge would delete
./scripts/purge_detect_captures.sh                  # purge now
crontab -l | grep detect-captures                   # confirm the schedule
./scripts/install_detect_capture_cron.sh --remove   # unschedule
```

## Troubleshooting

**It smiled but never greeted me.** Check what the recognition window actually saw:

```bash
journalctl -u robot.service | grep -E 'concierge:|face_id:' | tail -30
```

- `motion but no face in N try(s) (0 face hit(s) …)` — the camera genuinely saw no face. If you *were* there:
  the frame may be too dark (night frames come out ~5 KB vs ~17 KB in daylight — add light, the Haar detector
  needs a visible frontal face), or you were too far away for `ROBOT_FACE_ID_MIN_FACE_PX`.
- `face_id: no match (best max 0.15 < 0.38; face box 96px)` — a face **was** found but scored far below the
  threshold. A big box with a low score means your enrolment doesn't cover that pose/lighting: re-enrol with
  several shots (`./scripts/enroll_person.sh "Name" "desc" "say-as" a.jpg b.jpg --camera 3`). A box barely over
  `ROBOT_FACE_ID_MIN_FACE_PX` with a low score is usually Haar firing on scenery — raise
  `ROBOT_FACE_ID_MIN_NEIGHBORS`.
- Matches typically land around **0.40–0.52** with a good enrolment; scores stuck at 0.1–0.25 mean the crop is
  bad, not that the threshold is wrong. Lower `ROBOT_FACE_ID_THRESHOLD` only once the box sizes look sane.

**It greeted nobody ("ghost greeting").** That is a Haar false positive on scenery. The
`ROBOT_CONCIERGE_FACE_HITS=2` gate stops single flukes; if a *fixed* object keeps registering (a picture frame,
a patterned wall), raise `ROBOT_FACE_ID_MIN_NEIGHBORS` to 6–8, raise `ROBOT_CONCIERGE_FACE_HITS` to 3, and look
at the saved `*-unknown.jpg` capture to see what it thought was a face.

**`PIR CHATTER:` in the log / it wakes constantly with nobody there.** The sensor is false-triggering — a field
run logged **~508 wakes an hour, all night, with an empty house**, each one waking the camera. Usual causes, in
order of likelihood: VCC on a **noisy 5 V rail** (give it a separate supply, or a 100 µF cap across VCC/GND);
the sensor aimed at the Pi's own **fan exhaust**, a heat source or a sunlit window; the **sensitivity pot**
turned right up. Probe it in isolation with `./scripts/pir_sensor_probe.sh`, and meanwhile raise
`ROBOT_PIR_COOLDOWN_S` / `ROBOT_PIR_HOLD_S` (or clear `ROBOT_PIR_PIN` for a wake-word-only concierge). Each run
also logs a PIR summary on shutdown — a large `suppressed` count next to a small `woke` count is the signature.

**The log is enormous.** Per-frame camera capture lines are `DEBUG` now (they were `INFO`, and a recognition
burst turned that one line into ~144 MB of a four-day log). If you run with `--verbose` / `ROBOT_LOG_LEVEL=DEBUG`
they come back — *and* the power monitor drops to 1 Hz, which is another ~170 k lines a day. Keep `--verbose`
for bench sessions, not for a 24/7 greeter.

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
  tuned for a handful of known visitors, not a crowd. Enrol several shots and tune
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
