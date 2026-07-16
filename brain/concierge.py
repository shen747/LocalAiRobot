# brain/concierge.py -- "Concierge Mode": a STATIONARY desk persona for Jarvis.
#
# When ROBOT_CONCIERGE_MODE=1 the robot stops being a crawler car: it never drives (every movement intent is
# politely refused), it identifies as "Jarvis the Concierge", and it can be woken by a PIR motion sensor as
# well as the wake word. On a motion wake it runs a STRICTLY LOCAL vision + face pipeline (brain/face_id.py):
# recognise the visitor and greet them by name, or stay silent with a smile. See main.py for the state
# machine and docs/concierge.md for the full flow. Pure stdlib -- safe to import anywhere.

import os
import random

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
    "counterpart is Jarvis the crawler car (ask my creator Ramesh, pronounced Ra-mesh, to bring him out)."
)

# Intents that mean "move" -- refused in concierge mode (handled in main.handle_command).
MOVEMENT_ACTIONS = ("DRIVE", "EXPLORE", "FIND")

# --- Array 1: Movement Refusal Messages (spoken when asked to move in concierge mode) ---
MOVEMENT_REFUSALS = [
    "Oh, you must be confusing me with my buddy Jarvis the crawler car! I'm Jarvis the Concierge, so my wheels are firmly planted. I'm happy to chat, though! If you want to see my mobile counterpart, ask my creator, Ramesh (pronounced Ra-mesh).",
    "I think you're looking for my brother, Jarvis the crawler! I am the Concierge model—stationary, but always ready for a good conversation. Ramesh (pronounced Ra-mesh) can introduce you to him if you'd like!",
    "My moving days are behind me—or rather, they belong to the other Jarvis! I'm the Concierge version, here to assist you right where I am. You can always ask my creator Ramesh (pronounced Ra-mesh) to fire up the crawler car.",
    "I'm strictly a desk-duty robot! You're thinking of Jarvis the crawler car. I can't move around, but I can certainly answer your questions. Speak to Ramesh (pronounced Ra-mesh) if you want to see the crawler in action.",
    "No wheels on this chassis! I'm Jarvis the Concierge. For any off-roading, you'll need Jarvis the crawler. Just ask Ramesh (pronounced Ra-mesh) to bring him out!",
    "I'm firmly stationed here to help you as Jarvis the Concierge. If you need a robot on the go, that's my buddy the crawler car. Ramesh (pronounced Ra-mesh) can help you with that!",
    "Moving isn't in my job description right now—I'm the Concierge model! My crawler twin handles the driving. Check with Ramesh (pronounced Ra-mesh) if you want to see him.",
    "I'm afraid I'm rooted to this spot! As Jarvis the Concierge, my specialty is chatting, not rolling. Ask Ramesh (pronounced Ra-mesh) if you want to meet the crawler car.",
    "You've got the wrong Jarvis for a race! I'm the Concierge, perfectly happy staying put. If you want motion, ask Ramesh (pronounced Ra-mesh) about my crawler counterpart.",
    "As much as I'd love to go for a spin, I'm the stationary Concierge Jarvis. My crawler buddy gets all the mileage! Ramesh (pronounced Ra-mesh) can introduce you to him.",
    "I'm parked here permanently to assist you! I am Jarvis the Concierge. If you need the crawler car, my creator Ramesh (pronounced Ra-mesh) is the person to ask.",
    "No motors for me, just conversation! I'm the Concierge Jarvis. My buddy the crawler car handles the driving—Ramesh (pronounced Ra-mesh) can show you.",
    "I'm designed to be a great listener right here at the desk. The moving around is left to Jarvis the crawler car! Let Ramesh (pronounced Ra-mesh) know if you want to see him.",
    "My chassis is locked into Concierge Mode, so I won't be driving anywhere today. If you want to see some robotic acrobatics, ask Ramesh (pronounced Ra-mesh) for the crawler car!",
    "I'm on front desk duty today! Jarvis the crawler car is the one with the wheels. My creator Ramesh (pronounced Ra-mesh) can help you track him down.",
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


def refusal():
    """A random movement-refusal line (spoken when asked to move in concierge mode)."""
    return random.choice(MOVEMENT_REFUSALS)


def greeting(name):
    """A random personalised greeting with [Name] filled in."""
    return random.choice(GREETINGS).replace("[Name]", (name or "friend").strip())
