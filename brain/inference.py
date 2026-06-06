# brain/inference.py -- the single, serialised gateway to the Ollama model server.
#
# WHY THIS EXISTS: text-intent (main.query_ollama) and vision (vision._ollama_image) both POST
# to the SAME Ollama server and historically the SAME model (gemma3:4b). They were serialised
# only by accident -- the single-threaded push-to-talk loop happened never to overlap two calls.
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


def _post(cfg, payload, timeout):
    """POST one /api/generate request under model_lock and return the parsed envelope dict.
    keep_alive is injected here so every path benefits. The lock spans only the HTTP call --
    that is exactly the window the server holds the model busy."""
    body = dict(payload)
    body.setdefault("stream", False)
    ka = _keep_alive()
    if ka is not None:
        body["keep_alive"] = ka
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg["ollama_url"], data=data, headers={"Content-Type": "application/json"})
    with model_lock:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())


def ollama_generate(cfg, payload, timeout):
    """Run a generate call and return the model's parsed JSON OBJECT.

    Both callers (text intent + vision) want a dict and both unwrap Ollama's {"response": "..."}
    envelope identically, so that lives here. Raises ValueError on an empty response or a non-dict
    body -- callers treat the raise as fail-safe (main.py -> STOP; vision.py -> 'not visible')."""
    envelope = _post(cfg, payload, timeout)
    response = envelope.get("response")
    if not response:
        raise ValueError(f"ollama returned no response (raw: {str(envelope)[:200]!r})")
    obj = json.loads(response)
    if not isinstance(obj, dict):
        # JSON mode guarantees valid JSON, not a JSON *object* -- guard so a list/string can't
        # crash the caller later at obj.get(...).
        raise ValueError(f"model did not return a JSON object: {response[:200]!r}")
    return obj


def warm_up(cfg, timeout):
    """Best-effort boot-time priming generate so the model is resident before the first command.
    Sends a 1-token throwaway prompt (no JSON format) under model_lock + keep_alive. Never raises:
    a failed warmup (e.g. Ollama not up yet, or a laptop with no server) just falls back to the
    old lazy cold-load behaviour. Returns True on success."""
    try:
        _post(cfg, {"model": cfg["model"], "prompt": "ok",
                    "options": {"num_predict": 1}}, timeout)
        return True
    except Exception as e:
        log.warning("model warmup failed (%s) -- first command may cold-load", e)
        return False
