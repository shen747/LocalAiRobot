# 🎃 Halloween Mode

A spooky **sub-mode of [Concierge Mode](concierge.md)**. When a trick-or-treater trips the PIR motion
sensor, Jarvis doesn't give a friendly "Hello!" — it **spooks them**: a **LOUD jump-scare stinger**, one of
**50+ kid-friendly spooky phrases**, and a **random spooky face held on the panel**, then it drops into the
normal conversation loop so it can banter back in a spooky character. It stays **100 % stationary** (every
drive request is refused, spookily). A scare needs the **PIR and the camera to agree**: the camera looks for a
face **or anything moving**, so a **masked, costumed stranger** still gets spooked but an empty doorway
doesn't. There's no face recognition, and nothing ever leaves the Pi.

> Perfect on a porch on Halloween night: motion → camera sees someone → *SCREECH* → "Mwa-ha-ha… welcome, brave
> soul!" → chat → sleep.

---

## Turn it on

Halloween Mode is a **sub-mode of Concierge**, so it needs **both** flags. They live in `config/robot.env`
**on the robot**, so log in to it first.

**1. Log in to the robot** (from your PC):

```bash
ssh <user>@<hostname>.local     # e.g. ssh ramesh@LocalAiRobot-Jarvis.local
```

If `.local` won't resolve, use the robot's IP from your router instead. Connecting for the first time? See
Phase 1, step 3 of [docs/assembly.md](assembly.md). Away from your home Wi-Fi? See
[docs/change-pi-wifi.md](change-pi-wifi.md).

**2. Turn on both flags and restart.** Run these in that SSH session:

```bash
cd ~/LocalAiRobot
sed -i -e 's|^ROBOT_CONCIERGE_MODE=.*|ROBOT_CONCIERGE_MODE=1|' \
       -e 's|^ROBOT_HALLOWEEN_MODE=.*|ROBOT_HALLOWEEN_MODE=1|' config/robot.env
sudo systemctl restart robot.service     # or ./scripts/run_me.sh --reload
```

`ROBOT_HALLOWEEN_MODE=1` alone does nothing — if `ROBOT_CONCIERGE_MODE=0`, the robot is still the crawler
car. To go back to a normal Concierge greeting, set `ROBOT_HALLOWEEN_MODE=0`. To get the crawler back, set
`ROBOT_CONCIERGE_MODE=0`.

### Prerequisites (same as Concierge)

- **PIR motion sensor wired** — `ROBOT_PIR_PIN` (default GPIO4/pin 7). See
  [docs/wiring/pir-sensor-wiring.md](wiring/pir-sensor-wiring.md). Without it, only the wake word triggers.
- **A camera** (the Concierge webcam). Every PIR trigger is confirmed by the camera before a scare —
  `ROBOT_HALLOWEEN_CAMERA_CHECK=1`, the default. **Strict:** no camera, or one that returns no picture, means
  **no scares at all**, and the log says `the camera gave no picture -- NOT spooking`. No enrolment is needed:
  it only asks "is a face **or** anything moving in frame?", never "who is it?". Set
  `ROBOT_HALLOWEEN_CAMERA_CHECK=0` to go back to scaring on motion alone.
- A **speaker** for the stinger + phrases (the robot's normal voice). Turn it **up** — the stingers are
  synthesized hot on purpose.

---

## What happens (the flow)

1. **Motion** on the PIR wakes the robot, and the **camera confirms it**. It takes ~1 s of frames (longer
   while the sensor still says someone is there, up to 6 s) and looks for **a face, or anything moving**.
   Movement counts, not just faces, because trick-or-treaters wear **masks/costumes** that defeat face
   detection. Nothing seen → **no scare**: a short `ROBOT_CONCIERGE_SILENT_COOLDOWN_S`, then back to sleep.
   That is what stops a chattering PIR from scaring an empty doorway every half minute. The idle face stays
   up during the look, so an empty room never flashes a monster. If the camera can see its own movement (an
   animated prop, a flickering candle, a TV), raise `ROBOT_HALLOWEEN_MOVE_FRACTION` (default `0.01` = 1 % of
   the picture).
2. A **random spooky face** snaps onto the panel and is **held** — it stays that monster through the whole
   scare (no flicker back to the idle face).
3. It plays a **LOUD jump-scare stinger** (a screech/boom/shriek) in **full** — the face + sound sit there a
   beat — **then** speaks a **random spooky phrase** (the face still held).
4. It **falls into the conversation loop** (identical to Concierge): the kid can talk back, and Jarvis
   replies in a **spooky persona**. Ask it to drive and it refuses with a spooky line — it never moves.
5. After `ROBOT_FOLLOWUP_SILENCE_S` of silence, it **drowses back to sleep** and is ready for the next
   visitor **`ROBOT_HALLOWEEN_COOLDOWN_S` (10 s) later**. Lower it to spook the next group sooner; raise it
   if the PIR keeps re-spooking an empty doorway.

---

## The spooky faces

Ten monster faces live in **`brain/faces/halloween/01.png … 10.png`** (jack-o'-lantern, ghost, skull,
glowing eyes, Cheshire grin, spider, bat, Frankenstein, vampire, glitch). Each visitor gets **one at
random**, frozen for that encounter (a different monster every time).

They're committed, so a fresh clone already has them. To (re)generate — e.g. for a different panel size:

```bash
./scripts/generate_halloween_faces.sh                 # 480x320 (USB-C panel, landscape)
./scripts/generate_halloween_faces.sh --size 800x480  # a 5" DSI panel
./scripts/generate_halloween_faces.sh --force         # overwrite existing frames
```

Prefer your own art? Just drop your own PNGs in `brain/faces/halloween/` (any count — one is picked at
random). If the folder is somehow empty, the face falls back to a procedural jack-o'-lantern glow, so it's
never blank.

---

## The spooky sound (LOUD)

The jump-scare earcon is **not** a Piper voice line — it's a set of **synthetic, peak-amplitude stingers**
(`stinger_screech.wav`, `stinger_boom.wav`, `stinger_shriek.wav`) so the scare is **as loud and sudden as
the hardware allows** (they play through `aplay` at the ALSA mixer's 100 %). One is picked at random and
played in **full** before the phrase. They're **committed** (a fresh clone has them) and need **no Piper** —
pure Python stdlib. To (re)generate:

```bash
./scripts/generate_spook_stingers.sh          # writes sounds/spook_sounds/stinger_*.wav
./scripts/generate_spook_stingers.sh --force  # overwrite existing
```

Prefer your own SFX (a real scream, a horror sting)? Drop your own `.wav` files into `sounds/spook_sounds/`
— one is picked at random. Keep them **peak-normalized** so they stay loud. If the folder is empty the spook
still works — it just skips the earcon and goes straight to the phrase.

---

## Customising the phrases & persona

Everything spoken lives in **`brain/halloween.py`**:
- `SPOOKY_GREETINGS` — the 50+ opening scares. Add/edit freely; one is chosen at random.
- `SPOOKY_REFUSALS` — the in-character "I can't move, I'm haunting this doorway" lines.
- `PERSONA` — the identity the LLM adopts, so the *whole* conversation stays spooky.

Names are still pronounced via `ROBOT_TTS_PRONUNCIATIONS` (e.g. "Ramesh"), same as everywhere else.

---

## Safety & notes

- **Kid-safe by design** — the phrases are playful (a fright, then a wink), never traumatic. (The stingers
  *are* loud, though — that's the whole point of a jump scare. Turn the speaker down if a porch full of
  little ones is too much.)
- **Never moves** — it's a stationary Concierge; drive/explore/find are always refused.
- **Private** — the camera frames are checked on the Pi and never leave it. With
  `ROBOT_DETECT_CAPTURE_MODE=all`, the last frame of each empty-porch look is kept in `~/detect-captures`
  (so you can see what it saw) and purged after `ROBOT_DETECT_CAPTURE_KEEP_DAYS`.
- **Graceful** — no PIR, or no speaker? It simply does less (e.g. wake-word only, or no earcon) rather than
  crashing. **No camera** is the exception, by design: with the camera check on, it won't scare at all until
  the camera is back (or you set `ROBOT_HALLOWEEN_CAMERA_CHECK=0`).

Have a spooky night! 🦇
