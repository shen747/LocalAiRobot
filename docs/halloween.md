# 🎃 Halloween Mode

A spooky **sub-mode of [Concierge Mode](concierge.md)**. When a trick-or-treater trips the PIR motion
sensor, Jarvis doesn't give a friendly "Hello!" — it **spooks them**: a random cackle, one of **50+
kid-friendly spooky phrases**, and a **random spooky face**, then it drops into the normal conversation loop
so it can banter back in a spooky character. It stays **100 % stationary** (every drive request is refused,
spookily) and the face check is **strictly on-device** — the same privacy guarantee as Concierge.

> Perfect on a porch on Halloween night: motion → "Mwa-ha-ha… welcome, brave soul!" → chat → back to sleep.

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
- **Face libraries installed** — `install_stack_pi5.sh` / `_jetson.sh` set these up (OpenCV + the ArcFace
  model, as part of Concierge). Halloween only needs to **detect** a face, not recognise it, so **no one has
  to be enrolled** — but the face libs must be present (they are, after the install).
- A **speaker** for the phrases + cackle (the robot's normal voice).

---

## What happens (the flow)

1. **Motion** on the PIR wakes the robot.
2. It shows a **random spooky face** and snaps a camera frame.
3. **Is a person there?** A strictly-local Haar face-detect (`FaceID.detect()`) answers "yes, someone's at
   the door" — **recognition is not required**, so a costumed stranger counts. (Motion with *no* face — a cat,
   a curtain — is ignored; it quietly goes back to sleep.)
4. It plays a **spook earcon** (a cackle/boo/wail) and speaks a **random spooky phrase**.
5. It **falls into the conversation loop** (identical to Concierge): the kid can talk back, and Jarvis
   replies in a **spooky persona**. Ask it to drive and it refuses with a spooky line — it never moves.
6. After `ROBOT_FOLLOWUP_SILENCE_S` of silence, it **drowses back to sleep**, ready for the next visitor.

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

## The spooky sound

A short cackle/boo/wail plays **before** the phrase. Generate the clips (on your PC, needs Piper from
`host_setup.sh`):

```bash
./scripts/generate_sounds.sh          # creates sounds/spook_sounds/*.wav (among all earcons)
```

One is picked at random. Want a real sound effect instead of Piper's voice? Drop your own `.wav` files into
`sounds/spook_sounds/`. If the folder is empty the spook still works — it just skips the earcon.

---

## Customising the phrases & persona

Everything spoken lives in **`brain/halloween.py`**:
- `SPOOKY_GREETINGS` — the 50+ opening scares. Add/edit freely; one is chosen at random.
- `SPOOKY_REFUSALS` — the in-character "I can't move, I'm haunting this doorway" lines.
- `PERSONA` — the identity the LLM adopts, so the *whole* conversation stays spooky.

Names are still pronounced via `ROBOT_TTS_PRONUNCIATIONS` (e.g. "Ramesh"), same as everywhere else.

---

## Safety & notes

- **Kid-safe by design** — the phrases are playful (a fright, then a wink), never traumatic.
- **Never moves** — it's a stationary Concierge; drive/explore/find are always refused.
- **On-device** — face detection is OpenCV Haar only. No image ever leaves the Pi; no cloud, no VLM.
- **Graceful** — no PIR, no speaker, or no face libs? It simply does less (e.g. wake-word only, or no
  earcon) rather than crashing.

Have a spooky night! 🦇
