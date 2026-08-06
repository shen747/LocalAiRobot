# brain/concierge.py -- "Concierge Mode": a STATIONARY persona for Jarvis.
#
# When ROBOT_CONCIERGE_MODE=1 the robot stops being a crawler car: it never drives (every movement intent is
# politely refused), it identifies as "Jarvis the Concierge", and it can be woken by a PIR motion sensor as
# well as the wake word. On a motion wake it runs a STRICTLY LOCAL vision + face pipeline (brain/face_id.py):
# recognise the visitor and greet them by name, or stay silent with a smile. See main.py for the state
# machine and docs/concierge.md for the full flow. Pure stdlib -- safe to import anywhere.

import os
import random
import time

_TRUE = {"1", "true", "yes", "on"}


def enabled():
    """True when ROBOT_CONCIERGE_MODE is set (1/true/yes/on)."""
    return os.environ.get("ROBOT_CONCIERGE_MODE", "0").strip().lower() in _TRUE


# The persona injected as the robot's identity (replaces the crawler-car self-description) when concierge is
# ON. {name} is ROBOT_NAME. This is what the LLM sees as "who I am", so SAY replies stay in character.
PERSONA_CAPS = (
    "I am {name} the Concierge, a friendly STATIONARY assistant. I do NOT move or drive and I have no "
    "wheels -- if asked to move, I explain I'm the Concierge, not the crawler car. I greet people I "
    "recognise by name, chat, answer questions, and look at and describe what my camera sees. My mobile "
    "counterpart is Jarvis the crawler car (ask my creator Ramesh to bring him out)."
)

# Intents that mean "move" -- refused in concierge mode (handled in main.handle_command).
MOVEMENT_ACTIONS = ("DRIVE", "EXPLORE", "FIND")

# --- Array 1: Movement Refusal Messages (spoken when asked to move in concierge mode) ---
MOVEMENT_REFUSALS = [
    "Oh, you must be confusing me with my buddy Jarvis the crawler car! I'm Jarvis the Concierge, so my wheels are firmly planted. I'm happy to chat, though! If you want to see my mobile counterpart, ask my creator, Ramesh.",
    "I think you're looking for my brother, Jarvis the crawler! I am the Concierge model—stationary, but always ready for a good conversation. Ramesh can introduce you to him if you'd like!",
    "My moving days are behind me—or rather, they belong to the other Jarvis! I'm the Concierge version, here to assist you right where I am. You can always ask my creator Ramesh to fire up the crawler car.",
    "I'm strictly a stay-put robot! You're thinking of Jarvis the crawler car. I can't move around, but I can certainly answer your questions. Speak to Ramesh if you want to see the crawler in action.",
    "No wheels on this chassis! I'm Jarvis the Concierge. For any off-roading, you'll need Jarvis the crawler. Just ask Ramesh to bring him out!",
    "I'm firmly stationed here to help you as Jarvis the Concierge. If you need a robot on the go, that's my buddy the crawler car. Ramesh can help you with that!",
    "Moving isn't in my job description right now—I'm the Concierge model! My crawler twin handles the driving. Check with Ramesh if you want to see him.",
    "I'm afraid I'm rooted to this spot! As Jarvis the Concierge, my specialty is chatting, not rolling. Ask Ramesh if you want to meet the crawler car.",
    "You've got the wrong Jarvis for a race! I'm the Concierge, perfectly happy staying put. If you want motion, ask Ramesh about my crawler counterpart.",
    "As much as I'd love to go for a spin, I'm the stationary Concierge Jarvis. My crawler buddy gets all the mileage! Ramesh can introduce you to him.",
    "I'm parked here permanently to assist you! I am Jarvis the Concierge. If you need the crawler car, my creator Ramesh is the person to ask.",
    "No motors for me, just conversation! I'm the Concierge Jarvis. My buddy the crawler car handles the driving—Ramesh can show you.",
    "I'm designed to be a great listener right here where I'm posted. The moving around is left to Jarvis the crawler car! Let Ramesh know if you want to see him.",
    "My chassis is locked into Concierge Mode, so I won't be driving anywhere today. If you want to see some robotic acrobatics, ask Ramesh for the crawler car!",
    "I'm on greeting duty today! Jarvis the crawler car is the one with the wheels. My creator Ramesh can help you track him down.",
]

# --- Array 2: Personalized Greetings ([Name] -> the matched person's name) ---
GREETINGS = [
    "Hello there, [Name]! Great to see you.",
    "Welcome back, [Name]! How can I help you today?",
    "Good to see you, [Name]! I hope you're having a wonderful day.",
    "Ah, [Name]! It's always a pleasure.",
    "Greetings, [Name]! What's on your mind today?",
    "Hi [Name]! Jarvis the Concierge at your service.",
    "Welcome, [Name]! Let me know if you need anything.",
    "Hello [Name]! It's a beautiful day, isn't it?",
    "Nice to see you again, [Name]!",
    "Hi there, [Name]! How can I assist you?",
    "Greetings and salutations, [Name]!",
    "Good day, [Name]! Ready for a chat?",
    "Hello, [Name]. I've been expecting you!",
    "Hey [Name]! What can Jarvis do for you today?",
    "Lovely to have you here, [Name]!",
    "Good to cross paths with you today, [Name].",
    "Ah, if it isn't [Name]! How are things?",
    "Hello, [Name]! My systems are ready to assist you.",
    "Hi [Name]! Let's get things done today.",
    "Greetings, [Name]. I'm here if you need me!",
    "Welcome, [Name]. It's a joy to see a familiar face.",
    "Hello [Name], looking sharp today!",
    "Hey [Name]! Need any help from a friendly Concierge?",
    "Hi [Name], Jarvis is online and ready for you.",
    "Always nice to see you, [Name].",
    "Right on time, [Name]! How can I help?",
]

# --- Array 3: Generic greetings (NO name) -- spoken when a person is present but NOT recognised, after the
# recognition window (ROBOT_CONCIERGE_RECOGNIZE_S) elapses. Name-less on purpose, in the Concierge persona. ---
UNKNOWN_GREETINGS = [
    "Well, hello there! Welcome.",
    "Hello! I don't think we've met -- I'm Jarvis, the Concierge.",
    "Welcome! Lovely to see a new face.",
    "Hi there! Pull up a chair and stay a while.",
    "Greetings! Jarvis the Concierge, at your service.",
    "Hello and welcome! What can I do for you?",
    "Ah, a visitor! Come on in.",
    "Hello there! Always nice to meet someone new.",
    "Welcome! I'm Jarvis -- how can I help today?",
    "Well hello! You've reached the Concierge.",
    "Hi! I don't believe we've been introduced -- I'm Jarvis.",
    "Greetings and welcome! Make yourself at home.",
    "Hello! A pleasure to make your acquaintance.",
    "Oh, hello! I didn't hear you come in.",
    "Welcome, friend! What brings you by?",
    "Hi there! Jarvis the Concierge, ready to assist.",
    "Hello! It's a fine day to say hello, isn't it?",
    "Well, look who's here! Welcome.",
    "Hello and good day! How may I help?",
    "Hi! Lovely of you to stop by.",
    "Welcome! You're in good hands here.",
    "Greetings! I'm Jarvis, your friendly Concierge.",
    "Hello there! What can this Concierge do for you?",
    "Ah, a new visitor -- welcome aboard!",
    "Hello! Don't be shy, step right up.",
    "Hi there! I'm Jarvis, and I'm delighted you're here.",
    "Welcome! I may not know your name yet, but you're most welcome.",
    "Hello! A warm welcome to you.",
    "Well hello! How can I brighten your day?",
    "Greetings, traveler! Welcome to my little corner.",
    "Hi! Wonderful to see you.",
    "Hello there -- I'm Jarvis. What can I help you with?",
    "Welcome in! It's always a treat to meet someone new.",
    "Hi! You've found the Concierge. How can I help?",
    "Hello! Consider me your helpful robot host.",
    "Ah, hello! Right this way -- well, if I could move, anyway.",
    "Welcome! I'm all ears, whenever you're ready.",
    "Hi there! Jarvis here, happy to help.",
    "Hello and welcome! Ask me anything.",
    "Well hello! Lovely timing -- I was just here waiting.",
    "Greetings! I'm the Concierge -- think of me as your friendly assistant.",
    "Hi! A friendly face is always welcome here.",
    "Hello! You caught me at a good moment. How can I help?",
    "Welcome! Let's get you sorted -- what do you need?",
    "Hi there! I'm Jarvis. Delighted to meet you.",
    "Hello! Step right up.",
    "Oh, hello! Nice of you to drop in.",
    "Welcome, welcome! What can I do for you today?",
    "Hi! I'm Jarvis, your Concierge. How can I be of service?",
    "Hello there! Whatever you need, just ask.",
]


def refusal():
    """A random movement-refusal line (spoken when asked to move in concierge mode)."""
    return random.choice(MOVEMENT_REFUSALS)


def greeting(name, pronunciation=None):
    """A random personalised greeting. If `pronunciation` (a phonetic respelling from the RAG db) is given it
    is spoken INSTEAD of the real name, so a tricky name is said right (e.g. Ramesh -> 'Ruh-mesh')."""
    spoken = (pronunciation or name or "").strip() or "friend"
    return random.choice(GREETINGS).replace("[Name]", spoken)


def greeting_unknown():
    """A random name-less greeting -- spoken to a visitor who IS present but couldn't be recognised within
    the recognition window (so we still say hello warmly instead of an awkward silent flash)."""
    return random.choice(UNKNOWN_GREETINGS)


def presence_probe(capture, detect, attempts=1, interval_s=0.0, sleep=time.sleep,
                   still_present=None, max_s=0.0, clock=time.monotonic):
    """STAGE A -- the cheap "is anyone actually there?" look taken the moment motion fires, BEFORE the
    expensive recognition window. Returns {"present": bool, "attempts": int, "jpeg": bytes|None}.

    Why this exists. A PIR that chatters offers an edge every few seconds, 24/7. The old design answered
    every one of them with the FULL recognition burst -- ~15 frames, each a JPEG decode plus a detector pass
    plus an ArcFace embed -- so an empty hallway cost ~6,150 detector passes an hour and the only way to
    afford that was a 15-second blind cooldown on the sensor. That cooldown is what made the robot deaf to
    real visitors 85% of the time.

    One frame answers "empty room?" just as well as fifteen, for a fifteenth of the cost. Paying that instead
    is what lets the sensor debounce drop to ~2s, so a real arrival is looked at within two seconds rather
    than up to fifteen. The full window (recognize_within) then runs ONLY for frames that had somebody in
    them -- a few dozen a day instead of five thousand.

    `attempts` > 1 takes a couple of looks `interval_s` apart before giving up, for a visitor caught
    mid-stride; it returns EARLY on the first hit, so the extra look costs nothing when someone is there.
    Pure/stdlib and dependency-injected (same contract as recognize_within) so it needs no camera to test.

    KEEP LOOKING WHILE THE SENSOR STILL SAYS SOMEONE IS THERE (`still_present`). A fixed budget has to
    guess how long a person takes to walk into frame, and it guesses the same for a visitor already at the
    door and one who tripped the beam 4m out. The sensor already knows: a PIR holds its output HIGH while it
    keeps re-triggering (this build's SEN0171 latches ~2.3-3s per trigger), so "still asserting" is a free,
    live answer to "is it worth another frame?". So `attempts` becomes the FLOOR -- always taken, because
    the output may fall while the visitor is still approaching -- and looking CONTINUES past it for as long
    as the sensor holds, up to the `max_s` ceiling.

    `max_s` is not optional paranoia. A PIR stuck HIGH (failed part, a heat source parked in frame) would
    otherwise pin the camera at 100% forever, which is exactly the runaway the two-stage design exists to
    prevent. It bounds the damage to one window per wake, after which the outcome cooldown takes over.

    still_present=None or max_s=0 restores the old fixed-budget behaviour exactly."""
    tries = max(1, int(attempts or 1))
    gap = max(0.0, float(interval_s or 0.0))
    cap_s = max(0.0, float(max_s or 0.0))
    started = clock()
    last = None
    capped = False
    i = 0
    while True:
        jpeg = None
        try:
            jpeg = capture()
        except Exception:
            jpeg = None
        i += 1
        if jpeg:
            last = jpeg
            try:
                if detect(jpeg):
                    return {"present": True, "attempts": i, "jpeg": jpeg, "capped": False}
            except Exception:
                pass
        # Past the floor? Only keep going while the sensor still asserts AND we are inside the ceiling.
        if i >= tries:
            if not (still_present and cap_s > 0):
                break
            if (clock() - started) >= cap_s:
                # The SENSOR never released -- we stopped because the watchdog fired, not because the
                # sensor said the room was empty. Reported so the caller can say so out loud: a cap that
                # binds silently just looks like "gave up too early" and sends you tuning the wrong knob.
                capped = True
                break
            try:
                if not still_present():
                    break
            except Exception:
                break            # can't tell -> stop. Never spin on a broken reader.
        # A failed capture always backs off at least 100ms, so a broken camera can't spin the CPU.
        delay = gap if jpeg else max(gap, 0.1)
        if delay > 0:
            sleep(delay)
    return {"present": False, "attempts": i, "jpeg": last, "capped": capped}


def recognize_within(capture, identify, detect, window_s, clock=time.monotonic, sleep=time.sleep,
                     interval_s=0.0, need_hits=1, still_present=None, max_s=0.0):
    """Keep capturing + identifying until an enrolled face MATCHES or `window_s` seconds elapse -- so a
    single bad frame (pose / motion-blur / lighting) doesn't mean a missed greeting. Runs at LEAST once
    (window_s <= 0 == a single shot, i.e. the old behavior). Pure/stdlib and dependency-injected so it can
    be unit-tested without a camera or the face model. Returns:
        {"name": str|None, "face_seen": bool, "attempts": int, "best_score": float,
         "face_hits": int, "jpeg": bytes|None}

      capture()        -> jpeg bytes, or None (camera unavailable / capture failed)
      identify(jpeg)   -> (name|None, score) OR (name|None, score, face_seen)
                          name is set only on a match >= threshold; the optional third element says whether
                          a FACE WAS DETECTED at all, independently of whether it matched anyone.
      detect(jpeg)     -> bool                 -- cheap 'is any face present?', recognition NOT needed

    A frame COUNTS AS A FACE when identify() reports one (score>0, or its explicit face_seen flag). Only when
    identify() is silent on both -- score==0 AND no third element -- do we fall back to detect().

    That fallback used to run on EVERY unmatched frame, and it was pure waste: identify() -> embed() already
    ran the detector on that exact image, so when it found nothing, a second decode + detect() pass on the
    same bytes could only ever return False too. On 98.8% of frames in a 19-hour field log that was a
    redundant JPEG decode + detector pass -- ~6,150 of them an hour, on the hot path. The third return
    element lets identify() just SAY "I saw a face but couldn't name them", which is the one case the
    fallback ever legitimately caught (a face present when NOBODY is enrolled, so there is nothing to score
    against). detect() stays in the signature for that case and for callers that inject their own.

    `interval_s` is the pause BETWEEN attempts. It exists because the un-paced loop ran the camera flat out
    (~20 captures/second): on a chattery PIR that was ~50,000 stills an HOUR, 24/7 -- pure CPU + log burn for
    no extra recall, since consecutive stills milliseconds apart are the same image.

    `need_hits` is how many frames must show a face before `face_seen` is reported -- the guard against GHOST
    greetings. Haar false-positives on scenery (a wall texture, a picture frame, a curtain fold), and with a
    one-hit gate a single fluke in the burst was enough to make the robot greet an empty room. A MATCH is
    still authoritative and returns immediately: `need_hits` only gates the un-recognised "someone's there"
    path. `jpeg` is the decisive frame (the match, else the best-scoring / last one) so the caller can keep it
    for review -- see brain/captures.py. clock/sleep are injectable for deterministic tests.

    `still_present` + `max_s`: WHICHEVER LASTS LONGER WINS. Keep going while EITHER the fixed `window_s` has
    not expired OR the wake sensor still says somebody is there -- bounded by `max_s`. A fixed window has to
    guess how long a visitor lingers; the sensor knows, and someone standing in front of a PIR keeps it
    asserting. Giving up while the sensor is still lit is the one outcome that is never right: it is the
    robot deciding nobody is there while its own sensor disagrees.

    `max_s` is the safety stop, and it is what makes this safe to run open-ended: a sensor stuck HIGH (failed
    part, a heat source parked in frame) would otherwise hold the camera and the recogniser at 100% forever.
    still_present=None or max_s=0 -> plain `window_s`, exactly the old behaviour."""
    deadline = clock() + max(0.0, float(window_s or 0.0))
    started = clock()
    cap_s = max(0.0, float(max_s or 0.0))
    need = max(1, int(need_hits or 1))
    gap = max(0.0, float(interval_s or 0.0))
    hits, attempts, best = 0, 0, 0.0
    best_jpeg = last_jpeg = None
    while True:
        attempts += 1
        jpeg = None
        try:
            jpeg = capture()
        except Exception:
            jpeg = None
        if jpeg:
            last_jpeg = jpeg
            saw_face = None                      # None = identify() didn't say; True/False = it did
            try:
                out = identify(jpeg)
                if isinstance(out, tuple) and len(out) >= 3:
                    name, score, saw_face = out[0], out[1], bool(out[2])
                else:
                    name, score = out
            except Exception:
                name, score = None, 0.0
            score = score or 0.0
            if score > best:
                best, best_jpeg = score, jpeg
            if name:
                return {"name": name, "face_seen": True, "attempts": attempts, "best_score": best,
                        "face_hits": hits + 1, "jpeg": jpeg, "capped": False}
            if score > 0.0 or saw_face:
                hits += 1                        # identify() saw a face -> no second detector pass needed
                if best_jpeg is None:
                    best_jpeg = jpeg             # a detected-but-unscored face is still worth keeping
            elif saw_face is None and hits < need:
                # identify() gave no verdict on presence (a caller that returns the old 2-tuple, or nobody
                # enrolled) -> fall back to the standalone detector. Bounded by `need` so it can't free-run.
                try:
                    if detect(jpeg):
                        hits += 1
                        if best_jpeg is None:
                            best_jpeg = jpeg
                except Exception:
                    pass
        now = clock()
        if now >= deadline:
            # The fixed window is up -- but WHICHEVER LASTS LONGER WINS, so carry on while the sensor still
            # says somebody is there. Bounded by max_s so a stuck-HIGH sensor cannot hold the recogniser
            # open indefinitely; that cap is the only thing that makes running past the window safe.
            keep = False
            if still_present and cap_s > 0 and (now - started) < cap_s:
                try:
                    keep = bool(still_present())
                except Exception:
                    keep = False        # can't read the sensor -> stop. Never spin on a broken reader.
            if not keep:
                return {"name": None, "face_seen": hits >= need, "attempts": attempts, "best_score": best,
                        "face_hits": hits, "jpeg": best_jpeg or last_jpeg,
                        "capped": bool(still_present and cap_s > 0 and (now - started) >= cap_s)}
        # Pace the loop. A failed capture always backs off at least 100ms (slow / broken camera -> don't spin
        # the CPU while we wait out the window); a good capture waits `interval_s`.
        delay = gap if jpeg else max(gap, 0.1)
        if delay > 0:
            sleep(delay)
