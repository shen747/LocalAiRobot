# 🎃 HALLOWEEN MODE — feature plan (for approval)

A **spooky sub-mode of Concierge Mode**: when a trick-or-treater trips the PIR motion sensor, instead of a
friendly "Hello, [Name]!", Jarvis greets them with one of **50+ kid-friendly spooky phrases** and shows a
**spooky face**, then drops into the normal conversation loop so it can banter back. Everything else
(stationary, PIR wake, strictly-local face detection, movement always refused, sleep on silence) is
**identical to Concierge** — Halloween Mode only changes *what it says*, *what face it shows*, and *who it
reacts to*.

> ✅ **IMPLEMENTED** — user guide: [docs/halloween.md](docs/halloween.md). Final decisions locked in during
> the build: spook **everyone** detected · face = **random still** (a different monster per visitor) · a
> **spooky earcon** plays before the phrase · the **spooky persona is always on** · faces live in
> **`brain/faces/halloween/`**. The plan below is the as-designed record; the ONLY config flag that shipped is
> **`ROBOT_HALLOWEEN_MODE`** — the other flags in §3 were dropped as unnecessary given those decisions, and
> §11's open questions are all answered above.
>
> 🔧 **As-built amendments (post-testing).** On a real porch the face-detect gate (§7) proved wrong twice
> over: the door camera **captured fine but Haar found no face** — costumes/**masks** defeat face detection,
> and at doorway distance the face is often too small — so *every* motion logged `motion but no face` and the
> spooky face only **flickered** before reverting. Two fixes shipped:
> 1. **Trigger on motion, not a detected face.** The PIR wake *is* the "someone's there" signal; the camera
>    capture + `face_id.detect()` gate is **removed** from the spook path (`detect()` stays in `face_id.py`,
>    just unused here). A masked kid now gets spooked; §7 no longer gates the scare.
> 2. **Louder + longer.** The face is now **held** through the whole greeting (`say(..., talk_expr/end_expr =`
>    the spooky state`)`), and the earcon is a set of **LOUD synthetic stingers** (`sounds/spook_sounds/`
>    `stinger_*.wav` from `scripts/generate_spook_stingers.sh`) played **blocking / in full** before the
>    phrase — replacing the quiet Piper cackle. No new config: both are on by default.

---

## 1. Concept & gating

- New flag **`ROBOT_HALLOWEEN_MODE` (0/1, default 0)**.
- It is a **sub-mode of Concierge**: it does nothing on its own.
  **`halloween.enabled() == (concierge.enabled() AND ROBOT_HALLOWEEN_MODE truthy)`.**
  If `ROBOT_CONCIERGE_MODE=0`, Halloween Mode is inert no matter what `ROBOT_HALLOWEEN_MODE` is.
- Because Halloween runs *inside* Concierge, it **reuses Concierge's PIR motion wake, the stationary
  persona machinery, and the movement-refusal path with zero new wiring** — the PIR is already armed
  whenever `concierge.enabled()` is true.

**One-liner to turn it on (once concierge is set up):**
```bash
sed -i -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=1|' \
       -e 's|^ROBOT_HALLOWEEN_MODE=.*|ROBOT_HALLOWEEN_MODE=1|' config/robot.env
```

---

## 2. Behaviour — the flow (diffed against Concierge)

The motion-wake path in `main.py` (currently `trigger == "MOTION"`) gets a Halloween branch. Side-by-side:

| Step | Concierge (today) | **Halloween Mode (new)** |
|---|---|---|
| Wake | PIR motion → `MOTION` | **same** (reuses concierge PIR) |
| Face | show `happy` the instant motion is seen | show a **`spooky`** face |
| Who | capture → `face_id.identify()` → **known name?** | capture → `face_id.detect()` → **any person present?** (kids aren't enrolled) |
| No person | motion but no face → smile silently, sleep | **same** (motion but no face → stay quiet, sleep) |
| Greeting | `say(concierge.greeting(name))` — by name, only for KNOWN faces | **`say(halloween.spooky_greeting())`** — a random spooky phrase for **anyone detected** |
| Conversation | fall through to the follow-up loop | **same** — falls through to the identical loop |
| Persona (LLM identity) | `concierge.PERSONA_CAPS` | **`halloween.PERSONA`** (spooky character, so replies stay in-theme) |
| Movement | every DRIVE/EXPLORE/FIND refused with a concierge line | refused with a **spooky** line (`halloween.refusal()`) |
| Sleep | drowse to `sleeping` after `ROBOT_FOLLOWUP_SILENCE_S` | **same** |

**Key difference from Concierge:** Concierge greets *recognised* people and is *silent* to strangers.
Halloween **spooks any detected person** (a face is present) — which is exactly what you want for kids at
the door. Optional refinement below lets it *recognise family and spook strangers* instead.

---

## 3. Config flags (all in `config/robot.env`, in the CONCIERGE section)

| Key | Default | Meaning |
|---|---|---|
| `ROBOT_HALLOWEEN_MODE` | `0` | master switch (needs `ROBOT_CONCIERGE_MODE=1` too) |
| `ROBOT_HALLOWEEN_PERSONA` | `1` | swap the LLM identity to the spooky character so *chat* stays in-theme (0 = spook only on the greeting, normal concierge persona after) |
| `ROBOT_HALLOWEEN_SPOOK_KNOWN` | `1` | `1` = spook everyone (default). `0` = greet **enrolled** faces normally by name, spook only **strangers** |
| `ROBOT_HALLOWEEN_FACE_STATE` | `spooky` | which face folder/state to show (lets you theme it) |

No new sensor/pin config — the PIR (`ROBOT_PIR_PIN`) is shared with Concierge.

---

## 4. New module — `brain/halloween.py` (mirrors `brain/concierge.py`)

Pure stdlib, safe to import anywhere. Contents:
- `enabled()` → `concierge.enabled() and env truthy(ROBOT_HALLOWEEN_MODE)`.
- `SPOOKY_GREETINGS` — a list of **50+** phrases (see §5).
- `spooky_greeting()` → `random.choice(SPOOKY_GREETINGS)` (goes through the same TTS pronunciation +
  say() path as concierge greetings).
- `PERSONA` — the spooky identity string with `{name}` (see §5).
- `SPOOKY_REFUSALS` + `refusal()` — in-character movement refusals (still stationary).
- `FACE_STATE` — `os.environ.get("ROBOT_HALLOWEEN_FACE_STATE", "spooky")`.

`concierge.py` is untouched except that `main.py` chooses `halloween.*` over `concierge.*` when
`halloween.enabled()`.

---

## 5. The 50+ spooky phrases (tone: **fun-spooky, kid-safe**)

Trick-or-treaters are mostly little kids, so the phrases are **playfully** spooky — a fright then a wink,
never nightmare fuel. Many end with an invitation to respond, which flows naturally into the conversation
loop. Samples (the full 50+ ship in `halloween.py`):

1. "Welcome, brave soul… few who knock on this door return unchanged! …Just kidding — happy Halloween!"
2. "I've been *waiting* for you in the dark… do you dare come closer for a treat?"
3. "BOO! …Did I get you? That is a *frightfully* wonderful costume!"
4. "Who dares disturb my slumber on this haunted night…? Oh — it's just a marvellous little goblin!"
5. "The spirits whisper your name… they say you have *excellent* taste in candy."
6. "Beware! I can see your future… it is absolutely FULL of chocolate."
7. "A cold wind blows through the doorway… and it carries the smell of your amazing costume!"
8. "You've awakened the doorway guardian! Prove your bravery — give me your best monster growl!"
9. "Tremble, mortal… or just grab a treat. That works too."
10. "Careful — this porch is haunted by a friendly robot who *loves* Halloween!"

**Persona (draft):**
> "I am {name}, a mischievous Halloween spirit haunting this doorway. I speak in eerie, playful frights to
> delight trick-or-treaters, but I am friendly at heart and never truly scary. I do NOT move or drive — I am
> rooted to this haunted spot. I greet visitors with a spooky flourish, then happily banter, tell spooky
> jokes, and compliment their costumes. My mobile counterpart is Jarvis the crawler car."

---

## 6. The 10 spooky faces — `brain/faces/spooky/01.png … 10.png`

The face system loads frames from `brain/faces/<state>/NN.png`; a state with ≥2 frames animates, 1 frame is
static, 0 falls back to a procedural drawing. So Halloween's faces are simply a **new `spooky` state folder
with 10 frames**. Ten Pillow-drawn motifs (stylised, on-model with the existing procedural face):

| # | Motif | # | Motif |
|---|---|---|---|
| 01 | Jack-o'-lantern (glowing triangle eyes, carved jagged grin) | 06 | Spider (round body, 8 legs, many eyes) |
| 02 | Ghost (pale, hollow eyes, wavy sheet) | 07 | Bat (spread wings, red eyes, fangs) |
| 03 | Skull (black eye sockets, gritted teeth) | 08 | Frankenstein (green, flat head, neck bolts, stitches) |
| 04 | Glowing red eyes in the dark (+ faint grin) | 09 | Vampire (pale, fangs, red eyes) |
| 05 | Cheshire evil grin (huge toothy smile, half-lids) | 10 | "Glitch/static" distorted face (zigzag mouth) |

**Generation:** a `scripts/generate_halloween_faces.sh` (same pattern as `generate_faces.sh` — finds a
Python with Pillow, renders at the panel size, skips existing, `--force` to overwrite) draws the 10 into
`brain/faces/spooky/` and they're **committed** so a fresh clone already has them. A procedural
`_render("spooky", …)` fallback is added to `face.py` so a missing PNG still shows *something* spooky.

**Display choice (open question §11):** either (a) **flicker** — animate through all 10 for a creepy
strobe, or (b) **random still** — a *different* monster per trick-or-treater, frozen for that encounter.

---

## 7. Person detection — "someone's at the door" without enrolment

Concierge only reacts to **recognised** faces (`face_id.identify()` → name). Kids aren't enrolled, so
Halloween needs a plain **"is a face present?"** signal. `face_id.py` already detects the largest face
(Haar) before it embeds/recognises — so we add a tiny **`FaceID.detect(jpeg) -> bool`** (a face is present,
recognition not required) and the Halloween branch spooks whenever `detect()` is true. Still **100% local**
(OpenCV Haar only, no cloud, no VLM) — same privacy guarantee as Concierge. Degrades gracefully: if
OpenCV/onnxruntime aren't installed, `detect()` returns False and it just… doesn't spook.

---

## 8. Files to add / change (checklist)

**New:**
- `brain/halloween.py` — flag, phrases, persona, refusals, face-state.
- `brain/faces/spooky/01.png … 10.png` — the 10 committed spooky frames.
- `scripts/generate_halloween_faces.sh` — the Pillow generator.
- `docs/halloween.md` — user guide (enable, wire, the flow, tuning) — linked from README.

**Edit:**
- `brain/main.py` — `import halloween`; persona pick (§2); the `MOTION` branch (spook path); the
  movement-refusal line picks `halloween.refusal()` when enabled.
- `brain/face_id.py` — add `detect(jpeg) -> bool`.
- `brain/face.py` — procedural `_render("spooky", …)` fallback (robustness).
- `config/robot.env` — the flags in §3 (in the CONCIERGE section) + `ROBOT_HALLOWEEN_MODE` mirrored near
  `ROBOT_CONCIERGE_MODE` at the top.
- `scripts/install_stack_pi5.sh` / `_jetson.sh` — run the halloween face generator alongside
  `generate_faces.sh` (missing-only), so a fresh install has the frames.
- `README.md` — one line pointing at `docs/halloween.md`.

**Untouched:** `concierge.py`, the PIR wake, the conversation loop, the driver — Halloween is purely a
`main.py` branch + new data.

---

## 9. Safety & non-goals

- **Still 100% stationary** — every drive/explore/find is refused (spookily). No motor behaviour changes.
- **Strictly local** face detection (Haar) — no images leave the device, same as Concierge.
- **Kid-safe by design** — phrases are playful, not traumatic; the fright always resolves into friendliness.
- **Non-goal:** dispensing real candy / servo-triggered props (could be a later add-on via the face panel
  or a GPIO relay — out of scope for v1).

---

## 10. Testing

- **Unit-ish:** `python -c "import halloween; print(halloween.enabled(), len(halloween.SPOOKY_GREETINGS))"`
  under both flag combinations; `py_compile` all touched modules.
- **Faces:** run `scripts/generate_halloween_faces.sh`, eyeball the 10 PNGs at panel size.
- **Bench (no PIR):** with `ROBOT_CONCIERGE_MODE=1 ROBOT_HALLOWEEN_MODE=1`, simulate a motion wake (the
  dry-run `r` key / HTTP) → confirm a spooky face + a spooky phrase + the conversation loop, and that a
  drive request is refused with a spooky line.
- **Live:** wave at the PIR → spook → talk back → sleeps after silence. Flip `ROBOT_HALLOWEEN_MODE=0` →
  back to normal Concierge greetings.

---

## 11. Open questions for your approval

1. **Spook everyone, or recognise-family-and-spook-strangers?** Default proposed: spook everyone
   (`ROBOT_HALLOWEEN_SPOOK_KNOWN=1`). The `=0` option greets enrolled faces by name and only spooks
   strangers — cute, but is it worth it for v1?
2. **Face display:** flicker through all 10 (strobe) vs a random still per visitor (different monster each
   time)? I lean **random still** (more legible on a small panel, feels intentional).
3. **Spooky sound?** Optional `sounds/spooky/` earcon (a creak/whoosh) played *before* the phrase for extra
   punch. Add in v1, or skip?
4. **Persona in chat:** default `ROBOT_HALLOWEEN_PERSONA=1` makes the whole conversation spooky-in-character.
   Good? (You can always set `0` for spook-greeting-only.)
5. **Folder name:** `brain/faces/spooky/` (proposed) vs `brain/faces/halloween/`. Cosmetic.

---

**On approval I'll implement in this order:** `halloween.py` (phrases + persona) → `face_id.detect()` →
`main.py` branches → the 10 faces + generator → config + install hook → `docs/halloween.md` + README, with a
`py_compile` gate after each code step. 🎃
