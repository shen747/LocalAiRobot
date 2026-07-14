# brain/vision.py -- multimodal "look" via a dedicated VLM (ROBOT_VISION_MODEL, default moondream;
# falls back to sharing ROBOT_MODEL when unset).
#
# A captured JPEG is sent to Ollama's native /api/generate as a base64 `images` array with a
# JSON-schema `format`, so the model returns a structured, enum-constrained scene report that
# maps straight onto steering. The call goes through brain/inference.py (the shared model_lock +
# keep-alive gateway), so this slow vision call and the text-intent call never hit Ollama at the
# same time. CPU-only vision on the Pi 5 is SLOW (tens of seconds to minutes/image), so callers
# must stop the car first and treat this as an occasional discrete "look", never a loop input.
#
# Dry run / no camera: if there is no frame, fall back to ROBOT_FAKE_VISION
# (format "target:horizontal:distance:confidence", e.g. "sofa:left:near:0.9") so the explore
# loop can be exercised deterministically on an unwired Pi / laptop with no Ollama call.

import base64
import logging
import os

import inference

log = logging.getLogger("robot.vision")

_HORIZ = ("left", "center", "right")
_DIST = ("near", "far")

LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "object": {"type": "string"},
        "visible": {"type": "boolean"},
        "horizontal": {"type": "string", "enum": list(_HORIZ)},
        "distance": {"type": "string", "enum": list(_DIST)},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["object", "visible", "confidence"],
}

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "objects"],
}

# Agentic EXPLORE: ONE multimodal call per step returns the scene AND the next move + whether to stop, so
# a smart (remote) model can steer the exploration itself. move/steer are enum-constrained onto the driver;
# the ultrasonic gate in driver.command is still the safety authority -- this only *suggests* a move.
EXPLORE_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "move": {"type": "string", "enum": ["FORWARD", "STOP"]},
        "steer": {"type": "string", "enum": ["LEFT", "CENTER", "RIGHT"]},
        "speak": {"type": "string"},
        "done": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["description", "move", "done"],
}


def _vision_timeout():
    try:
        return float(os.environ.get("ROBOT_VISION_TIMEOUT_S", "600") or 600)
    except ValueError:
        return 600.0


def _ollama_image(cfg, prompt, jpeg_bytes, schema):
    # Route through the shared inference gateway: ONE model_lock serialises this with the text
    # intent call so two generates never hit Ollama at once (the software twin of the reference's
    # /dev/hailo0 device-exclusivity crash), and keep_alive keeps the model resident. Uses a
    # dedicated vision model when ROBOT_VISION_MODEL is set, otherwise falls back to ROBOT_MODEL.
    return inference.ollama_generate(cfg, {
        "model": cfg.get("vision_model") or cfg["model"],
        "prompt": prompt,
        "images": [base64.b64encode(jpeg_bytes).decode("ascii")],
        "format": schema,
        "options": {"temperature": 0, "num_predict": 220},
    }, _vision_timeout())


def _parse_fake():
    """Parse ROBOT_FAKE_VISION='target:horizontal:distance:confidence' -> dict or None."""
    raw = os.environ.get("ROBOT_FAKE_VISION", "").strip()
    if not raw:
        return None
    parts = (raw.split(":") + ["", "", "", ""])[:4]
    target, horiz, dist, conf = parts
    try:
        conf = float(conf) if conf else 0.9
    except ValueError:
        conf = 0.9
    return {
        "object": target or "object",
        "horizontal": horiz if horiz in _HORIZ else "center",
        "distance": dist if dist in _DIST else "far",
        "confidence": conf,
    }


def _normalize_look(obj, target):
    visible = bool(obj.get("visible"))
    horiz = obj.get("horizontal")
    dist = obj.get("distance")
    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "object": str(obj.get("object") or target),
        "visible": visible,
        "horizontal": horiz if horiz in _HORIZ else ("center" if visible else None),
        "distance": dist if dist in _DIST else None,
        "confidence": max(0.0, min(1.0, conf)),
        "notes": str(obj.get("notes") or ""),
    }


def look(cfg, target, jpeg_bytes):
    """Is `target` visible? -> {object, visible, horizontal, distance, confidence, notes}.
    Never raises: on any error returns a fail-safe 'not visible' result."""
    if not jpeg_bytes:
        fake = _parse_fake()
        if fake and (target.lower() in fake["object"].lower()
                     or fake["object"].lower() in target.lower()):
            log.info("vision LOOK (FAKE): %s visible at %s/%s conf=%.2f",
                     target, fake["horizontal"], fake["distance"], fake["confidence"])
            return _normalize_look({**fake, "visible": True, "notes": "fake vision"}, target)
        log.info("vision LOOK: no frame -> '%s' not visible", target)
        return _normalize_look({"object": target, "visible": False,
                                "notes": "no camera frame"}, target)
    prompt = (
        "You are the eyes of a small RC car. Look at the image and reply with JSON only. "
        f"Is a '{target}' visible? Set visible true/false. If visible, give horizontal "
        "(left/center/right from the car's view) and distance (near/far) and a confidence 0..1. "
        "Put any other notable objects or obstacles in notes."
    )
    try:
        obj = _ollama_image(cfg, prompt, jpeg_bytes, LOOK_SCHEMA)
        result = _normalize_look(obj, target)
        log.info("vision LOOK: %s visible=%s horiz=%s dist=%s conf=%.2f",
                 target, result["visible"], result["horizontal"],
                 result["distance"], result["confidence"])
        return result
    except Exception as e:
        log.warning("vision LOOK failed (%s) -> treating as not visible", e)
        return _normalize_look({"object": target, "visible": False,
                                "notes": f"vision error: {e}"}, target)


def describe(cfg, jpeg_bytes, query=None):
    """Describe the scene -> {description, objects:[...]}. Never raises. If `query` is given (the operator's
    actual question, e.g. 'what's in the framed photo?'), the prompt FOCUSES the answer on what they asked --
    including the CONTENT of a specific thing (a photo/screen/page/label), not just that it exists -- instead
    of the generic one-sentence scene summary. No query -> the old scene summary (REMEMBER / scene logging)."""
    if not jpeg_bytes:
        fake = _parse_fake()
        if fake:
            return {"description": f"(dry-run) I can see a {fake['object']}.",
                    "objects": [fake["object"]]}
        return {"description": "(dry-run) no camera frame.", "objects": []}
    q = (query or "").strip()
    if q:
        prompt = (
            "You are the eyes of a small RC car. The operator asked: \"" + q + "\". Answer by describing "
            "what you ACTUALLY SEE in the image, focused on what they asked. If they ask about a specific "
            "thing -- a photo, a screen, a page, a label, a sign -- describe the CONTENT of that thing (what "
            "is depicted or written on it), not merely that it is present. Use 1-3 sentences, then list the "
            "notable objects you can see. JSON only. If you truly cannot make out the detail, say so briefly."
        )
    else:
        prompt = (
            "You are the eyes of a small RC car. Briefly describe the scene in one sentence and list "
            "the notable objects / furniture you can see (e.g. sofa, table, doorway, wall). JSON only."
        )
    try:
        obj = _ollama_image(cfg, prompt, jpeg_bytes, DESCRIBE_SCHEMA)
        desc = str(obj.get("description") or "")
        objs = [str(o) for o in obj.get("objects", []) if o]
        log.info("vision DESCRIBE: %s | objects=%s", desc[:80], objs)
        return {"description": desc, "objects": objs}
    except Exception as e:
        log.warning("vision DESCRIBE failed (%s)", e)
        return {"description": "", "objects": []}


def _normalize_step(obj):
    move = str(obj.get("move") or "FORWARD").upper()
    if move not in ("FORWARD", "STOP"):
        move = "FORWARD"
    steer = str(obj.get("steer") or "CENTER").upper()
    if steer not in ("LEFT", "CENTER", "RIGHT"):
        steer = "CENTER"
    return {
        "description": str(obj.get("description") or ""),
        "objects": [str(o) for o in (obj.get("objects") or []) if o],
        "move": move,
        "steer": steer,
        "speak": str(obj.get("speak") or ""),
        "done": bool(obj.get("done")),
        "reason": str(obj.get("reason") or ""),
    }


def explore_step(cfg, jpeg_bytes, goal="", memory_summary="", distances=None):
    """One agentic explore step: given the current frame + memory + sensor distances, return the scene
    AND the next move -> {description, objects[], move(FORWARD|STOP), steer(LEFT|CENTER|RIGHT), speak,
    done, reason}. Never raises: on any error (or no frame) returns a fail-safe STOP+done so the loop ends."""
    distances = distances or {}
    fmt = lambda v: f"{v:.0f}" if isinstance(v, (int, float)) else "?"
    if not jpeg_bytes:
        fake = _parse_fake()
        if fake:
            return _normalize_step({
                "description": f"(dry-run) I can see a {fake['object']}.",
                "objects": [fake["object"]], "move": "FORWARD",
                "steer": (fake.get("horizontal") or "center").upper(),
                "speak": f"I can see a {fake['object']}.", "done": False, "reason": "fake vision"})
        return {"description": "(dry-run) no camera frame.", "objects": [], "move": "STOP",
                "steer": "CENTER", "speak": "", "done": True, "reason": "no camera"}
    prompt = (
        "You are the driver AND the eyes of a small RC car exploring a room. Look at the image and choose "
        "the SINGLE next move. Reply with JSON only.\n"
        f"Goal: {goal or 'look around and learn what is in this space'}.\n"
        f"Already seen (memory): {memory_summary or 'nothing yet'}.\n"
        f"Ultrasonic distance to obstacles, cm: front={fmt(distances.get('front'))}, "
        f"left={fmt(distances.get('left'))}, right={fmt(distances.get('right'))}.\n"
        "Give a one-sentence description and the notable objects. Then pick move (FORWARD to advance, STOP "
        "to hold position) and steer (LEFT/CENTER/RIGHT) toward something interesting or an OPEN path -- do "
        "not steer into a close obstacle. Put your navigation reasoning in reason (NOT spoken). In speak, put "
        "a VERY short remark (a few words) ONLY about something newly notable -- leave speak EMPTY if the view "
        "is the same as before. Keep exploring: strongly PREFER to keep MOVING toward open space you have not "
        "visited yet. Set done=true ONLY when you are genuinely boxed in (no open path in any direction) or the "
        "SAME view has repeated for several steps in a row -- NOT merely because you have glanced around once."
    )
    try:
        step = _normalize_step(_ollama_image(cfg, prompt, jpeg_bytes, EXPLORE_STEP_SCHEMA))
        log.info("vision EXPLORE_STEP: move=%s steer=%s done=%s | %s",
                 step["move"], step["steer"], step["done"], step["description"][:80])
        return step
    except Exception as e:
        log.warning("vision EXPLORE_STEP failed (%s) -> STOP + done", e)
        return {"description": "", "objects": [], "move": "STOP", "steer": "CENTER",
                "speak": "", "done": True, "reason": f"vision error: {e}"}


PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "person_present": {"type": "boolean"},
        "description": {"type": "string"},
    },
    "required": ["person_present"],
}


def check_person(cfg, jpeg_bytes):
    """Quick 'is a PERSON in front of the camera right now?' check -> {present: bool, description: str}.
    Run on wake so the brain knows someone is there before it answers. Never raises."""
    if not jpeg_bytes:
        return {"present": False, "description": ""}
    prompt = ("Look at the image. Is there a PERSON (a human) clearly in front of the camera, reasonably "
              "close? Reply JSON only: person_present true/false, and if true a SHORT description (a few "
              "words, e.g. 'a man with glasses'). A distant person or an empty background = false.")
    try:
        obj = _ollama_image(cfg, prompt, jpeg_bytes, PERSON_SCHEMA)
        present, desc = bool(obj.get("person_present")), str(obj.get("description") or "")
        log.info("vision PERSON: present=%s %s", present, desc[:60])
        return {"present": present, "description": desc}
    except Exception as e:
        log.warning("vision person-check failed (%s)", e)
        return {"present": False, "description": ""}
