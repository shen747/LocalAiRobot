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
import socket
import threading
import time
import urllib.error
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


def _log_http_error(e, backend, model=None):
    """Log the BODY of a model-backend HTTP error. The status line alone ('HTTP Error 403: Forbidden')
    hides WHY -- Google/Ollama put the real reason (API_KEY_INVALID, PERMISSION_DENIED, 'API not enabled
    for this project', an unknown-model 404, a 429 quota message) in the RESPONSE BODY, which urllib drops
    from str(e). Read it once, truncate, and log at ERROR so the DEFAULT run log pinpoints it; add a short
    actionable hint for the usual culprits. Never raises -- diagnostics must not mask the original error."""
    detail = ""
    try:
        detail = e.read().decode("utf-8", "replace").strip()
    except Exception:
        pass
    code = getattr(e, "code", None)
    hint = {
        400: "bad request -- often API_KEY_INVALID (a malformed/non-Gemini key) or a rejected field.",
        401: "auth rejected -- ROBOT_GEMINI_API_KEY isn't a valid Gemini API key (AI Studio -> Get API key).",
        403: "forbidden -- the key is invalid/restricted, the Generative Language API isn't enabled for its "
             "project, or this key can't use this model. Verify the key and its model access.",
        404: f"model not found -- check the model name ({model!r}) and ROBOT_GEMINI_URL.",
        429: "rate/quota limit -- slow down, raise the tier, or switch to a lighter model.",
    }.get(code, "")
    where = f"{backend} model {model!r}" if model else backend
    log.error("%s HTTP %s %s -- response body: %s%s", where, code, getattr(e, "reason", ""),
              detail[:800] or "(empty body)", f"  HINT: {hint}" if hint else "")


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
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            _log_http_error(e, "ollama", body.get("model"))
            raise


def _lenient_object(text):
    """Recover a dict from the almost-JSON models emit even in JSON mode: a truncated tail (missing a closing
    quote/brace when the reply hit a token / thinking-budget limit -- e.g. gemini-*-flash returning
    `{"action":"LOOK","reply":"..."` with no closing brace), a stray quote or other junk between a value and
    the next ',' / '}' (e.g. `..."reply":"...baby."\\n"\\n}`), or a trailing comma. Returns a dict, or None if
    there is no object-looking span. Never fabricates values: a value that was cut off is kept as far as it
    parsed, or dropped if it never started. This is the LAST resort, after strict/fence/outermost-span fail."""
    n = len(text)
    start = text.find("{")
    if start < 0:
        return None

    def skip_ws(p):
        while p < n and text[p] in " \t\r\n":
            p += 1
        return p

    def read_string(p):
        p += 1  # consume opening quote
        out = []
        while p < n:
            c = text[p]
            if c == "\\":
                if p + 1 >= n:
                    p += 1
                    break
                nx = text[p + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                            '"': '"', "\\": "\\", "/": "/"}.get(nx, nx))
                p += 2
                continue
            if c == '"':
                return "".join(out), p + 1
            out.append(c)
            p += 1
        return "".join(out), p  # truncated string (EOF before the closing quote)

    def read_value(p):
        p = skip_ws(p)
        if p >= n:
            return None, p, False
        c = text[p]
        if c == '"':
            v, p = read_string(p)
            return v, p, True
        if c == '{':
            v, p = read_object(p)
            return v, p, True
        if c == '[':
            v, p = read_array(p)
            return v, p, True
        j = p                                      # scalar: number / true / false / null / bare word
        while j < n and text[j] not in ',}]"':
            j += 1
        tok = text[p:j].strip()
        if not tok:
            return None, j, False
        low = tok.lower()
        if low == "true":
            return True, j, True
        if low == "false":
            return False, j, True
        if low in ("null", "none"):
            return None, j, True
        try:
            return json.loads(tok), j, True        # a number
        except Exception:
            return tok, j, True                    # keep the bare token as a string

    def read_array(p):
        arr = []
        p += 1  # consume '['
        while True:
            p = skip_ws(p)
            if p >= n or text[p] == ']':
                return arr, (p + 1 if p < n else p)
            v, p, ok = read_value(p)
            if ok:
                arr.append(v)
            p = skip_ws(p)
            while p < n and text[p] not in ',]':   # resync past stray junk
                p += 1
            if p < n and text[p] == ',':
                p += 1
            elif p < n and text[p] == ']':
                return arr, p + 1
            if p >= n:
                return arr, p

    def read_object(p):
        obj = {}
        p += 1  # consume '{'
        while True:
            p = skip_ws(p)
            if p >= n or text[p] == '}':
                return obj, (p + 1 if p < n else p)
            if text[p] != '"':                     # junk where a key should be -> skip to a key or the end
                while p < n and text[p] not in '"}':
                    p += 1
                continue
            key, p = read_string(p)
            p = skip_ws(p)
            if p < n and text[p] == ':':
                p += 1
            v, p, ok = read_value(p)
            if ok:
                obj[key] = v
            p = skip_ws(p)
            while p < n and text[p] not in ',}':   # resync -- this is what eats a stray '"' after a value
                p += 1
            if p < n and text[p] == ',':
                p += 1
            elif p < n and text[p] == '}':
                return obj, p + 1
            if p >= n:
                return obj, p

    obj, _ = read_object(start)
    return obj if isinstance(obj, dict) else None


def _loads_lenient(text):
    """Parse a model's JSON reply, tolerating the markdown fences / stray prose some models add EVEN in
    JSON mode (the classic cause of 'Expecting value: line 1 column 1 (char 0)' -- e.g. Gemini vision
    wrapping the object in ```json ... ```). Tries strict first, then a fenced block, then the outermost
    {...} span, then a tolerant reader (_lenient_object) that recovers truncated / stray-quote / trailing-comma
    near-JSON. Raises ValueError WITH a snippet if none parse, so the log shows what actually came back."""
    s = (text or "").strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    if s.startswith("```"):                          # ```json ... ```  or  ``` ... ```
        inner = s.strip("`")
        nl = inner.find("\n")
        if nl != -1 and inner[:nl].strip().lower() in ("json", ""):
            inner = inner[nl + 1:]
        try:
            return json.loads(inner.strip().rstrip("`").strip())
        except Exception:
            pass
    i, j = s.find("{"), s.rfind("}")                 # outermost object, ignoring any surrounding prose
    if 0 <= i < j:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    recovered = _lenient_object(s)                    # last resort: truncated / stray-quote / trailing-comma
    if recovered:                                     # non-empty dict only; {} or None falls through to raise
        return recovered
    raise ValueError(f"model did not return parseable JSON (len={len(s)}): {s[:500]!r}")


def _llm_retries():
    """Retries for a TRANSIENT model network failure (read timeout / dropped connection). Default 1 -- a
    stalled request gets ONE fresh-connection retry; 0 disables. Total wall-clock stays within
    ROBOT_LLM_TIMEOUT_S because the budget is split across attempts (see _generate_with_retries)."""
    try:
        return max(0, int(os.environ.get("ROBOT_LLM_RETRIES", "1") or 1))
    except ValueError:
        return 1


def _generate_with_retries(cfg, payload, timeout):
    """Fetch the raw model response string, retrying a TRANSIENT network failure on a FRESH connection.
    The field 'The read operation timed out' is a stalled TCP read that never delivers data within the
    timeout; a second, fresh attempt usually returns fast. The timeout is an ABSOLUTE deadline: each
    attempt is derived from the REMAINING budget (targeting ~20 s per attempt only where the budget
    allows), so the TOTAL wall-clock never exceeds ROBOT_LLM_TIMEOUT_S -- the point is to avoid the long
    dead-air, not to wait even longer. Content errors (_gemini_generate's ValueError for a blocked/empty
    answer) are NOT network errors, so they propagate immediately without a retry."""
    attempts = max(1, _llm_retries() + 1)
    deadline = time.monotonic() + timeout
    is_gemini = cfg.get("ai_backend") == "gemini"
    for i in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"llm budget of {timeout}s exhausted after {i} attempt(s)")
        per_attempt = min(remaining, max(remaining / (attempts - i), 20.0))
        try:
            if is_gemini:
                return _gemini_generate(cfg, payload, per_attempt)
            return _post(cfg, payload, per_attempt).get("response")
        except urllib.error.HTTPError:
            raise                      # a real HTTP status (404 model / 400 bad-request / 429) -- surface it (its body is the useful error), don't retry
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError) as e:
            if i + 1 >= attempts:
                raise
            log.warning("llm network attempt %d/%d failed (%s) -- retrying on a fresh connection",
                        i + 1, attempts, e)
    raise TimeoutError(f"llm budget of {timeout}s exhausted after {attempts} attempt(s)")  # defensive


def text_backend(cfg):
    """Which backend serves TEXT (conversation + intent) for this cfg -- 'gemini' (remote) or 'local'.
    Product rule: everything past the greeting is handed to the remote AI UNLESS the rig is fully local. So
    BOTH 'gemini' and 'mixed' route text to Gemini WHEN a ROBOT_GEMINI_API_KEY is present; without a key (or
    backend 'local') text stays on the local model. VISION resolves separately (vision.vision_backend), which
    is why 'mixed' can send vision remote while, historically, text stayed local -- that split is now gone for
    text: a key present means conversation goes remote in both remote-capable modes."""
    ab = (cfg.get("ai_backend") or "local").strip().lower()
    have_key = bool((cfg.get("gemini_api_key") or "").strip())
    if ab in ("gemini", "mixed") and have_key:
        return "gemini"
    return "local"


def ollama_generate(cfg, payload, timeout):
    """Run a generate call and return the model's parsed JSON OBJECT.

    Dispatches by backend (cfg['ai_backend'], from ROBOT_AI_BACKEND): 'gemini' -> Google Gemini
    (remote), anything else -> local Ollama (default). Both backends yield the model's response as
    a JSON *string*; the shared tail parses (leniently -- see _loads_lenient) + validates it to a dict,
    so the two callers (text intent + vision) stay backend-agnostic. A transient network failure is retried
    on a fresh connection (see _generate_with_retries). Raises ValueError on an empty/blocked response or a
    non-dict body -- callers treat the raise as fail-safe (main.py -> STOP; vision.py -> 'not visible')."""
    response = _generate_with_retries(cfg, payload, timeout)
    if not response:
        raise ValueError(f"model returned no response (backend={cfg.get('ai_backend') or 'local'})")
    obj = _loads_lenient(response)
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


def _gemini_thinking_budget(is_vision):
    """Gemini 'thinking budget'. Thinking models (Gemini Robotics-ER, the *-flash models) reason BEFORE
    answering, which adds latency and can burn the token budget so the structured JSON truncates. For the
    robot's schema-constrained vision we want fast, deterministic replies, so the VISION path DEFAULTS to 0
    (thinking off) -- the value the Robotics-ER docs recommend. Text keeps the model's own default.
      ROBOT_GEMINI_VISION_THINKING_BUDGET / ROBOT_GEMINI_THINKING_BUDGET: int (0 = off, -1 = dynamic/model
      decides, N = fixed budget); a non-integer (e.g. 'auto') OMITS the field for a model that rejects it."""
    key = "ROBOT_GEMINI_VISION_THINKING_BUDGET" if is_vision else "ROBOT_GEMINI_THINKING_BUDGET"
    raw = (os.environ.get(key) or "").strip()
    if raw == "":
        return 0 if is_vision else None      # vision -> off (fast/no truncation); text -> model default
    try:
        return int(raw)
    except ValueError:
        return None                          # 'auto'/'default'/etc -> don't send thinkingConfig at all


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
        # Local num_predict (e.g. 220) is tuned for the small LOCAL models and is FAR too low for Gemini
        # 'thinking' models (e.g. gemini-*-flash): the reasoning tokens burn that whole budget and the JSON
        # gets truncated to a preamble ("Here is the JSON..."). Floor it high so the structured reply always
        # has room to finish thinking AND emit the object.
        gen["maxOutputTokens"] = max(int(opts["num_predict"]), 8192)
    fmt = payload.get("format")
    if isinstance(fmt, dict):            # vision passes a JSON schema; text passes "json" (skipped)
        schema = _to_gemini_schema(fmt)
        if schema:
            gen["responseSchema"] = schema
    tb = _gemini_thinking_budget(is_vision)   # vision defaults to thinking OFF (Robotics-ER: fast, no truncation)
    if tb is not None:
        gen["thinkingConfig"] = {"thinkingBudget": tb}

    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": gen}).encode("utf-8")
    base = (cfg.get("gemini_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    req = urllib.request.Request(
        f"{base}/models/{model}:generateContent", data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    with model_lock:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                envelope = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            _log_http_error(e, "gemini", model)
            raise
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
    cold-load, so it's a no-op success there.

    RETRIES, because a single attempt loses the race on EVERY boot. Under systemd the brain starts within
    seconds of ollama.service, and ollama accepts the connection before its llama-server child is ready --
    so the warmup got a hard `HTTP 500 {"error":"timed out waiting for llama-server to start"}` and gave up,
    leaving the model cold for the visitor who actually needed it. (Observed on all three boots in a
    four-day field log.) So: re-attempt with a short backoff, for up to ROBOT_WARMUP_RETRY_S seconds."""
    if cfg.get("ai_backend") == "gemini":
        return True
    try:
        budget = max(0.0, float(os.environ.get("ROBOT_WARMUP_RETRY_S", "") or 90))
    except ValueError:
        budget = 90.0
    deadline = time.monotonic() + budget
    attempt, last = 0, None
    while True:
        attempt += 1
        try:
            _post(cfg, {"model": cfg["model"], "prompt": "ok",
                        "options": {"num_predict": 1}}, timeout)
            if attempt > 1:
                log.info("model warmup succeeded on attempt %d -- model resident", attempt)
            return True
        except Exception as e:
            last = e
            if time.monotonic() >= deadline:
                break
            log.info("model warmup attempt %d failed (%s) -- the model server is probably still starting; "
                     "retrying", attempt, e)
            time.sleep(5.0)
    log.warning("model warmup failed after %d attempt(s) over %.0fs (%s) -- first command may cold-load",
                attempt, budget, last)
    return False
