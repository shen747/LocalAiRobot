# 🎃 Halloween Mode

A spooky **sub-mode of [Concierge Mode](concierge.md)**. When a trick-or-treater trips the PIR motion
sensor, Jarvis doesn't give a friendly "Hello!" — it **spooks them**: a **LOUD jump-scare stinger**, one of
**50+ kid-friendly spooky phrases**, and a **random spooky face held on the panel**, then it drops into the
normal conversation loop so it can banter back in a spooky character. It stays **100 % stationary** (every
drive request is refused, spookily). The trigger is **motion alone** — no camera, no face recognition — so a
**masked, costumed stranger** gets spooked just the same, and nothing ever leaves the Pi.

> Perfect on a porch on Halloween night: motion → *SCREECH* → "Mwa-ha-ha… welcome, brave soul!" → chat → sleep.

---

## Turn it on

Halloween Mode is a **sub-mode of Concierge**, so it needs **both** flags:

```bash
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
- **A camera is NOT required.** Halloween triggers on PIR **motion** alone — it never looks for a face
  (costumes/masks defeat face detection anyway), so no camera, no face libraries, and no enrolment are
  needed. (They're still installed as part of Concierge; Halloween just doesn't use them.)
- A **speaker** for the stinger + phrases (the robot's normal voice). Turn it **up** — the stingers are
  synthesized hot on purpose.

---

## What happens (the flow)

1. **Motion** on the PIR wakes the robot — that motion *is* the "someone's at the door" signal, so it
   spooks straight away. (No camera peek, no face-detect: trick-or-treaters wear **masks/costumes** that
   defeat face detection, so gating on a detected face would let the best-costumed kids walk right up
   unscared. Motion alone is the right trigger here.)
2. A **random spooky face** snaps onto the panel and is **held** — it stays that monster through the whole
   scare (no flicker back to the idle face).
3. It plays a **LOUD jump-scare stinger** (a screech/boom/shriek) in **full** — the face + sound sit there a
   beat — **then** speaks a **random spooky phrase** (the face still held).
4. It **falls into the conversation loop** (identical to Concierge): the kid can talk back, and Jarvis
   replies in a **spooky persona**. Ask it to drive and it refuses with a spooky line — it never moves.
5. After `ROBOT_FOLLOWUP_SILENCE_S` of silence, it **drowses back to sleep**, ready for the next visitor.

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
- **Private** — the trigger is a PIR motion pin, not the camera. No photo is taken; nothing leaves the Pi.
- **Graceful** — no PIR, or no speaker? It simply does less (e.g. wake-word only, or no earcon) rather than
  crashing.

Have a spooky night! 🦇
