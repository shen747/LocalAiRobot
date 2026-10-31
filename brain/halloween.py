# brain/halloween.py -- "Halloween Mode": a spooky SUB-MODE of Concierge Mode (brain/concierge.py).
#
# When ROBOT_CONCIERGE_MODE=1 AND ROBOT_HALLOWEEN_MODE=1, a PIR motion wake greets whoever is at the door
# (a trick-or-treater -- NOT an enrolled face) with one of 50+ kid-friendly SPOOKY phrases and a random
# spooky face, then falls through to the SAME conversation loop as Concierge so it can banter back. It is
# still 100% STATIONARY (every move/explore/find is refused, spookily). Everything else -- the PIR wake, the
# conversation loop, sleep-on-silence -- is Concierge's, unchanged. Recognition is SKIPPED, but the camera
# CONFIRMS the motion before a spook (check_doorway): a PERSON in frame, found by a body detector, so masks
# and costumes don't matter -- or a face.
#
# Stdlib at import -- safe to import anywhere. OpenCV/numpy are imported lazily, only when a frame is actually
# looked at. See main.py for the wake-flow branch and docs/halloween.md.

import logging
import os
import random
import time

import concierge   # Halloween is a sub-mode of Concierge -- it reuses concierge.enabled() as the base gate

log = logging.getLogger("robot.halloween")

_TRUE = {"1", "true", "yes", "on"}

# The face STATE (and folder brain/faces/<state>/) shown during a spook. 10 frames live there; the wake flow
# picks ONE at random per visitor (a different monster each time). See scripts/generate_halloween_faces.sh.
FACE_STATE = "halloween"


def enabled():
    """True ONLY when Concierge Mode is on AND ROBOT_HALLOWEEN_MODE is set. Halloween does nothing on its
    own -- it layers onto Concierge (which provides the PIR wake + stationary persona)."""
    return concierge.enabled() and os.environ.get("ROBOT_HALLOWEEN_MODE", "0").strip().lower() in _TRUE


# --- The doorway check: is there a PERSON at the door before we scare them?
#
# The PIR alone is not enough. A field log had this build's sensor firing every ~11s for hours with nobody
# there, and on motion alone every one of those edges became a full scare at an empty doorway. Faces alone
# are not enough either -- a masked trick-or-treater has no face to find, which is why this mode once dropped
# the camera altogether. Movement is not enough: on a real porch the door glass reflects the street and the
# sun moves across the bricks, and both read as "something moved". So the camera asks the question that
# matters with a person DETECTOR -- a body, mask or no mask -- and also accepts a face.
#
# LOOK_FRAMES is the floor, always taken; past it the look continues for as long as the PIR still asserts,
# up to LOOK_MAX_S -- a visitor who tripped the sensor 4m out needs a few seconds to walk into frame.
LOOK_FRAMES = 4
LOOK_INTERVAL_S = 0.2
LOOK_MAX_S = 6.0

# The detector: OpenCV Zoo's NanoDet-Plus (COCO, Apache-2.0), committed as models/person_nanodet.onnx (3.8MB)
# so a git pull is the whole install. 416x416 input, ~50-100ms a frame on a Pi 5 through cv2.dnn. Only its
# "person" class is read.
PERSON_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "models",
                            "person_nanodet.onnx")
_SIZE = 416
_STRIDES = (8, 16, 32)              # this export has three output levels (2704 / 676 / 169 anchors)
_REG_MAX = 7
_MEAN = (103.53, 116.28, 123.675)   # BGR, the order NanoDet was trained in
_STD = (57.375, 57.12, 58.395)
# A person must be at least this share of the frame's height. That is somebody near the door (a kid ~4m from
# a wide lens is ~0.16); it rules out the small people in the door glass's reflection of the street, and
# passers-by far off.
MIN_PERSON_HEIGHT = 0.15


def camera_check():
    """ROBOT_HALLOWEEN_CAMERA_CHECK (default ON): a spook needs the PIR AND the camera. With it on, no camera
    (or one that returns no frames) means no spooks at all. 0 = spook on motion alone (the old behaviour)."""
    return os.environ.get("ROBOT_HALLOWEEN_CAMERA_CHECK", "1").strip().lower() in _TRUE


def person_score():
    """ROBOT_HALLOWEEN_PERSON_SCORE (default 0.5): how sure the detector must be that a box is a person.
    Measured: people in test photos 0.78-0.84; this robot's empty porch 0.07 (0.24-0.30 before the
    MIN_PERSON_HEIGHT cut drops the street reflected in the door glass). Raise it if something on the porch
    keeps passing (a life-size decoration); lower it if costumed kids get missed."""
    try:
        return min(1.0, max(0.0, float(os.environ.get("ROBOT_HALLOWEEN_PERSON_SCORE", "") or 0.5)))
    except ValueError:
        return 0.5


class PersonDetector:
    """`detector(jpeg) -> bool`: is there a person in this frame? Loads the model on first use (or via
    load()); never raises -- a missing model or a bad frame reads as "no person", and load() says why once."""

    def __init__(self, path=None):
        self.path = path or PERSON_MODEL
        self._net = None
        self._failed = False
        self.last_score = 0.0        # best person score in the last frame -- for the decision log line

    def load(self):
        if self._net is not None:
            return True
        if self._failed:
            return False
        try:
            import cv2                  # lazy: keeps this module importable on a box without OpenCV
            import numpy as np
            net = cv2.dnn.readNet(self.path)
            self._cv2, self._np, self._net = cv2, np, net
            self._outs = net.getUnconnectedOutLayersNames()
            return True
        except Exception as e:
            self._failed = True
            log.error("halloween: could not load the person detector %s (%s) -- with the camera check on, "
                      "only a visible FACE can confirm a visitor now. git pull should have brought it.",
                      self.path, e)
            return False

    def best(self, jpeg):
        """The highest person score among boxes at least MIN_PERSON_HEIGHT of the frame tall (0.0 = none)."""
        self.last_score = 0.0
        if not jpeg or not self.load():
            return 0.0
        cv2, np = self._cv2, self._np
        try:
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return 0.0
            h, w = img.shape[:2]
            k = _SIZE / max(h, w)
            nh, nw = int(round(h * k)), int(round(w * k))
            box = np.zeros((_SIZE, _SIZE, 3), np.float32)
            box[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
            box = (box - np.array(_MEAN, np.float32)) / np.array(_STD, np.float32)
            self._net.setInput(cv2.dnn.blobFromImage(box))
            outs = self._net.forward(self._outs)
            proj = np.arange(_REG_MAX + 1, dtype=np.float32)
            best = 0.0
            for s, cls, reg in zip(_STRIDES, outs[::2], outs[1::2]):
                p = cls.reshape(-1, cls.shape[-1])[:, 0]          # class 0 = person
                reg = reg.reshape(-1, _REG_MAX + 1)
                e = np.exp(reg - reg.max(axis=1, keepdims=True))
                dist = ((e / e.sum(axis=1, keepdims=True)) @ proj).reshape(-1, 4) * s   # l, t, r, b
                tall = (dist[:, 1] + dist[:, 3]) / k >= MIN_PERSON_HEIGHT * h
                if tall.any():
                    best = max(best, float(p[tall].max()))
            self.last_score = best
            return best
        except Exception as e:
            log.debug("halloween: person detector failed on a frame: %s", e)
            return 0.0

    def __call__(self, jpeg):
        return self.best(jpeg) >= person_score()


_detector = PersonDetector()


def person_detector():
    """The shared detector -- one model in memory for the whole run."""
    return _detector


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


def check_doorway(capture, has_face, still_present=None, has_person=None, sleep=time.sleep,
                  clock=time.monotonic):
    """Look before spooking. Returns (verdict, look). ONLY "seen" may spook -- sensor AND camera, never the
    sensor alone:
      "seen"   -- a frame showed a person (or a face) -> spook.
      "nobody" -- frames came back and none showed one -> don't spook.
      "blind"  -- the camera returned no frame at all -> don't spook either. It is reported separately only
                  so the caller can say the camera is broken, rather than letting it pass as an empty porch.
    `look` is concierge.presence_probe's dict (frame count + the last JPEG). The person detector runs first;
    the face check only when it said no (a close-up face can fill too much of the frame for the body
    detector). Dependency-injected, so it runs without a camera in tests/test_halloween_look.py."""
    person = has_person or person_detector()
    look = concierge.presence_probe(capture, lambda jpeg: person(jpeg) or has_face(jpeg),
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
