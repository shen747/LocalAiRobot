# brain/halloween.py -- "Halloween Mode": a spooky SUB-MODE of Concierge Mode (brain/concierge.py).
#
# When ROBOT_CONCIERGE_MODE=1 AND ROBOT_HALLOWEEN_MODE=1, a PIR motion wake greets whoever is at the door
# (a trick-or-treater -- NOT an enrolled face) with one of 50+ kid-friendly SPOOKY phrases and a random
# spooky face, then falls through to the SAME conversation loop as Concierge so it can banter back. It is
# still 100% STATIONARY (every move/explore/find is refused, spookily). Everything else -- the PIR wake, the
# strictly-local face DETECTION, the conversation loop, sleep-on-silence -- is Concierge's, unchanged.
#
# Pure stdlib -- safe to import anywhere. See main.py for the wake-flow branch and docs/halloween.md.

import os
import random

import concierge   # Halloween is a sub-mode of Concierge -- it reuses concierge.enabled() as the base gate

_TRUE = {"1", "true", "yes", "on"}

# The face STATE (and folder brain/faces/<state>/) shown during a spook. 10 frames live there; the wake flow
# picks ONE at random per visitor (a different monster each time). See scripts/generate_halloween_faces.sh.
FACE_STATE = "halloween"


def enabled():
    """True ONLY when Concierge Mode is on AND ROBOT_HALLOWEEN_MODE is set. Halloween does nothing on its
    own -- it layers onto Concierge (which provides the PIR wake + stationary persona)."""
    return concierge.enabled() and os.environ.get("ROBOT_HALLOWEEN_MODE", "0").strip().lower() in _TRUE


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
