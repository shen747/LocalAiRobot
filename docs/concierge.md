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

The motion path runs in **two stages**: a cheap look that answers *"is anyone there?"*, and — only if that
says yes — the expensive one that answers *"who is it?"*. The rate limit is applied **after** a look, chosen
from what the camera actually found. See [Why two stages](#why-two-stages) for the reasoning.

```
        (idle, asleep)
             │  wake word ───────► normal conversation turn (SAY/LOOK/REMEMBER; movement REFUSED)
             │
             │  PIR motion ──► EDGE FILTERS (brain/pir.py): drop spikes that fall within
             │                 ROBOT_PIR_HOLD_S, debounce one pulse train to one wake
             │                 (ROBOT_PIR_COOLDOWN_S, ~2s), and honour any OUTCOME
             │                 cooldown a previous look armed.
             │                        │  (survives the filters)
             │                        ▼
             │                 show FRIENDLY face (happy) -- HELD, never flashes
             │                 (ROBOT_CONCIERGE_SMILE_ON_MOTION=0 defers it to Stage A)
             │                        │
             │                        ▼
             │     ┌── STAGE A · presence probe: ROBOT_CONCIERGE_PROBE_TRIES frames ──┐
             │     │  detector ONLY, no recognition. Returns early on the first hit,  │
             │     │  so a real visitor costs ONE frame. ~99% of a chattering PIR's   │
             │     │  wakes die right here for ~120ms instead of a 15-frame burst.    │
             │     └───────────────┬──────────────────────────┬────────────────────┘
             │                nobody there                someone there
             │                     │                          │
             │                     ▼                          ▼
             │        arm SILENT cooldown          ┌── STAGE B · recognition window ──┐
             │        (…SILENT_COOLDOWN_S)         │  a frame every RECOGNIZE_INTERVAL │
             │        smile off, STAY SILENT       │  _S + local ArcFace match, until  │
             │        (DEBUG log only)             │  a match OR RECOGNIZE_S elapses   │
             │                     │               └────────────────┬─────────────────┘
             │                     │                                │
             │                     │     ┌──────────────────────────┼──────────────────────────┐
             │                     │  recognised                a face seen in            lost them again
             │                     │  (by name, and clear of     >= FACE_HITS frames       (walked past)
             │                     │   the runner-up by                │                        │
             │                     │   …MIN_MARGIN)                    │                        │
             │                     │        │                          ▼                        ▼
             │                     │        ▼                  generic name-less        arm SILENT cooldown
             │                     │  greet BY NAME            "Hello there!"           smile off, SILENT
             │                     │  "Welcome back, X!"               │                        │
             │                     │        └───────────┬──────────────┘                        │
             │                     │                    ▼                                       │
             │                     │        arm GREET cooldown (…GREET_COOLDOWN_S)              │
             │                     │        so the visitor you are already                      │
             │                     │        talking to can't re-trigger a greeting              │
             │                     │                    │                                       │
             └─────────────────────┴─ normal conversation loop ◄───────────────────────────────┘
                                      │  … silence …                    (silent paths skip it)
                                      ▼
                                back to sleep  (a SHORT goodbye -- see Notes & limits)
```

Implementation: `brain/pir.py` (edge filters + `arm_cooldown()`, then motion → `wake.cancel_listen()` →
`wait_for_trigger` returns `MOTION`), `brain/face_id.py` (detect + **align** + embed + match),
`brain/concierge.py` (persona + greeting arrays + `presence_probe()` and `recognize_within()`),
`brain/captures.py` (keeping the deciding frame), and the `MOTION` branch + movement gate in `brain/main.py`.

### Why two stages

Both halves of this design come straight out of a 19-hour field log, and both fix bugs that looked like
something else:

**The PIR "not waking" 90% of the time was actually too much throttle, not too few triggers.** The sensor
offered an edge every ~7 seconds round the clock — 3,786 accepted wakes, 1,232 of them between midnight and
6am in an empty house. The old design answered *every* accepted wake with a full ~15-frame recognition
burst, which was only affordable behind a 15-second blind cooldown on the sensor. That cooldown was applied
to the **edge**, before anything had looked at anything: 205 wakes/hour × 15s = **85% of every hour deaf to
a new arrival**. Walking up to Jarvis meant a random 0–15s wait before the camera even opened, by which time
you had turned away and the window saw nothing. Splitting the look in two makes the common case ~15× cheaper,
which pays for a ~2s debounce — so a real arrival is seen within two seconds, while idle detector work
*drops* from ~6,150/hour to ~500/hour.

**Recognition was returning noise because the crop was never aligned.** ArcFace/w600k is trained on 112×112
crops affine-warped so five landmarks land on fixed pixels. The old Haar cascade gives a box and no
landmarks, so the crop couldn't be aligned and the model saw out-of-distribution input: 6 matches out of 257
face frames (**2.3%**), every score squashed into 0.13–0.21, and the "closest" name spread evenly across all
35 enrolled people. One person stood in front of the camera for 14 consecutive frames and scored 0.142.
YuNet returns the landmarks, `face_id._align()` warps them onto the canonical template, and measured on this
repo's own enrolment photos that moves same-vs-different separation **0.334 → 0.491**, by-name recognition
**75% → 88%**, and impostors above threshold **0.76% → 0.12%**.

> **⚠️ Alignment requires re-enrolment.** Stored embeddings are produced by this same code path, so any made
> before alignment are themselves unaligned — and aligning only the live side compares apples to noise,
> which is *worse* than not aligning at all. After installing YuNet for the first time, run
> `bash enroll_all.sh` on the Pi and confirm with `./scripts/rag.sh --faces`.

## Hardware

- **PIR motion sensor** (e.g. Adafruit PIR, ADA189). **5 V power (⚠️ not 3.3 V — it won't trigger reliably
  on 3.3 V)**, **3.3 V digital OUT** → Pi-safe, no divider. Default wiring: **OUT → GPIO4 (pin 7), VCC → 5 V
  (pin 2), GND → GND (pin 6)** (`ROBOT_PIR_PIN=4`). Full
  pinout + tuning: **[docs/wiring/pir-sensor-wiring.md](wiring/pir-sensor-wiring.md)**; test it with
  `./scripts/pir_sensor_probe.sh`. Set `ROBOT_PIR_PIN=` empty for a wake-word-only concierge.
- **A camera** for the face pipeline — a **USB (UVC) module**, and you must apply **one** camera patch (the
  installer no longer sets a camera up for you; it deliberately writes no boot overlay):
  ```bash
  ./scripts/patches/pi5/3_camera_usb_webcam.sh 0      # any USB webcam
  ./scripts/patches/pi5/3_camera_usb_ir_1080p.sh 0    # ...or a 1080p IR-CUT night-vision module
  ```
  No reboot — a USB camera isn't a CSI device, so `./scripts/run_me.sh --reload` is enough. Confirm from the
  startup log: `camera: OpenCV/v4l2 …`, and check the resolution it reports is the one you asked for.
  **Recognition is the concierge's whole job, so frame detail matters more here than anywhere else** — see
  `ROBOT_CAMERA_SIZE` / `ROBOT_CAMERA_CAPTURE_WIDTH` below, and measure real face sizes in place with
  `./scripts/camera_face_probe.sh`. On an IR module, **night frames are greyscale infrared and won't match
  daylight enrolment photos** — enrol a few night shots per person too (`ROBOT_FACE_ID_AGGREGATE=max` keeps
  both sets under one name).
- **No motor board / ESC / ultrasonics needed.** Concierge never drives, so `brain/driver.py` doesn't even
  probe the PCA9685 (an absent motor board is expected here, not a hardware failure) — skip the `1_esc_*` and
  `2_sensor_*` patches entirely. The `I2C->PCA9685 …` lines you see at startup are the *simulated* PWM kit.

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
| `ROBOT_PIR_COOLDOWN_S` | `2` | **Edge debounce** (was 15). One sensor pulse train = one wake. **No longer the rate limiter** — the two outcome cooldowns below are, and they run *after* the camera has looked. `0` = react to every edge. |
| `ROBOT_CONCIERGE_SILENT_COOLDOWN_S` | `8` | **Outcome cooldown after "nobody there".** The real idle rate limiter — applied *with* evidence, so it throttles a lying sensor without throttling real visitors. `0` = off. |
| `ROBOT_CONCIERGE_GREET_COOLDOWN_S` | `60` | **Outcome cooldown after a greeting** (or a Halloween spook). Stops the visitor you're already talking to from re-triggering their own greeting — the field log shows the same person greeted twice, 30s apart. `0` = off. |
| `ROBOT_CONCIERGE_PROBE_TRIES` | `2` | **Stage A.** Cheap one-frame "is anyone there?" looks before committing to the full window. Returns early on the first hit, so a real visitor costs one frame. `0` = skip Stage A (old behaviour). |
| `ROBOT_CONCIERGE_PROBE_INTERVAL_S` | `0.15` | Pause between those Stage-A looks — short, to catch someone mid-stride, not to wait for them to arrive. |
| `ROBOT_CONCIERGE_SMILE_ON_MOTION` | `1` | Smile the instant motion fires, before anyone is confirmed. `0` = defer ~0.3s until Stage A has seen somebody — worth it if a chattering PIR makes the face flash all night. |
| `ROBOT_CONCIERGE_SUMMARY_S` | `3600` | One summary line per period: PIR wakes, how many died in the probe, how many greeted. Silent looks are DEBUG now, so this is your main diagnostic. `0` = off. |
| `ROBOT_PIR_CHATTER_N` / `_WINDOW_S` | `20` / `600` | **Health warning only.** More than N wakes in the window → one loud `PIR CHATTER` warning naming the likely causes. Never suppresses a wake. `0` = never warn. |
| `ROBOT_CONCIERGE_RECOGNIZE_S` | `7` | **Stage B.** Once someone is confirmed, how long to **hold the smile and keep retrying** recognition. Recognised → by name; face seen but unmatched → generic greeting (then chat); lost them → silent. `0` = single shot. |
| `ROBOT_CONCIERGE_RECOGNIZE_INTERVAL_S` | `0.2` | Pause **between** capture attempts in that window. ~35 independent looks per visitor; affordable now each frame runs the detector once rather than twice. `0` = flat out. |
| `ROBOT_CONCIERGE_FACE_HITS` | `2` | **Ghost-greeting guard.** How many frames must show a face before the *generic* greeting fires — a 1-frame gate let a single detector fluke greet an empty room. A recognised **match** still greets on its first frame. `1` = old behaviour. |
| `ROBOT_FACE_ID_MODEL` | `~/.localairobot/models/arcface.onnx` *(auto-downloaded)* | ArcFace recognition ONNX. Missing → face match off (smiles at everyone). |
| `ROBOT_FACE_ID_DETECTOR_BACKEND` | `auto` | **The most important accuracy key here.** `auto` = YuNet if present else Haar; `yunet` / `haar` to force. YuNet supplies the 5 landmarks that let the crop be **aligned** before ArcFace — without it scores collapse into noise (2.3% recognition in the field). |
| `ROBOT_FACE_ID_YUNET_MODEL` | `~/.localairobot/models/face_detection_yunet.onnx` *(auto-downloaded)* | The YuNet detector (~230 KB). **Installing it for the first time requires re-enrolling everyone.** |
| `ROBOT_FACE_ID_YUNET_SCORE` | `0.6` | YuNet confidence 0–1. Raise (0.7–0.9) if scenery registers as a face; lower (0.4–0.5) to notice people in poor light or steep profile. |
| `ROBOT_FACE_ID_MIN_MARGIN` | `0.08` | **Wrong-name guard.** How far clear of the runner-up the winner must be to say a **name**. A photo finish gets the friendly name-less greeting instead — being called someone else's name is worse than being greeted without one. `0` = threshold only. |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | Cosine match threshold. **Higher = stricter** (fewer false greetings, more misses). |
| `ROBOT_FACE_ID_INPUT` | `112` | Face model input size (px). |
| `ROBOT_FACE_ID_DETECTOR` | *(bundled)* | Optional Haar cascade XML, used **only on the Haar fallback path**; empty = OpenCV's default frontal-face cascade. |
| `ROBOT_FACE_ID_AGGREGATE` | `max` | How to combine a person's **multiple enrolled photos** when matching (only matters for 2+ images). `max` = best-matching photo wins (best recall). `topk` = mean of their top `ROBOT_FACE_ID_TOPK` photos. `mean` = average of all. `centroid` = match vs their averaged prototype. |
| `ROBOT_FACE_ID_TOPK` | `3` | For `ROBOT_FACE_ID_AGGREGATE=topk`: how many best-matching photos to average. |
| `ROBOT_FACE_ID_MIN_NEIGHBORS` | `5` | **Haar-only** strictness (YuNet uses `_YUNET_SCORE`): overlapping detections needed to believe a box. **Raise (6–8)** when scenery keeps registering as a face; lower (3–4) if real visitors go undetected in poor light. |
| `ROBOT_FACE_ID_MIN_FACE_PX` | `60` | Smallest face box accepted from a **live** frame, in a `ROBOT_CAMERA_SIZE`-px-square frame (default 512). Raise to ignore distant faces and reject small spurious boxes. |
| `ROBOT_FACE_ID_MIN_FACE_PX_ENROLL` | `40` | The same floor for **enrolment photos**, deliberately lower — a human chose that photo deliberately, once, offline. Two of this repo's 73 enrolment photos have ~53 px faces and were being dropped silently. |
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
  `ROBOT_FACE_ID_MIN_FACE_PX` with a low score is usually the detector firing on scenery.
- **Every score bunched into 0.13–0.21 with margins near zero, and the "closest" name changing every frame?**
  That is not a threshold problem and no amount of tuning will fix it — it is the **unaligned-crop** signature.
  Check the startup line: `grep 'face_id:' ` should say `detector=yunet, 5-point ALIGNED`. If it says
  `UNALIGNED crops` there is a loud warning right beneath it; run `./scripts/provision_face_id.sh` to install
  YuNet, **then `bash enroll_all.sh`** to rebuild the embeddings aligned.
- Aligned matches typically land around **0.45–0.60**. Lower `ROBOT_FACE_ID_THRESHOLD` only once the box sizes
  look sane *and* alignment is confirmed on.

**It greets me but never by name.** Look at the reason in the log:
`X cleared the threshold (0.41 >= 0.38) but only by +0.02 over Y (need +0.08) -- too close to call` means the
wrong-name guard fired: two enrolled people are confusable at that pose. Enrol more/better shots of both, or
lower `ROBOT_FACE_ID_MIN_MARGIN` if you would rather risk the occasional wrong name.

**It greeted nobody ("ghost greeting").** A detector false positive on scenery. The
`ROBOT_CONCIERGE_FACE_HITS=2` gate stops single flukes; if a *fixed* object keeps registering (a picture frame,
a patterned wall), raise `ROBOT_FACE_ID_YUNET_SCORE` to 0.7–0.9 (or `ROBOT_FACE_ID_MIN_NEIGHBORS` to 6–8 on the
Haar path), raise `ROBOT_CONCIERGE_FACE_HITS` to 3, and look at the saved `*-unknown.jpg` capture to see what it
thought was a face.

**`PIR CHATTER:` in the log / it wakes constantly with nobody there.** The sensor is false-triggering — a field
run logged **205 accepted wakes an hour round the clock, 1,232 of them between midnight and 6 am in an empty
house**. The software now absorbs this (the Stage-A probe kills such a wake in ~120 ms, and the silent outcome
cooldown paces them), so it is no longer urgent — but it is still a broken sensor. Usual causes, in order of
likelihood: the **sensitivity pot** turned right up; the **retrigger jumper** (HC-SR501 `H`/`L`) in the wrong
position, which makes the module free-run; the sensor aimed at the Pi's own **fan exhaust**, a heat source or a
sunlit window; VCC on a **noisy 5 V rail** (separate supply, or a 100 µF cap across VCC/GND). Probe it in
isolation with `./scripts/pir_sensor_probe.sh`.

> Note: `rejected_spike = 0` in the shutdown summary means the edges are **not** electrical noise — the sensor
> is genuinely asserting its output pulse, so look at the pot/jumper/aim before the wiring.

Read the shutdown summary and the hourly line together:

```
PIR GPIO 4: woke 431x, suppressed 402 (outcome cooldown) + 96 (debounce) + 0 (noise spikes)
concierge: last 60 min -- PIR 431 wake(s) (+402 held by cooldown, +96 debounce, +0 noise)
           | 428 silent (probe), 3 looked at, 1 lost in the window, 2 greeting(s) (1 by name)
```

`silent (probe)` ≫ `looked at` means the **sensor** is lying (hardware, above). `looked at` ≫ `greeting(s)`
means the **recogniser** is the problem (alignment / enrolment, above). That split is the whole point of the
line — previously both looked identical in the log and they need opposite fixes.

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
- **Recognition window:** a motion wake retries recognition for `ROBOT_CONCIERGE_RECOGNIZE_S` (default 7s)
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
