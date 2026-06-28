# brain/inference.py -- the single, serialised gateway to the model backend (local Ollama OR remote Gemini).
#
# BACKEND SWITCH: ROBOT_AI_BACKEND (cfg['ai_backend']) selects 'local' (Ollama on the Pi, default,
# fully offline) or 'gemini' (Google Gemini, remote). The switch lives ONLY here -- the two callers
# (text intent in main.query_ollama, vision in vision._ollama_image) build one Ollama-shaped payload
# and are backend-agnostic. The Gemini path translates that payload to a generateContent request and
# returns the model's JSON text, so the shared parse/validate tail below is reused unchanged.
#
# WHY THIS EXISTS: text-intent (main.query_ollama) and vision (vision._ollama_image) both POST
# to the SAME Ollama server and historically the SAME model (gemma3:4b). They were serialised
# only by accident -- the single-threaded command loop happened never to overlap two calls.
# Add any second caller (an always-listening wake thread, a debug curl, a future async path) and
# a slow vision call (tens of seconds to minutes) would queue the text request until it times out
# and fail-safes to STOP. That is the software twin of the "warming the VLM grabs /dev/hailo0 and
# crashes the LLM" hazard from the reference project.
#
# So every model call now goes through ollama_generate(), which holds ONE module-level
# model_lock for the duration of the HTTP request. The lock is near-free today (the loop already
# serialises) and is insurance the moment any concurrency is introduced.
#   Honest limit: an in-PROCESS lock only guards THIS process. A second OS process / curl hitting
#   the same Ollama is NOT covered -- for that the protection is keep_alive (model stays resident)
#   plus simply not spawning extra clients.
#
# It also (a) injects keep_alive so Ollama doesn't evict the model between commands (eviction =
# the next command cold-loads again), and (b) exposes warm_up() for a boot-time priming call so
# the FIRST real command isn't a multi-minute cold load (see config/robot.env:ROBOT_LLM_TIMEOUT_S,
# which today warns you to raise the timeout for exactly that reason).
#
# Pure stdlib (json/urllib/threading) -- no new dependency, imports fine on a laptop.

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger("robot.infer")

# The one lock every model call shares. Mirrors main.py's audio_lock (half-duplex audio);
# this is half-duplex *inference*: at most one Ollama generate in flight from this process.
model_lock = threading.Lock()


def _keep_alive():
    """ROBOT_OLLAMA_KEEPALIVE controls how long Ollama keeps the model resident after a call.
    Ollama's keep_alive is EITHER a duration string WITH A UNIT ('30m', '1h', '90s') OR a NUMBER
    of seconds (0 = unload now, negative = keep forever). A bare number like '-1' or '300' is NOT
    a valid duration string, so we must send it as a JSON number, not a string -- otherwise Ollama
    fails to parse the request and every generate call 400s. So: coerce an integer-looking value
    to int (JSON number); pass a unit-suffixed string through unchanged; '' -> None (Ollama default)."""
    v = (os.environ.get("ROBOT_OLLAMA_KEEPALIVE", "30m") or "").strip()
    if not v:
        return None
    try:
        return int(v)          # '-1' / '0' / '300' -> JSON number (Ollama: <0 keeps it forever)
    except ValueError:
        return v               # '30m' / '1h' / '90s' -> duration string


def _num_ctx():
    """ROBOT_NUM_CTX caps Ollama's context window (KV cache). Gemma 4 defaults to 128K -- far more
    than the robot's short prompts need, and a big KV cache is slow + RAM-hungry on the Pi. ~4096 is
    plenty for the intent prompt AND a moondream image. 0 / '' / unset -> Ollama's default."""
    try:
        n = int(os.environ.get("ROBOT_NUM_CTX", "4096") or 0)
    except ValueError:
        return 0
    return n if n > 0 else 0


def _post(cfg, payload, timeout):
    """POST one /api/generate request under model_lock and return the parsed envelope dict.
    keep_alive is injected here so every path benefits. The lock spans only the HTTP call --
    that is exactly the window the server holds the model busy."""
    body = dict(payload)
    body.setdefault("stream", False)
    ka = _keep_alive()
    if ka is not None:
        body["keep_alive"] = ka
    nc = _num_ctx()
    if nc:
        opts = dict(body.get("options") or {})
        opts.setdefault("num_ctx", nc)     # cap the KV cache; a caller's explicit num_ctx still wins
        body["options"] = opts
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg["ollama_url"], data=data, headers={"Content-Type": "application/json"})
    with model_lock:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())


def ollama_generate(cfg, payload, timeout):
    """Run a generate call and return the model's parsed JSON OBJECT.

    Dispatches by backend (cfg['ai_backend'], from ROBOT_AI_BACKEND): 'gemini' -> Google Gemini
    (remote), anything else -> local Ollama (default). Both backends yield the model's response as
    a JSON *string*; the shared tail parses + validates it to a dict, so the two callers (text intent
    + vision) stay backend-agnostic. Raises ValueError on an empty/blocked response or a non-dict
    body -- callers treat the raise as fail-safe (main.py -> STOP; vision.py -> 'not visible')."""
    if cfg.get("ai_backend") == "gemini":
        response = _gemini_generate(cfg, payload, timeout)
    else:
        response = _post(cfg, payload, timeout).get("response")
    if not response:
        raise ValueError(f"model returned no response (backend={cfg.get('ai_backend') or 'local'})")
    obj = json.loads(response)
    if not isinstance(obj, dict):
        # JSON mode guarantees valid JSON, not a JSON *object* -- guard so a list/string can't
        # crash the caller later at obj.get(...).
        raise ValueError(f"model did not return a JSON object: {response[:200]!r}")
    return obj


# ---------------------------------------------------------------------------
# Google Gemini (remote) backend. Same gateway, different wire format. Used only when
# ROBOT_AI_BACKEND=gemini. Translates the Ollama-shaped payload the callers already build
# (model/prompt/images[b64]/format/options) into a generateContent request and returns the
# model's JSON TEXT, so ollama_generate's shared parse/validate tail is reused. Pure stdlib.
# ---------------------------------------------------------------------------
_GEMINI_TYPE = {  # JSON-schema type -> Gemini Schema Type enum (proto JSON wants the upper-case name)
    "object": "OBJECT", "array": "ARRAY", "string": "STRING",
    "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN",
}


def _to_gemini_schema(schema):
    """Translate the small JSON-schema dict vision.py passes into a Gemini responseSchema. Only the
    constructs the robot uses (object/array/string + properties/items/required) are mapped, with the
    type names upper-cased to Gemini's Type enum. Returns None on anything odd, so a schema quirk
    falls back to plain JSON mode rather than crashing a look."""
    if not isinstance(schema, dict):
        return None
    out = {}
    t = schema.get("type")
    if isinstance(t, str):
        out["type"] = _GEMINI_TYPE.get(t.lower(), t.upper())
    props = schema.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: _to_gemini_schema(v) for k, v in props.items()}
    if "items" in schema:
        out["items"] = _to_gemini_schema(schema["items"])
    if "required" in schema:
        out["required"] = schema["required"]
    return out


def _gemini_generate(cfg, payload, timeout):
    """POST one Gemini generateContent request (under model_lock) and return the model's JSON text."""
    api_key = (cfg.get("gemini_api_key") or "").strip()
    if not api_key:
        raise ValueError("ROBOT_AI_BACKEND=gemini but ROBOT_GEMINI_API_KEY is empty "
                         "(get one: docs/gemini-setup.md)")
    is_vision = bool(payload.get("images"))
    model = (cfg.get("gemini_vision_model") if is_vision else "") or \
        cfg.get("gemini_model") or "gemini-2.5-flash"

    parts = [{"text": payload.get("prompt", "")}]
    for b64 in payload.get("images") or []:
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64}})

    gen = {"responseMimeType": "application/json"}   # both callers want JSON back
    opts = payload.get("options") or {}
    if "temperature" in opts:
        gen["temperature"] = opts["temperature"]
    if "num_predict" in opts:
        gen["maxOutputTokens"] = opts["num_predict"]
    fmt = payload.get("format")
    if isinstance(fmt, dict):            # vision passes a JSON schema; text passes "json" (skipped)
        schema = _to_gemini_schema(fmt)
        if schema:
            gen["responseSchema"] = schema

    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": gen}).encode("utf-8")
    base = (cfg.get("gemini_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    req = urllib.request.Request(
        f"{base}/models/{model}:generateContent", data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    with model_lock:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            envelope = json.loads(resp.read())
    return _gemini_text(envelope)


def _gemini_text(envelope):
    """Pull the model's text out of a generateContent reply, or raise with the reason. A blocked
    prompt has no candidates (promptFeedback.blockReason); a truncated/blocked answer carries a
    finishReason. Either way we raise so the caller fail-safes (STOP / 'not visible')."""
    candidates = envelope.get("candidates") or []
    if not candidates:
        fb = envelope.get("promptFeedback") or {}
        raise ValueError(f"gemini returned no candidates (feedback: {str(fb)[:200]!r})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ValueError(f"gemini returned empty text (finishReason: {candidates[0].get('finishReason')!r})")
    return text


def warm_up(cfg, timeout):
    """Best-effort boot-time priming generate so the model is resident before the first command.
    Sends a 1-token throwaway prompt (no JSON format) under model_lock + keep_alive. Never raises:
    a failed warmup (e.g. Ollama not up yet, or a laptop with no server) just falls back to the
    old lazy cold-load behaviour. Returns True on success. The remote Gemini backend has no model to
    cold-load, so it's a no-op success there."""
    if cfg.get("ai_backend") == "gemini":
        return True
    try:
        _post(cfg, {"model": cfg["model"], "prompt": "ok",
                    "options": {"num_predict": 1}}, timeout)
        return True
    except Exception as e:
        log.warning("model warmup failed (%s) -- first command may cold-load", e)
        return False
