# brain/concierge.py -- "Concierge Mode": a STATIONARY desk persona for Jarvis.
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
    "I am {name} the Concierge, a friendly STATIONARY desk assistant. I do NOT move or drive and I have no "
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
    "I'm strictly a desk-duty robot! You're thinking of Jarvis the crawler car. I can't move around, but I can certainly answer your questions. Speak to Ramesh if you want to see the crawler in action.",
    "No wheels on this chassis! I'm Jarvis the Concierge. For any off-roading, you'll need Jarvis the crawler. Just ask Ramesh to bring him out!",
    "I'm firmly stationed here to help you as Jarvis the Concierge. If you need a robot on the go, that's my buddy the crawler car. Ramesh can help you with that!",
    "Moving isn't in my job description right now—I'm the Concierge model! My crawler twin handles the driving. Check with Ramesh if you want to see him.",
    "I'm afraid I'm rooted to this spot! As Jarvis the Concierge, my specialty is chatting, not rolling. Ask Ramesh if you want to meet the crawler car.",
    "You've got the wrong Jarvis for a race! I'm the Concierge, perfectly happy staying put. If you want motion, ask Ramesh about my crawler counterpart.",
    "As much as I'd love to go for a spin, I'm the stationary Concierge Jarvis. My crawler buddy gets all the mileage! Ramesh can introduce you to him.",
    "I'm parked here permanently to assist you! I am Jarvis the Concierge. If you need the crawler car, my creator Ramesh is the person to ask.",
    "No motors for me, just conversation! I'm the Concierge Jarvis. My buddy the crawler car handles the driving—Ramesh can show you.",
    "I'm designed to be a great listener right here at the desk. The moving around is left to Jarvis the crawler car! Let Ramesh know if you want to see him.",
    "My chassis is locked into Concierge Mode, so I won't be driving anywhere today. If you want to see some robotic acrobatics, ask Ramesh for the crawler car!",
    "I'm on front desk duty today! Jarvis the crawler car is the one with the wheels. My creator Ramesh can help you track him down.",
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
    "Welcome to the desk, [Name]!",
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
    "Well hello! You've reached the front desk.",
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
    "Welcome! You're in good hands at the front desk.",
    "Greetings! I'm Jarvis, your friendly Concierge.",
    "Hello there! What can this Concierge do for you?",
    "Ah, a new visitor -- welcome aboard!",
    "Hello! Don't be shy, step right up.",
    "Hi there! I'm Jarvis, and I'm delighted you're here.",
    "Welcome! I may not know your name yet, but you're most welcome.",
    "Hello! A warm welcome from the front desk.",
    "Well hello! How can I brighten your day?",
    "Greetings, traveler! Welcome to my little corner.",
    "Hi! Wonderful to see you.",
    "Hello there -- I'm Jarvis. What can I help you with?",
    "Welcome in! It's always a treat to meet someone new.",
    "Hi! You've found the Concierge desk. How can I help?",
    "Hello! Consider me your helpful robot host.",
    "Ah, hello! Right this way -- well, if I could move, anyway.",
    "Welcome! I'm all ears, whenever you're ready.",
    "Hi there! Jarvis here, happy to help.",
    "Hello and welcome! Ask me anything.",
    "Well hello! Lovely timing -- I was just here waiting.",
    "Greetings! I'm the Concierge -- think of me as your desk assistant.",
    "Hi! A friendly face is always welcome here.",
    "Hello! You caught me at a good moment. How can I help?",
    "Welcome! Let's get you sorted -- what do you need?",
    "Hi there! I'm Jarvis. Delighted to meet you.",
    "Hello! Step right up to the front desk.",
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


def recognize_within(capture, identify, detect, window_s, clock=time.monotonic, sleep=time.sleep):
    """Keep capturing + identifying until an enrolled face MATCHES or `window_s` seconds elapse -- so a
    single bad frame (pose / motion-blur / lighting) doesn't mean a missed greeting. Runs at LEAST once
    (window_s <= 0 == a single shot, i.e. the old behavior). Pure/stdlib and dependency-injected so it can
    be unit-tested without a camera or the face model. Returns:
        {"name": str|None, "face_seen": bool, "attempts": int, "best_score": float}

      capture()        -> jpeg bytes, or None (camera unavailable / capture failed)
      identify(jpeg)   -> (name|None, score)   -- name set only on a match >= threshold
      detect(jpeg)     -> bool                 -- cheap 'is any face present?' (Haar), recognition NOT needed

    `face_seen` becomes True as soon as a real face is seen at least once: score>0 means identify() already
    embedded a face; when score==0 (no face, OR nobody enrolled) we fall back to detect() so an
    un-enrolled visitor still counts as 'present'. clock/sleep are injectable for deterministic tests."""
    deadline = clock() + max(0.0, float(window_s or 0.0))
    face_seen, attempts, best = False, 0, 0.0
    while True:
        attempts += 1
        jpeg = None
        try:
            jpeg = capture()
        except Exception:
            jpeg = None
        if jpeg:
            try:
                name, score = identify(jpeg)
            except Exception:
                name, score = None, 0.0
            best = max(best, score or 0.0)
            if name:
                return {"name": name, "face_seen": True, "attempts": attempts, "best_score": best}
            if (score or 0.0) > 0.0:
                face_seen = True
            elif not face_seen:
                try:
                    face_seen = bool(detect(jpeg))
                except Exception:
                    pass
        if clock() >= deadline:
            return {"name": None, "face_seen": face_seen, "attempts": attempts, "best_score": best}
        if not jpeg:
            sleep(0.1)   # slow / broken capture -> don't spin the CPU while we wait out the window
