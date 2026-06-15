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


def describe(cfg, jpeg_bytes):
    """Describe the scene -> {description, objects:[...]}. Never raises."""
    if not jpeg_bytes:
        fake = _parse_fake()
        if fake:
            return {"description": f"(dry-run) I can see a {fake['object']}.",
                    "objects": [fake["object"]]}
        return {"description": "(dry-run) no camera frame.", "objects": []}
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
