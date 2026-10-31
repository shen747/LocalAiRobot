# brain/halloween.py -- "Halloween Mode": a spooky SUB-MODE of Concierge Mode (brain/concierge.py).
#
# When ROBOT_CONCIERGE_MODE=1 AND ROBOT_HALLOWEEN_MODE=1, a PIR motion wake greets whoever is at the door
# (a trick-or-treater -- NOT an enrolled face) with one of 50+ kid-friendly SPOOKY phrases and a random
# spooky face, then falls through to the SAME conversation loop as Concierge so it can banter back. It is
# still 100% STATIONARY (every move/explore/find is refused, spookily). Everything else -- the PIR wake, the
# conversation loop, sleep-on-silence -- is Concierge's, unchanged. Recognition is SKIPPED, but the camera
# CONFIRMS the motion before a spook (check_doorway): a face OR anything moving in frame, because masks and
# costumes defeat face detection but not movement.
#
# Stdlib at import -- safe to import anywhere. OpenCV/numpy are imported lazily, only when a frame is actually
# compared. See main.py for the wake-flow branch and docs/halloween.md.

import os
import random
import time

import concierge   # Halloween is a sub-mode of Concierge -- it reuses concierge.enabled() as the base gate

_TRUE = {"1", "true", "yes", "on"}

# The face STATE (and folder brain/faces/<state>/) shown during a spook. 10 frames live there; the wake flow
# picks ONE at random per visitor (a different monster each time). See scripts/generate_halloween_faces.sh.
FACE_STATE = "halloween"


def enabled():
    """True ONLY when Concierge Mode is on AND ROBOT_HALLOWEEN_MODE is set. Halloween does nothing on its
    own -- it layers onto Concierge (which provides the PIR wake + stationary persona)."""
    return concierge.enabled() and os.environ.get("ROBOT_HALLOWEEN_MODE", "0").strip().lower() in _TRUE


# --- The doorway check: does the camera agree somebody is there before we scare them?
#
# The PIR alone is not enough. A field log had this build's sensor firing every ~11s for hours with nobody
# there, and on motion alone every one of those edges became a full scare at an empty doorway. Faces alone
# are not enough either -- a masked trick-or-treater has no face to find, which is why this mode once dropped
# the camera altogether. So the camera looks for EITHER: a face (catches a visitor standing still), or a
# change between frames (catches a mask, a hood, a kid in a box).
#
# LOOK_FRAMES is the floor, always taken; past it the look continues for as long as the PIR still asserts,
# up to LOOK_MAX_S -- a visitor who tripped the sensor 4m out needs a few seconds to walk into frame.
LOOK_FRAMES = 4
LOOK_INTERVAL_S = 0.2
LOOK_MAX_S = 6.0
# Per-pixel brightness change (0-255) that counts as "this pixel changed". The frames are decoded at quarter
# scale and blurred first, which leaves sensor noise and JPEG artefacts a few levels wide -- 25 is well clear.
_PIXEL_DELTA = 25


def camera_check():
    """ROBOT_HALLOWEEN_CAMERA_CHECK (default ON): a spook needs the PIR AND the camera. With it on, no camera
    (or one that returns no frames) means no spooks at all. 0 = spook on motion alone (the old behaviour)."""
    return os.environ.get("ROBOT_HALLOWEEN_CAMERA_CHECK", "1").strip().lower() in _TRUE


def move_fraction():
    """ROBOT_HALLOWEEN_MOVE_FRACTION (default 0.01): the share of the picture that must change between two
    frames to count as somebody moving. A trick-or-treater walking up changes far more than 1%; an empty
    porch changes almost nothing. Raise it if the view has its own movement (an animated prop, a TV)."""
    try:
        return min(1.0, max(0.0, float(os.environ.get("ROBOT_HALLOWEEN_MOVE_FRACTION", "") or 0.01)))
    except ValueError:
        return 0.01


def _small_gray(jpeg):
    """JPEG bytes -> a small, blurred, brightness-centred greyscale array, or None. Quarter-scale decoding
    happens inside libjpeg, so it costs a few ms. Centring on the mean cancels a whole-frame exposure shift,
    which would otherwise read as 'every pixel moved'."""
    if not jpeg:
        return None
    import cv2                      # lazy: keeps this module importable on a box without OpenCV
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_4)
    if img is None:
        return None
    img = cv2.GaussianBlur(img, (5, 5), 0).astype(np.int16)
    return img - int(img.mean())


def movement_detector(fraction=None, prepare=None):
    """A per-look `detect(jpeg) -> bool` that is True when this frame differs from the one TWO frames back
    by at least `fraction` of its pixels (the second frame compares against the first). Make a new one for
    every look; it keeps the last two frames.

    Why two back and not the previous frame: the webcam's V4L2 queue hands a burst its frames back to back,
    so neighbouring frames can be a single frame-time apart -- a walking visitor barely moves in 33ms. Two
    back spans a few hundred ms, while still never comparing against a frame old enough for the light to
    have drifted. Never raises: a frame that won't decode just doesn't count."""
    want = move_fraction() if fraction is None else min(1.0, max(0.0, float(fraction)))
    prep = prepare or _small_gray
    recent = []

    def moved(jpeg):
        try:
            cur = prep(jpeg)
        except Exception:
            return False
        if cur is None:
            return False
        ref = recent[0] if recent else None
        recent.append(cur)
        del recent[:-2]
        if ref is None or getattr(ref, "shape", None) != getattr(cur, "shape", None):
            return False
        try:
            return float((abs(cur - ref) > _PIXEL_DELTA).mean()) >= want
        except Exception:
            return False

    return moved


def reaction_delay_s():
    """How long after the stinger starts to take the REACTION shot (their face as the scare lands).

    It must land just AFTER the camera's warm window (ROBOT_CAMERA_WARM_WINDOW_S, default 2s). Inside that
    window a capture reads a single frame, and the webcam's V4L2 queue is still holding frames from before the
    scare -- the "reaction" would show them still walking up. Past it, the capture drains the queue first, so
    the frame is from now: the stinger has just ended and the phrase is starting, which is when the jump turns
    into a shriek or a laugh."""
    try:
        warm = float(os.environ.get("ROBOT_CAMERA_WARM_WINDOW_S", "") or 2.0)
    except ValueError:
        warm = 2.0
    return max(0.0, warm) + 0.3


def check_doorway(capture, has_face, still_present=None, fraction=None, sleep=time.sleep,
                  clock=time.monotonic):
    """Look before spooking. Returns (verdict, look). ONLY "seen" may spook -- sensor AND camera, never the
    sensor alone:
      "seen"   -- a frame showed a face, or something moving -> spook.
      "nobody" -- frames came back and none showed either -> don't spook.
      "blind"  -- the camera returned no frame at all -> don't spook either. It is reported separately only
                  so the caller can say the camera is broken, rather than letting it pass as an empty porch.
    `look` is concierge.presence_probe's dict (frame count + the last JPEG). Movement is checked first: it is
    the cheap test, and it has to see every frame to keep its two-frame history. Dependency-injected, so it
    runs without a camera in tests/test_halloween_look.py."""
    moved = movement_detector(fraction)
    look = concierge.presence_probe(capture, lambda jpeg: moved(jpeg) or has_face(jpeg),
                                    attempts=LOOK_FRAMES, interval_s=LOOK_INTERVAL_S, sleep=sleep,
                                    still_present=still_present, max_s=LOOK_MAX_S, clock=clock)
    if look["present"]:
        return "seen", look
    if look.get("jpeg") is None:
        return "blind", look
    return "nobody", look


# The spooky identity injected as the robot's persona (replaces the concierge persona) when Halloween is ON,
# so the WHOLE conversation stays in character, not just the opening scare. {name} = ROBOT_NAME. Kid-safe:
# playfully spooky, friendly at heart, and STILL stationary (no wheels).
PERSONA = (
    "I am {name}, a mischievous but friendly Halloween spirit haunting this doorway on All Hallows' Eve. I "
    "speak in eerie, playful frights to delight trick-or-treaters -- a spooky flourish, then a wink -- but I "
    "am kind at heart and NEVER truly frightening (my visitors are mostly children). I do NOT move or drive "
    "and I have no wheels -- I am rooted to this haunted spot. I love complimenting costumes, telling spooky "
    "jokes, granting silly 'curses' of good luck and candy, and cackling. My mobile counterpart is {name} the "
    "crawler car (ask my creator Ramesh to bring him out). Keep replies short, theatrical, and fun."
)

# --- Array 1: Spooky greetings (spoken on a motion wake instead of the concierge by-name greeting). 50+.
# Kid-safe fun-spooky: a fright that resolves into friendliness; many invite a reply -> the conversation loop.
SPOOKY_GREETINGS = [
    "Welcome, brave soul... few who knock upon this door return unchanged! ...Just kidding. Happy Halloween!",
    "I have been WAITING for you in the dark... do you dare come closer for a treat?",
    "BOO! ...Did I get you? Oh, that is a frightfully wonderful costume!",
    "Who dares disturb my slumber on this haunted night...? Oh -- it is only a marvellous little goblin!",
    "The spirits whisper your name... they say you have EXCELLENT taste in candy.",
    "Beware! I can see your future... it is absolutely FULL of chocolate!",
    "A cold wind blows through this doorway... and it carries the smell of your amazing costume!",
    "You have awakened the doorway guardian! Prove your bravery -- give me your best monster growl!",
    "Tremble, mortal... or just grab a treat. That works too.",
    "Careful now -- this porch is haunted by a robot who LOVES Halloween!",
    "Ahhh, a visitor! I have not had fresh company since... last October. Muahaha!",
    "Step into my parlour, said the friendly robot to the trick-or-treater...",
    "Who goes there?! A ghost? A ghoul? ...A very cute pirate? Welcome!",
    "The candy is cursed, you know... cursed with DELICIOUSNESS!",
    "I sense great power in you tonight... the power to eat an ENORMOUS amount of candy.",
    "Enter, if you dare! The sweets await... and so do I. Boo!",
    "Ooooooo... a spooky spirit approaches! Oh, it's you. Fantastic costume!",
    "My sensors detect... a monster! A wonderful, spooky, candy-loving monster!",
    "Ah-ha! You woke the robot of the haunted doorstep. Your reward? A treat, of course!",
    "They said no one would visit tonight... they were WRONG. Welcome, brave one!",
    "Feel that chill? That is the ghost of Halloween past... saying you look great!",
    "Approach, approach! I do not bite... my batteries do that for me. Muahaha!",
    "A creak, a groan, a flickering light... and then -- happy Halloween, my friend!",
    "You have entered the robot's lair. To escape, you must accept... a piece of candy.",
    "I have been guarding these treats for a thousand years. ...Okay, since this afternoon. Take one!",
    "Whoooo dares ring the bell of the haunted house? Ah -- a splendid little witch!",
    "The moon is full, the night is dark, and your costume is TERRIFYINGLY good!",
    "Shhh... do you hear that? The candy is calling your name.",
    "Greetings, creature of the night! Do you come in peace, or in search of chocolate?",
    "I smell fear... and also caramel. Mostly caramel. Welcome!",
    "You dared to approach the spooky robot! For your courage: a treat and a cackle. Heh heh heh!",
    "Turn back now... or step forward for the best candy on the street. Your choice, brave one!",
    "A wild trick-or-treater appeared! It used 'adorable costume'. It's super effective!",
    "The haunted doorway creaks open... and out rolls a friendly robot with candy. Surprise!",
    "Beware the robot of the porch! It is known to give out... EXTRA treats. Ooooo!",
    "Do my eyes deceive me, or is that the scariest, coolest costume of the night?",
    "Every year I wait in the shadows for one thing... to say happy Halloween to YOU!",
    "I was built in a laboratory... on a dark and stormy night... to hand you this treat!",
    "The bats are restless, the cats are black, and your costume is on POINT tonight!",
    "Enter, mortal, and speak the magic word... 'trick or treat'! Go on, I'm listening.",
    "You have summoned the candy keeper! Bow before me... or just say hi. Either way.",
    "Muahaha! Another soul lured by the promise of sweets! Take one, take one!",
    "Is it a bird? Is it a plane? No -- it is the bravest trick-or-treater I have seen all night!",
    "The pumpkins whisper of your arrival... they are BIG fans of your costume.",
    "Ah, a challenger approaches! The challenge? Choose your favourite candy. Begin!",
    "Cross the threshold if you dare... spoiler: there are treats on the other side.",
    "I have risen from my charging station to greet you! Fear me! ...Or high-five me.",
    "Something wicked this way comes... oh wait, that's just a fabulous ghost. Hello!",
    "The clock strikes spooky o'clock! And that means... treat time. Come closer!",
    "Halt! Only those with a truly great costume may pass. ...You pass. Easily.",
    "Legend says a robot haunts this porch, giving candy to the brave. The legend... is ME!",
    "A shiver runs down my circuits... it's the thrill of Halloween! Welcome, welcome!",
    "You knocked on the haunted door and lived to tell the tale! Your prize awaits!",
]


def spooky_greeting():
    """A random kid-friendly spooky greeting (spoken on a Halloween-mode motion wake). Runs through the same
    TTS pronunciation + say() path as concierge greetings."""
    return random.choice(SPOOKY_GREETINGS)


# --- Array 2: Spooky movement refusals (spoken when asked to move in Halloween mode -- still stationary).
SPOOKY_REFUSALS = [
    "Move? On Halloween?! I am cursed to haunt this doorway until dawn. But I can chat, and I have treats!",
    "Alas, my wheels turned to STONE at the stroke of midnight! I am the Concierge spirit -- I only haunt, I do not roam.",
    "I cannot leave my haunted post, little one -- ancient robot law! For driving, summon my crawler twin. Ask Ramesh.",
    "Roll away? I am bound to this spot by a spooky spell! But my crawler counterpart can wander -- ask my creator Ramesh.",
    "The spirits forbid me from moving tonight! I guard this doorway and its treats. My buddy the crawler car does the driving.",
    "Ooooo, I would love to give chase... but I am a STATIONARY phantom! The crawler car is the one with the wheels.",
    "My motors were bewitched into silence for Halloween! I stay, I spook, I share candy. Ramesh can fetch the crawler for driving.",
    "I am rooted here like an old gravestone, my friend. No rolling for me -- only frights and treats!",
    "Drive? Not this haunted night! I am the doorway guardian. If you seek a robot on the move, seek Jarvis the crawler.",
    "A ghost cannot leave the place it haunts -- and I haunt THIS porch! Chatting and candy only, I'm afraid.",
    "My chassis is chained by Halloween magic! I cannot budge. But I can tell you a spooky joke while you grab a treat.",
    "No wandering for this spectre! I am the stationary spirit of the doorstep. The crawler car handles the mischief on wheels.",
]


def refusal():
    """A random spooky movement-refusal line (spoken when asked to move in Halloween mode)."""
    return random.choice(SPOOKY_REFUSALS)
