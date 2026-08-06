#!/usr/bin/env python3
# brain/main.py -- Local AI robot orchestrator (Raspberry Pi 5 "brain").
#
# Wake word (or ENTER in --dry) -> whisper.cpp (STT) -> Ollama (ROBOT_MODEL -- Gemma 4 intent JSON; robot.env
# ships gemma4:e2b-it-qat, code fallback gemma4:e4b-it-qat) -> Piper (TTS),
# driving an RC crawler (ESC (Electronic Speed Controller) throttle + steering servo) through brain/driver.py. It can also
# SEE (Pi Camera V2 NoIR -> a dedicated vision model, ROBOT_VISION_MODEL), react with an expressive FACE
# (USB-C screen), sense in three directions (front/left/right HC-SR04P), remember what it has
# seen (SQLite, fed back into the prompt), and run a bounded autonomous explore/find behaviour.
#
# Design carried over from the v1 review:
#   - No shell: Piper gets the reply text via stdin (an LLM reply can't inject commands).
#   - Half-duplex audio: a single lock means the mic is never open while the speaker talks.
#   - Timeouts on every subprocess + the Ollama HTTP call, so nothing can wedge the robot.
#   - The Pi whitelists actions and clamps speed/duration; unknown -> STOP (fail-safe).
#   - Motion is handed to driver.py, whose control thread enforces the software failsafe.
#   - AUTONOMY IS OPT-IN: EXPLORE/FIND only run if an explore phrase is in the transcript
#     (the trigger gate), checked on the raw text -- never off an ordinary DRIVE command.

import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import behavior
import captures
import concierge
import halloween
import inference
import remote
import vision
from camera import Camera
from driver import RobotDriver, VALID_DRIVE, VALID_STEER
from face import Face
from face_id import FaceID
from fan import make_fan
from memory import Memory
from pir import MotionWake
from sounds import Sounds
from wakeword import WakeWord

log = logging.getLogger("robot.main")


def env(key, default):
    v = os.environ.get(key)
    return v if v else default


def load_config():
    home = os.path.expanduser("~")
    return {
        # Brain backend: 'local' = Ollama on the Pi (default, offline) | 'gemini' = remote Google
        # Gemini. The switch is read in inference.py; both text-intent and vision follow it. STT
        # (whisper), TTS (piper), sensors, motors and the wake word are always local.
        "ai_backend": env("ROBOT_AI_BACKEND", "local").strip().lower(),
        "gemini_api_key": env("ROBOT_GEMINI_API_KEY", ""),
        "gemini_model": env("ROBOT_GEMINI_MODEL", "gemini-2.5-flash"),
        "gemini_vision_model": env("ROBOT_GEMINI_VISION_MODEL", ""),
        "gemini_url": env("ROBOT_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta"),
        "ollama_url": env("ROBOT_OLLAMA_URL", "http://localhost:11434/api/generate"),
        "model": env("ROBOT_MODEL", "gemma4:e4b-it-qat"),
        "whisper_bin": env("ROBOT_WHISPER_BIN", os.path.join(home, "whisper.cpp/build/bin/whisper-cli")),
        "whisper_model": env("ROBOT_WHISPER_MODEL", os.path.join(home, "whisper.cpp/models/ggml-base.en.bin")),
        "piper_bin": env("ROBOT_PIPER_BIN", os.path.join(home, "piper/piper/piper")),
        "piper_voice": env("ROBOT_PIPER_VOICE", os.path.join(home, "piper/en_US-lessac-medium.onnx")),
        "mic": env("ROBOT_MIC", "plughw:1,0"),
        "record_secs": env("ROBOT_RECORD_SECS", "4"),
        "tmpdir": env("ROBOT_TMPDIR", "/dev/shm"),
        # Optional dedicated vision model (e.g. moondream). Empty -> vision shares cfg["model"]
        # (slower, and only if that model can see). Keep moondream set. See vision.py.
        "vision_model": env("ROBOT_VISION_MODEL", ""),
        # The robot's own name/identity (default Jarvis). It identifies as this in speech + the LLM persona,
        # introduces itself on first run, and stores it as top-priority RAG memory. NOT the wake word -- that
        # is a fixed openWakeWord model ('hey jarvis' unless you train/point ROBOT_WAKEWORD_MODEL elsewhere).
        "robot_name": (env("ROBOT_NAME", "Jarvis").strip() or "Jarvis"),
    }


# Common whisper hallucinations on silence -- ignore these outright (they're real words, not sound tags).
WHISPER_NOISE = {
    "", ".", "you", "bye.", "thank you.",
    "thanks for watching.", "thank you for watching.",
}

# whisper writes NON-SPEECH events as bracketed/starred tags: "(machine whirring)", "[BLANK_AUDIO]",
# "*buzzing*", "(clicking)". Matching ONE tag lets us strip them all and see if any real words remain --
# so we don't have to hardcode every ambient sound (the Pi's own fans alone produce many variants).
_NOISE_TAG = re.compile(r"[\(\[\*][^\)\]\*]*[\)\]\*]")


def is_noise(text):
    """True if the transcript is silence, a known whisper hallucination, or ONLY non-speech sound tags
    (e.g. '(machine whirring)' from the Pi's own cooling fans). Such a transcript must NOT burn an LLM
    call, and (mid-conversation) must NOT reset the silence timer -- else the fan noise keeps it awake."""
    t = text.strip().lower()
    if t in WHISPER_NOISE:
        return True
    # Strip every bracketed sound tag; if only punctuation/space is left, it's pure noise (no real words).
    remainder = _NOISE_TAG.sub("", t).strip(" .,!?-–—…\"'")
    return len(remainder) < 2


# --- Autonomy trigger gate: EXPLORE/FIND only fire if the transcript contains one of these. ---
DEFAULT_EXPLORE_TRIGGERS = ("explore,look around,have a look,take a look,find,look for,"
                            "search for,where is,go to,go towards,move towards,move toward,head towards")


def explore_triggers():
    raw = env("ROBOT_EXPLORE_TRIGGERS", DEFAULT_EXPLORE_TRIGGERS)
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def is_explore_command(text):
    low = text.lower()
    return any(trig in low for trig in explore_triggers())


# --- REMEMBER gate: only photograph + store when the user actually said a "teach me" phrase, so a
# misclassified command can't trigger a stray capture + slow VLM describe (mirrors the explore gate). ---
REMEMBER_TRIGGERS = ("remember", "this is", "these are", "meet ", "name is",
                     "call this", "call him", "call her", "say hello to")


def is_remember_command(text):
    low = text.lower()
    return any(t in low for t in REMEMBER_TRIGGERS)


# --- LOOK gate: a live "what do you see / what is this" request captures a FRESH camera frame and describes
# what's visible NOW, instead of answering from stored scene memory. Promotes SAY -> LOOK on these phrases. ---
DEFAULT_LOOK_TRIGGERS = ("what is this,what's this,what is that,what's that,what do you see,what can you "
                         "see,what are you looking at,what am i holding,look at this,look at that,what is in "
                         "front,what's in front,describe what you see,can you see")


def look_triggers():
    raw = env("ROBOT_LOOK_TRIGGERS", DEFAULT_LOOK_TRIGGERS)
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def is_look_command(text):
    low = text.lower()
    return any(t in low for t in look_triggers())


# Crawl by default: a normal move ("come forward", "follow me") stays SLOW. Speed only climbs when the
# user EXPLICITLY asks for it -- detected on the RAW transcript, so the model can't decide to floor it on
# its own. The crawl cap is ROBOT_DRIVE_CRAWL_SPEED (config). Any of these words lets speed climb to 100:
FAST_WORDS = ("fast", "faster", "quick", "quickly", "hurry", "speed up", "full speed",
              "full throttle", "top speed", "floor it", "step on it", "zoom", "race", "sprint")


def wants_speed(text):
    return any(w in text.lower() for w in FAST_WORDS)


# audio_lock makes the physical audio path half-duplex: at most one of {record, playback}
# at a time. Kills the self-listening feedback loop AND concurrent-aplay garbling.
audio_lock = threading.Lock()


def _last_line(s):
    """The last non-empty line of captured stderr -- the bit that actually names the failure
    (e.g. ALSA's 'audio open error: No such file or directory'), for clear logs."""
    s = (s or "").strip()
    return s.splitlines()[-1].strip() if s else "(no stderr)"


def listen_and_transcribe(cfg):
    wav = os.path.join(cfg["tmpdir"], "command.wav")
    t0 = time.monotonic()
    rec_timeout = _as_int(cfg["record_secs"], 4) + 10   # never shorter than the requested capture
    with audio_lock:
        # Capture arecord's stderr instead of /dev/null: when the mic device is missing/busy/renamed
        # we want ALSA's actual reason in the log, not a bare 'exit status 1'.
        rec = subprocess.run(
            ["arecord", "-D", cfg["mic"], "-d", cfg["record_secs"],
             "-f", "S16_LE", "-r", "16000", "-c", "1", wav],
            timeout=rec_timeout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    if rec.returncode != 0:
        raise RuntimeError(f"arecord failed on ROBOT_MIC={cfg['mic']!r} (exit {rec.returncode}): "
                           f"{_last_line(rec.stderr)}  -- check `arecord -l` (card renamed/unplugged?)")
    rec_s = time.monotonic() - t0
    # whisper.cpp cost is per 30-s window and scales with ROBOT_WHISPER_MODEL size, not capture length;
    # a 120 s floor covers small/medium ggml models on a Pi 5 (base.en stays well under it).
    stt_timeout = max(120, _as_int(cfg["record_secs"], 4) * 4)
    out = subprocess.run(
        [cfg["whisper_bin"], "-m", cfg["whisper_model"], "-f", wav, "-nt"],
        timeout=stt_timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"whisper failed (exit {out.returncode}): {_last_line(out.stderr)}")
    text = out.stdout.strip()
    log.info("STT: %.1fs record + %.1fs transcribe -> %r", rec_s, time.monotonic() - t0 - rec_s, text[:60])
    return text


def wait_for_trigger(cfg, wake, motion_event=None):
    """Block until the operator starts a turn. Returns 'WAKE' | 'MOTION' | 'QUIT' | 'FAILED' | ...

    LIVE triggers on the wake word (+ 'q' to quit over SSH; headless needs no stdin). In Concierge Mode a PIR
    motion sensor is an ADDITIONAL trigger: its callback sets motion_event and cancels the in-progress listen,
    so a 'CANCELLED' return WITH motion_event set is reported as 'MOTION'. In --dry, ENTER starts a turn and
    'r' simulates a wake. The trigger ONLY starts capture -- the EXPLORE/FIND autonomy gate is separate."""
    # Check the flag BEFORE listening. Motion is normally noticed as a cancelled listen, but cancel_listen()
    # only works on a listen that is ALREADY RUNNING: WakeWord._listen() clears the cancel flag on entry, so
    # an edge that arrived while the robot was busy (mid-greeting, recording, transcribing, playing the
    # goodbye) had its cancel thrown away and the visitor was dropped on the floor. The Event survives that
    # race, so honouring it here is what makes "someone walked up while you were talking" work at all.
    if motion_event is not None and motion_event.is_set():
        motion_event.clear()
        return "MOTION"
    result = wake.wait(audio_lock)
    if motion_event is not None and motion_event.is_set():
        # Always CLEAR it, whatever the listener returned. A flag left set is how a stale motion used to turn
        # the next unrelated cancel (the voice-abort listener) into a phantom MOTION. It is only reported as
        # motion when the listen was CANCELLED -- any other return is a real trigger of its own (the operator
        # spoke, or we're quitting) and outranks a wave at the sensor.
        motion_event.clear()
        if result == "CANCELLED":
            return "MOTION"
    return result


# The intent/JSON contract is shared by both personas; only the ONE identity sentence at the front differs,
# so Concierge Mode can describe itself as a stationary greeter (not a car) without touching the crawler
# prompt. _CRAWLER_INTRO + _ACTION_SCHEMA reproduces the original SYSTEM_PROMPT byte-for-byte.
_CRAWLER_INTRO = (
    "You are the brain of a remote-control crawler car with Ackermann steering "
    "(one drive motor plus one steering servo -- it CANNOT strafe or spin in place). "
    "It has a forward camera and can look around. ")
_CONCIERGE_INTRO = (
    "You are the brain of a STATIONARY AI concierge / assistant -- it does NOT move, drive, or go "
    "anywhere (it has no wheels); it greets visitors and chats. It has a forward camera and can look around. ")
_ACTION_SCHEMA = (
    "Convert the user's spoken input into "
    "ONE JSON object and nothing else.\n"
    "Keys:\n"
    '- "action": one of DRIVE, EXPLORE, FIND, STOP, SAY, REMEMBER, LOOK.\n'
    "    DRIVE = ordinary movement (go / turn / reverse / stop).\n"
    "    EXPLORE = only if the user asks to look around or explore.\n"
    "    FIND = only if the user asks to find, go to, or move towards a named object.\n"
    "    STOP = stop moving now.\n"
    "    REMEMBER = the user is naming a person or thing for you to remember (e.g. 'this is my "
    "wife Shani', 'remember this is the kitchen'). The car will photograph what its camera sees "
    "and store it. Do NOT move.\n"
    "    LOOK = the user asks what you SEE right now, what something IS, or to look at something they are "
    "showing you (e.g. 'what is this?', 'what do you see?', 'look at this'). Capture the camera and describe "
    "what is CURRENTLY visible -- answer from the LIVE view, NOT from memory. Do NOT move.\n"
    "    SAY = anything else that is NOT a movement, look, or remember request -- a question, a "
    "greeting, chit-chat, or something the car cannot physically do. Answer in words; do NOT move.\n"
    '- "target": for FIND, the object to look for (e.g. "sofa"); for REMEMBER, the NAME to store '
    '(e.g. "Shani"); otherwise omit.\n'
    '- "note": for REMEMBER, the relationship or description the user gave (e.g. "my wife"); otherwise omit.\n'
    '- "drive": EXACTLY one of FORWARD, REVERSE, STOP (used when action is DRIVE).\n'
    '- "steer": EXACTLY one of LEFT, CENTER, RIGHT.\n'
    '- "speed": integer 0-100. Use a SLOW crawl (about 10-15) for normal moves like "come forward" or '
    '"follow me"; use a HIGH speed (60-100) ONLY when the user explicitly asks to go fast/quick/hurry.\n'
    '- "duration_ms": integer 300-5000 (how long to move; use 0 only for STOP).\n'
    '- "reply": the spoken response. For SAY, a natural, helpful answer (max ~30 words); '
    "for movement, a brief confirmation (max 12 words). Plain text only.\n"
    "To turn, set steer LEFT or RIGHT together with drive FORWARD (the car must be moving to "
    "steer). Choose LOOK when the user asks about what you currently see; DRIVE / EXPLORE / FIND when "
    "they want the car to move or explore; when in doubt, choose SAY and just answer.\n"
)
SYSTEM_PROMPT = _CRAWLER_INTRO + _ACTION_SCHEMA   # crawler-mode prompt (identical to the original)


def _llm_timeout():
    try:
        return float(os.environ.get("ROBOT_LLM_TIMEOUT_S", "120") or 120)
    except ValueError:
        return 120.0


def query_ollama(cfg, user_text, context=""):
    name = cfg.get("robot_name") or "Jarvis"
    # Identity preamble so the robot answers as its configured name -- and, in Concierge Mode, as a STATIONARY
    # greeter rather than a crawler car (otherwise it introduces itself as a "Crawler Car"). The crawler branch
    # is byte-identical to before, so crawler mode is unchanged. (context, from mem.prompt_context(), already
    # leads with the stored top-priority identity -- who it is + what it can do.)
    if concierge.enabled():
        preamble = (f"You are {name} the Concierge, a friendly STATIONARY assistant. If the user asks who "
                    f"you are, your name, or what you can do, answer as {name} the Concierge -- a stationary "
                    f"greeter that recognises visitors and chats; you never move, drive, or go anywhere.\n")
        prompt = preamble + _CONCIERGE_INTRO + _ACTION_SCHEMA
    else:
        preamble = (f"You are {name}, a friendly voice-controlled RC crawler robot car. If the user asks who you "
                    f"are, your name, or what you can do, answer as {name}.\n")
        prompt = preamble + SYSTEM_PROMPT
    if context:
        prompt += "Known surroundings and memory: " + context + "\n"
    prompt += "User command: " + user_text
    # Routed through inference.ollama_generate: one shared model_lock serialises this with the vision call (so
    # they never hit Ollama concurrently), and keep_alive keeps the model warm. It unwraps the {"response": ...}
    # envelope and validates a dict; a raise is fail-safe here.
    #
    # TEXT backend (product rule: conversation past the greeting goes REMOTE unless the rig is fully local):
    # inference.text_backend() sends BOTH gemini and mixed to Gemini when a key is present, else local. Under
    # 'mixed' a remote failure falls back to the LOCAL model for THIS turn (like the vision path); pure gemini
    # surfaces the error (caller fail-safes).
    payload = {"model": cfg["model"], "prompt": prompt, "format": "json"}
    ab = (cfg.get("ai_backend") or "local").strip().lower()
    if inference.text_backend(cfg) == "gemini":
        gcfg = {**cfg, "ai_backend": "gemini"}
        try:
            return inference.ollama_generate(gcfg, payload, _llm_timeout())
        except Exception as e:
            if ab != "mixed":
                raise
            log.warning("mixed: remote text unavailable (%s) -> falling back to the LOCAL model", e)
    lcfg = {**cfg, "ai_backend": "local"}
    return inference.ollama_generate(lcfg, payload, _llm_timeout())


# --- TTS pronunciation fixups -----------------------------------------------------------------------
# Piper mis-says some names (it reads "Ramesh" as "RAM-esh"). We respell them phonetically JUST before
# synthesis, so EVERY spoken string -- LLM replies, concierge greetings/refusals, spoken errors -- comes out
# right without polluting the text the LLM sees (or the on-screen face caption, which speaks the same text).
# Matches are whole-word and case-insensitive. Add/override entries via config/robot.env:
#   ROBOT_TTS_PRONUNCIATIONS="Ramesh=Ruh-mesh; Shani=Shah-nee"
# NOTE: use a SINGLE joined respelling ("Ruh-mesh"), NOT space-separated letters ("R uh m ai sh") -- Piper's
# espeak phonemizer reads spaced single letters as the ALPHABET (it says "R-A-M-E-S-H").
_PRONUNCIATIONS = {"Ramesh": "Ruh-mesh"}
_pron_re = None    # compiled lazily from _PRONUNCIATIONS (+ env) on first _fix_pronunciation() call
_pron_lut = None   # lowercased key -> respelling, for the case-insensitive substitution


def _fix_pronunciation(text):
    """Respell known-mispronounced names phonetically for Piper. Whole-word, case-insensitive. Returns the
    text unchanged if nothing matches. Overridable/extendable via ROBOT_TTS_PRONUNCIATIONS ('Name=respell;...')."""
    global _pron_re, _pron_lut
    if _pron_re is None:
        table = dict(_PRONUNCIATIONS)
        for pair in os.environ.get("ROBOT_TTS_PRONUNCIATIONS", "").split(";"):
            key, sep, val = pair.partition("=")
            if sep and key.strip():
                table[key.strip()] = val.strip()
        _pron_lut = {k.lower(): v for k, v in table.items() if k.strip()}
        # A never-matching pattern when the table is empty, so the sub below is a cheap no-op.
        _pron_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in _pron_lut) + r")\b",
                              re.IGNORECASE) if _pron_lut else re.compile(r"(?!)")
    if not _pron_lut:
        return text
    return _pron_re.sub(lambda m: _pron_lut[m.group(0).lower()], text)


def _talk_delay_ms():
    """ms to delay the 'talking' face AFTER playback starts (ROBOT_FACE_TALK_DELAY_MS, default 0). Nudge it up
    (e.g. 150) if the DSI framebuffer face leads the first audible sample; 0 = start with aplay."""
    return max(0, _as_int(os.environ.get("ROBOT_FACE_TALK_DELAY_MS"), 0))


def speak(cfg, text, on_play=None):
    """Synthesize `text` with Piper and play it. `on_play` (optional) is fired IN STEP with playback -- callers
    use it to start the 'talking' face exactly when sound begins, not before the (multi-second) synthesis."""
    text = _fix_pronunciation((text or "").strip())
    if not text:
        return
    wav = os.path.join(cfg["tmpdir"], f"reply-{os.getpid()}-{int(time.time() * 1000)}.wav")
    t0 = time.monotonic()
    talk_timer = None
    try:
        # Synthesize: pipe text via stdin (no shell -> no injection). Capture stderr so a Piper/voice
        # failure is logged with its reason, not silently swallowed.
        syn = subprocess.run(
            [cfg["piper_bin"], "-m", cfg["piper_voice"], "--output_file", wav],
            input=text.encode("utf-8"), timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if syn.returncode != 0:
            raise RuntimeError(f"piper exit {syn.returncode}: {_last_line(syn.stderr.decode('utf-8', 'replace'))}")
        # Play back under the audio lock (serialized, never overlaps recording). aplay errors
        # (wrong/busy speaker, bad ~/.asoundrc) are the usual 'no voice' culprit -- surface them.
        with audio_lock:
            # Kick the 'talking' face HERE (playback), not before synthesis above -- a long reply takes Piper
            # seconds, which used to animate the mouth silently well ahead of any sound. A timer (vs an inline
            # call) lets ROBOT_FACE_TALK_DELAY_MS push it a touch past the first sample if the DSI face leads.
            if on_play:
                talk_timer = threading.Timer(_talk_delay_ms() / 1000.0, on_play)
                talk_timer.start()
            pb = subprocess.run(["aplay", wav], timeout=30,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if pb.returncode != 0:
            raise RuntimeError(f"aplay exit {pb.returncode}: {_last_line(pb.stderr)}  -- check ~/.asoundrc / `aplay -l`")
        log.info("TTS: spoke %d chars in %.1fs", len(text), time.monotonic() - t0)
    except Exception as e:
        print(f"tts: {e}", file=sys.stderr)
        log.warning("TTS failed: %s", e)
    finally:
        if talk_timer is not None:
            talk_timer.cancel()   # a tuned delay outlasting very short audio: don't flip to 'talking' after it ends
        try:
            os.remove(wav)
        except OSError:
            pass


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _short_error(e):
    """A short, speakable one-liner from an exception, for the spoken error message. Collapses
    whitespace/newlines and truncates so Piper doesn't read a giant technical string aloud."""
    msg = " ".join(str(e).split())
    if len(msg) > 140:
        msg = msg[:140].rstrip() + "…"
    return msg or e.__class__.__name__


def _persist_env(key, value):
    """Write KEY=value into config/robot.env in place (preserving any trailing '# comment') or append it,
    like scripts/setup_audio.sh's set_kv. Used to save an auto-generated ROBOT_HTTP_TOKEN. Best-effort."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "robot.env")
    try:
        lines = []
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
        hit = False
        for i, ln in enumerate(lines):
            m = re.match(rf"^({re.escape(key)}=)([^\s#]*)(.*)$", ln)
            if m:
                lines[i] = f"{m.group(1)}{value}{m.group(3)}\n"
                hit = True
                break
        if not hit:
            lines.append(f"{key}={value}\n")
        with open(path, "w") as f:
            f.writelines(lines)
    except Exception as e:
        log.warning("could not persist %s to config/robot.env: %s", key, e)


# vcgencmd get_throttled bit meanings (Pi firmware). Low bits = happening NOW; bit+16 = since boot.
_POWER_BITS = {
    0: "UNDERVOLTAGE NOW", 1: "arm freq capped NOW", 2: "throttled NOW", 3: "soft temp limit NOW",
    16: "undervoltage since boot", 17: "freq-cap since boot", 18: "throttled since boot",
    19: "soft temp limit since boot",
}


def _start_power_monitor():
    """Log Pi POWER trouble (undervoltage/throttling) so battery brownouts are visible in the run log.
    On battery (LiPo -> UBEC -> UPS/USB-C, no USB-PD negotiation) a sagging 5V rail sheds USB devices --
    the mic/speaker/face panel vanish MID-RUN (aplay 'No such device' mid-word, /dev/ttyACM0 gone, the mic
    missing from arecord -l) with nothing in our log saying WHY. This thread is the why: it polls
    `vcgencmd get_throttled` every ROBOT_POWER_LOG_PERIOD_S and logs a WARNING the moment any new
    undervoltage/throttle flag appears. On a Pi 5 it ALSO reads the PMIC input voltage (`pmic_read_adc`
    EXT5V_V) so the log shows HOW FAR the rail sagged and warns EARLY when it nears the ~4.8V undervoltage
    floor -- before a deeper dip browns the Pi out. Pi-only by nature: no vcgencmd (Jetson/laptop) -> silent
    no-op, so board parity is preserved. ROBOT_POWER_MONITOR=0 disables; ROBOT_POWER_MIN_V sets the floor.
    Every poll it also logs a CONCISE draw line -- input volts + estimated 5V-input amps + total watts
    (summed from the PMIC's per-rail V*A) -- so the load is visible in normal runs; under --verbose it polls
    at 1 Hz (like `watch -n1 vcgencmd pmic_read_adc`) and adds a per-rail breakdown at DEBUG."""
    if os.environ.get("ROBOT_POWER_MONITOR", "1") != "1":
        return
    import shutil
    if not shutil.which("vcgencmd"):
        return   # not a Raspberry Pi -- nothing to poll
    verbose = log.isEnabledFor(logging.DEBUG)   # run_me.sh --verbose -> DEBUG: poll fast + per-rail detail
    period = 1.0 if verbose else max(2.0, float(_as_int(os.environ.get("ROBOT_POWER_LOG_PERIOD_S"), 10)))

    def _read_throttled():
        out = subprocess.run(["vcgencmd", "get_throttled"], timeout=5, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True).stdout
        return int(out.strip().split("=")[1], 16)   # "throttled=0x50005" -> 0x50005

    def _pmic():
        """Pi 5 PMIC full ADC dump -> (input_v, total_w, rails).  Each INTERNAL rail reports BOTH a current
        (`<rail>_A`) and a voltage (`<rail>_V`); pairing them gives per-rail watts, and their SUM is the
        board's total draw. input_v is the 5V input (EXT5V_V) -- HOW FAR the rail has sagged (the Pi wants
        ~5.1V and flags undervoltage below ~4.8V), the number a battery brownout otherwise hides. This is
        all `vcgencmd pmic_read_adc` exposes; every value is None/empty on boards with no PMIC ADC (Pi
        4/older). rails = {name: (V, A, W)}."""
        try:
            out = subprocess.run(["vcgencmd", "pmic_read_adc"], timeout=5, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True).stdout
        except Exception:
            return None, None, {}
        volts, amps = {}, {}
        for name, kind, val in re.findall(r"(\w+?)_([VA])\s+(?:volt|current)\(\d+\)=([\d.]+)[VA]", out):
            (volts if kind == "V" else amps)[name] = float(val)
        rails = {n: (volts[n], amps[n], volts[n] * amps[n]) for n in volts if n in amps}
        total_w = sum(t[2] for t in rails.values()) if rails else None
        return volts.get("EXT5V"), total_w, rails

    def _flags(val):
        return ", ".join(name for bit, name in _POWER_BITS.items() if val & (1 << bit)) or "none"

    def _draw(input_v, total_w, amps):
        """Concise 'V / ~A / W' draw string (skips fields the PMIC didn't provide)."""
        parts = []
        if input_v is not None:
            parts.append(f"{input_v:.2f}V")
        if amps is not None:
            parts.append(f"~{amps:.2f}A")
        if total_w is not None:
            parts.append(f"{total_w:.1f}W")
        return " ".join(parts) if parts else "n/a"

    try:
        min_v = float(os.environ.get("ROBOT_POWER_MIN_V", "4.90"))
    except ValueError:
        min_v = 4.90

    def _loop():
        last, warned_low = None, False
        while True:
            try:
                val = _read_throttled()
            except Exception:
                time.sleep(period)
                continue
            input_v, total_w, rails = _pmic()
            amps = (total_w / input_v) if (total_w and input_v) else None   # est. draw at the 5V input
            draw = _draw(input_v, total_w, amps)

            if last is None and val:
                log.warning("power: get_throttled=0x%x (%s) [%s] -- the 5V rail has ALREADY sagged this "
                            "boot: expect USB devices (mic/speaker/face) to drop off. Check the UBEC (5V "
                            "jumper, ~5A rating, short thick leads) and usb_max_current_enable=1 in the "
                            "boot config.", val, _flags(val), draw)
            elif last is not None and (val & ~last):
                log.warning("power: NEW event, get_throttled=0x%x (+%s) [%s] -- the 5V rail sagged or the "
                            "SoC throttled; USB devices (mic/speaker/face) may drop off the bus. On "
                            "battery: check the UBEC output under load + usb_max_current_enable=1.",
                            val, _flags(val & ~last), draw)
            else:
                # routine draw line: concise volts / est. amps / watts, every poll (also the startup line)
                log.info("power: %s | throttled=0x%x (%s)", draw, val, _flags(val))

            # VERBOSE (run_me.sh --verbose -> DEBUG): per-rail V*A=W breakdown, biggest consumers first, so
            # you can see WHICH rail spikes (VDD_CORE on an LLM burst, the 5V/USB rail on motor inrush).
            if rails and verbose:
                detail = "  ".join(f"{n} {v:.2f}V*{a:.2f}A={w:.2f}W"
                                   for n, (v, a, w) in sorted(rails.items(),
                                                              key=lambda kv: kv[1][2], reverse=True)
                                   if w >= 0.01)
                log.debug("power rails: %s", detail or "(all rails <0.01W)")

            # Early warning: the rail can sag toward the ~4.8V floor BEFORE a throttle flag trips -- that dip
            # is the brownout that freezes SSH mid-word. Warn once per crossing below ROBOT_POWER_MIN_V (and
            # note recovery), so the log shows the sag COMING instead of just going dark.
            if input_v is not None:
                if input_v < min_v and not warned_low:
                    log.warning("power: input SAGGING to %.2fV (< %.2fV, nearing the ~4.8V undervoltage "
                                "floor) -- the UBEC/wiring can't hold 5V under load; a deeper dip browns "
                                "out the Pi (frozen SSH). Raise the UBEC toward 5.1-5.2V (<=5.25V on USB-C) "
                                "with short thick leads, or feed via the UPS HAT on charged cells.",
                                input_v, min_v)
                    warned_low = True
                elif input_v >= min_v + 0.05 and warned_low:
                    log.info("power: input recovered to %.2fV.", input_v)
                    warned_low = False
            last = val
            time.sleep(period)

    threading.Thread(target=_loop, daemon=True, name="power-monitor").start()


def _reapply_audio():
    """Re-apply the USB-speaker ALSA default + volume at brain startup, so audio works no matter HOW the brain
    was launched. run_me.sh and the systemd unit's ExecStartPre already run scripts/setup_audio.sh
    --audio-only on a NORMAL launch -- but a systemd auto-restart on a STALE unit (installed before that
    ExecStartPre existed), or a reboot after a brownout, can bring the brain up WITHOUT it, and USB-speaker
    volume resets to 0 across a reboot -> the 'systems online' greeting + every earcon are silent with
    nothing saying why. Doing it here too (idempotent, best-effort) closes that gap from inside the brain, so
    it can't depend on the launcher. Skipped as root (setup_audio writes ~/.asoundrc in the user's home) or
    if the script is missing; ROBOT_AUDIO_SELFHEAL=0 disables it."""
    if os.environ.get("ROBOT_AUDIO_SELFHEAL", "1") != "1":
        return
    if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return   # writes ~/.asoundrc in the invoking user's home -- refuse as root; no-op off-POSIX
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "setup_audio.sh")
    if not os.path.isfile(script):
        return
    try:
        subprocess.run(["bash", script, "--audio-only"], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        log.info("audio: re-applied USB-speaker default + volume (setup_audio.sh --audio-only)")
    except Exception as e:
        log.debug("audio self-heal skipped: %s", e)


def main():
    # Hardware actions (intended PWM/GPIO/I2C) are logged by driver.py on the "robot.hw"
    # logger -- configure logging FIRST so the startup banner + mapping show on the console.
    # ROBOT_LOG_LEVEL (default INFO) lets run_me.sh --verbose crank it to DEBUG for a live-diagnosis
    # run (sensor sweeps, wake-word health) without touching code. Unknown value -> INFO.
    _lvl = getattr(logging, os.environ.get("ROBOT_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    logging.basicConfig(
        level=_lvl,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # CRIT-02: catch SIGTERM (systemd stop / run_me.sh --reload) and route it through the SAME graceful
    # cleanup as Ctrl-C. Without this, Python's default SIGTERM disposition kills the process instantly --
    # the control thread dies, the finally-block never runs, and the PCA9685 holds its LAST pulse (an
    # in-flight FORWARD keeps driving). CPython runs the handler on the main thread, so the KeyboardInterrupt
    # lands in wake.wait/handle_command and falls through to the existing except/finally that neutralises the
    # ESC + closes devices. Installed BEFORE RobotDriver() so it covers the whole live run.
    def _sigterm(_signum, _frame):
        raise KeyboardInterrupt   # reuse the existing Ctrl-C cleanup path
    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, OSError):
        pass
    # Make the USB speaker work on EVERY launch path (foreground, --detach, systemd restart, reboot after a
    # brownout) -- not just the ones that go through run_me.sh / ExecStartPre. Best-effort, before the greeting.
    _reapply_audio()
    # Board HAL: resolve the target board and apply its metadata BEFORE any hardware is built.
    # hal.current() also applies the board's launch env (e.g. the Pi's GPIOZERO_PIN_FACTORY=lgpio) via
    # os.environ.setdefault; here we seed the board's default pin map the same way -- so config/robot.env
    # and run_me.sh need not hard-code per-board pins/factory, yet anything already set still wins.
    import hal
    _board = hal.current()   # logs "board selected: ..." and applies the board's launch env
    for _pin, _val in _board.default_pins().items():
        os.environ.setdefault(_pin, str(_val))
    cfg = load_config()
    driver = RobotDriver()
    cam = Camera()
    mem = Memory()
    fc = Face()
    snd = Sounds(audio_lock, cfg)
    wake = WakeWord(cfg)
    fan = make_fan()   # optional enclosure exhaust fan (ROBOT_FAN_ENABLE=1 + ROBOT_FAN_PIN); None when disabled
    # Concierge Mode extras (no effect unless ROBOT_CONCIERGE_MODE=1): a LOCAL face recogniser + a PIR motion
    # sensor that wakes the robot like the wake word. On motion the callback sets motion_event + cancels the
    # in-progress listen so wait_for_trigger() returns 'MOTION'. Both degrade to no-ops if unconfigured.
    face_identifier = FaceID(mem)
    motion_event = threading.Event()
    # WHICH sensor decides "someone arrived". Only 'pir' ships today -- any digital-pin motion sensor, from a
    # few-pound HC-SR501 to an industrial Panasonic PaPIRs, since brain/pir.py just reads a HIGH/LOW (pick
    # one with scripts/patches/common/6_motion_pir_*.sh). The dispatch stays because a wake sensor only has
    # to expose available / arm_cooldown / stats_line / close / last_wake_at + counters for everything
    # downstream -- the two-stage look, the outcome cooldowns, the latency line -- to work unchanged.
    #
    # Know this before choosing where to POINT it: a PIR is passive, so it fires when a warm body CROSSES
    # between its detection zones, which makes head-on approach its WORST case. Measured on the reference
    # build: 12 deliberate walk-straight-at-it approaches produced 1-3 detections with both pots maxed,
    # while the same sensor gave 12/12 when the person crossed its field. Aim it ACROSS the traffic.
    _wake_cb = lambda: (motion_event.set(), wake.cancel_listen())
    _sensor_kind = (os.environ.get("ROBOT_WAKE_SENSOR", "") or "pir").strip().lower()
    if _sensor_kind != "pir":
        log.warning("unknown ROBOT_WAKE_SENSOR=%r -- using 'pir'", _sensor_kind)
    _pir_pin = os.environ.get("ROBOT_PIR_PIN") if concierge.enabled() else None
    if concierge.enabled() and os.environ.get("ROBOT_ZIGBEE_WAKE", "0") == "1":
        # OR MODE: a wireless Zigbee sensor wakes the robot as well as the wired PIR, whichever fires
        # first. Deliberately ADDITIVE rather than another ROBOT_WAKE_SENSOR value -- the Zigbee sensor
        # COMPLEMENTS the wired one (it covers ground the wired one cannot see) rather than replacing it,
        # and every combination has to work: wired only, both, or Zigbee only with ROBOT_PIR_PIN empty.
        # Each source independently degrades to available=False, so an unplugged dongle, a dead broker or
        # a flat sensor battery leaves the wired PIR running exactly as it does today.
        from wake_sources import CompositeWake
        from zigbee_wake import ZigbeeWake
        _composite = CompositeWake(_wake_cb)
        _composite.add(MotionWake(_pir_pin, _composite.make_callback(MotionWake.source_name)))
        _composite.add(ZigbeeWake(_composite.make_callback(ZigbeeWake.source_name)))
        _composite.log_ready()
        motion_wake = _composite
    else:
        motion_wake = MotionWake(_pir_pin, _wake_cb)
    if concierge.enabled():
        # Load the face model NOW, in the background, instead of lazily on the first identify(). That first
        # call happens INSIDE a visitor's recognition window, so the multi-second onnxruntime + ArcFace import
        # used to eat most of the 5s we had to recognise them -- making the very first visitor of a run the one
        # most likely to get a silent smile. A thread so a slow load never delays the robot coming up.
        if os.environ.get("ROBOT_FACE_ID_WARMUP", "1") == "1":
            threading.Thread(target=face_identifier.ensure_loaded, daemon=True, name="faceid-warmup").start()
        # Sweep any stale detect-captures from previous runs (age + size cap) so the folder can't creep up
        # between cron purges. Backgrounded: it's disk I/O over a folder that may hold thousands of files.
        if captures.enabled():
            log.info("detect captures: keeping the deciding frame of each greeting in %s "
                     "(mode=%s, purge after %s day(s)) -- browse it to see what I saw", captures.directory(),
                     "all looks" if captures.keep_all() else "greetings only",
                     os.environ.get("ROBOT_DETECT_CAPTURE_KEEP_DAYS", "7") or "7")
            threading.Thread(target=captures.prune, daemon=True, name="captures-prune").start()

    # P2-3: ONE teardown list for BOTH exit paths (the early no-trigger return AND the main finally),
    # so the two can never drift apart again. driver.stop stays FIRST (ESC to neutral before anything
    # closes); fan is None when disabled. remote_srv.stop() and the SIG_IGN loop stay in the finally --
    # remote_srv does NOT exist yet at the early return.
    def _close_devices():
        for closer in (driver.stop, driver.close, cam.close, fc.close, mem.close, snd.close, wake.close,
                       motion_wake.close) + ((fan.close,) if fan else ()):
            try:
                closer()
            except Exception:
                pass

    # Robot identity: persist who it is (name + what it can do) as top-priority RAG memory, and detect a
    # first run / rename so it introduces itself below. Name comes from ROBOT_NAME (default Jarvis).
    robot_name = cfg.get("robot_name") or "Jarvis"
    if halloween.enabled():
        # Halloween Mode (a Concierge SUB-mode): a spooky, STATIONARY doorway spirit -- so the whole
        # conversation stays in character, not just the opening scare.
        robot_caps = halloween.PERSONA.format(name=robot_name)
    elif concierge.enabled():
        # Concierge Mode: a STATIONARY persona -- it introduces itself as the Concierge and never drives.
        robot_caps = concierge.PERSONA_CAPS.format(name=robot_name)
    else:
        robot_caps = (f"I am {robot_name}, a friendly voice-controlled RC crawler robot car. I can drive around "
                      "(forward, reverse, and turn), explore a room on my own, find and go to objects, look at "
                      "and describe what my camera sees, remember people and places you show me, and chat.")
    identity_status = mem.ensure_identity(robot_name, robot_caps)

    # Step 15: the wake-mic pause is REFCOUNTED. The obstacle announcement and a behaviour's narration can
    # overlap in two threads, and wakeword's pause/resume is a plain boolean -- without the refcount the first
    # say() to finish would re-open the mic mid-TTS of the other and re-block its aplay on audio_lock.
    mic_pause_lock = threading.Lock()
    mic_pause_count = [0]

    def say(text, talk_expr="talking", end_expr="idle"):
        text = (text or "").strip()
        if not text:
            return
        # During a behaviour the background voice-abort listener holds the mic + audio_lock; pause it so
        # this narration can play (else speak() would block on audio_lock). No-op when not in a behaviour.
        paused = False
        if voice_abort_active.is_set():
            try:
                with mic_pause_lock:
                    if mic_pause_count[0] == 0:
                        wake.pause_mic()
                    mic_pause_count[0] += 1
                paused = True
            except Exception:
                pass
        # Start the 'talking' face WHEN the audio plays (via on_play), not now -- otherwise the mouth animates
        # through the whole (slow) Piper synthesis before any sound. The face holds its prior state (e.g.
        # 'thinking') until playback begins, then end_expr once speak() returns.
        speak(cfg, text, on_play=lambda: fc.set_expression(talk_expr))
        fc.set_expression(end_expr)
        if paused:
            try:
                with mic_pause_lock:
                    mic_pause_count[0] -= 1
                    if mic_pause_count[0] == 0:
                        wake.resume_mic()
            except Exception:
                pass

    # Step 15: single-flight is MANDATORY -- repeated trips (driver debounces at 3s) during one long
    # narration would each synthesize then block, replaying a warning backlog later.
    obstacle_say_gate = threading.Lock()

    def _announce_obstacle():
        if not obstacle_say_gate.acquire(blocking=False):
            return   # a warning is already speaking -- don't queue another synthesis
        try:
            say("Obstacle ahead. Stopping.", talk_expr="alarmed", end_expr="alarmed")
        finally:
            obstacle_say_gate.release()

    def on_obstacle(distance_cm):
        print(f"Obstacle {distance_cm:.1f} cm ahead -> stopping")
        fc.set_expression("alarmed")
        try:
            mem.record("obstacle", notes=f"{distance_cm:.0f}cm ahead", distances=driver.distances())
        except Exception:
            pass
        snd.play("error")   # fire-and-forget earcon; never on the synchronous driver.stop() path
        # Run TTS off the control thread; say() (not speak()) pauses the behaviour's voice-abort mic so the
        # warning plays NOW, and end_expr keeps the face alarmed.
        threading.Thread(target=_announce_obstacle, daemon=True).start()

    # Shared abort for autonomous behaviours (explore/find): tripped by HTTP /stop, the wake word during a
    # behaviour (listener below), or Ctrl-C. behavior_active lets on_stop report whether anything is running.
    # CRIT-03: behavior_abort + estop are cleared at the start of each TURN (in handle_command), NOT each
    # behaviour -- so a /stop that lands mid-turn (e.g. during the spoken confirmation) survives into the
    # behaviour and aborts it, instead of being erased by a fresh-run clear.
    behavior_abort = threading.Event()
    estop = threading.Event()   # CRIT-03: latches an out-of-band /stop so an in-flight turn can't override it
    behavior_active = threading.Event()
    voice_abort_active = threading.Event()   # set while the background voice-abort listener runs (say() pauses the mic)

    def run_behavior(fn, *args):
        # Run an autonomous behaviour with a graceful abort: while it runs, Ctrl-C / HTTP /stop / the wake
        # word set an abort flag (honoured at the next look/step boundary) and return to the prompt instead
        # of killing the program. Any behaviour-internal error is contained, not fatal, and we always leave
        # the ESC (Electronic Speed Controller) at neutral.
        ev = threading.Event()
        installed, prev = False, None

        def _abort(_signum, _frame):
            # Print only on the FIRST Ctrl-C: the behaviour notices `ev` at the next look/step boundary,
            # so mashing Ctrl-C in that window would otherwise spam the line. (ev.set() is idempotent.)
            if not ev.is_set():
                print("\n(aborting — returning to prompt)")
            ev.set()

        try:
            prev = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _abort)
            installed = True
        except (ValueError, OSError):
            pass   # not the main thread -> Ctrl-C falls through to the outer handler (quits)
        behavior_active.set()   # CRIT-03: behavior_abort/estop are cleared per-TURN in handle_command, not here
        # Voice abort: listen for the wake word in the BACKGROUND during the behaviour; "hey jarvis" trips
        # on_stop(). Live + working wake word only (dry-run/no-model rely on HTTP /stop + Ctrl-C). say()
        # pauses this mic (voice_abort_active) so the listener never records the robot's own narration.
        listener = None
        if wake.available and not wake.dry_run:
            voice_abort_active.set()

            def _voice_abort():
                try:
                    if wake.wait(audio_lock) == "WAKE":
                        print("\n(wake word heard — stopping)")
                        on_stop()
                except Exception as e:
                    log.debug("voice-abort listener error: %s", e)

            listener = threading.Thread(target=_voice_abort, daemon=True, name="voice-abort")
            listener.start()
        try:
            # aborted() is honoured at every look/step boundary -- ev (Ctrl-C) OR the shared flag.
            fn(*args, aborted=lambda: ev.is_set() or behavior_abort.is_set())
        except Exception as e:
            print(f"behaviour: {e}", file=sys.stderr)
        finally:
            if listener is not None:
                wake.cancel_listen()        # break the background listen -> it returns CANCELLED and exits
                listener.join(timeout=3.0)
                voice_abort_active.clear()
            behavior_active.clear()
            driver.stop()
            fc.set_expression("idle")
            if installed:
                try:
                    signal.signal(signal.SIGINT, prev)
                except Exception:
                    pass

    def on_stop():
        """Interrupt a running behaviour / timed drive from OUTSIDE the turn (HTTP /stop, or the wake word
        during a behaviour). Trips the abort flag AND neutralises the ESC immediately. Safe to call anytime;
        returns whether a behaviour was actually running."""
        active = behavior_active.is_set()
        estop.set()             # CRIT-03: latch BEFORE driver.stop() so an in-flight turn can't override it
        behavior_abort.set()
        try:
            driver.stop()
        except Exception:
            pass
        log.info("STOP requested (behaviour active=%s)", active)
        return active

    driver.on_obstacle = on_obstacle

    turn_lock = threading.RLock()  # one command turn at a time (voice/HTTP); RLock so remote_command can
    #                                hold it across the wake-mic pause AND handle_command's own acquire

    def _start_thinking():
        """Kick off the processing ('thinking') earcon with backend-aware timing: REMOTE (Gemini / any
        non-local) stays silent ~4s so a fast reply plays NO cue, and plays only ONCE if it's slow;
        LOCAL leads quickly (~0.5s) and re-cues with the 'still...' set. All keys have safe defaults so
        config/robot.env need not set them."""
        # This cue covers the command turn's INTENT call -- remote (pure gemini) only. Under 'mixed' the
        # intent stays LOCAL (only vision is remote), so mixed uses the LOCAL quick-lead + re-cue timing.
        if cfg.get("ai_backend", "local") == "gemini":
            snd.start_thinking(lead_ms=_as_int(os.environ.get("ROBOT_THINKING_LEAD_REMOTE_MS"), 4000),
                               repeat_s=_as_int(os.environ.get("ROBOT_THINKING_REPEAT_REMOTE_S"), 0))
        else:
            snd.start_thinking()   # env defaults: ROBOT_THINKING_LEAD_MS / ROBOT_THINKING_REPEAT_S

    # Wake person-check: on "hey jarvis" we snap a frame + ask the VLM if a PERSON is in front, IN PARALLEL
    # with the command recording/transcription -- so on the fast REMOTE backend it's essentially free, and
    # the answer is fed into THAT turn's prompt so the robot replies person-aware. ROBOT_WAKE_PERSON_CHECK:
    # auto (default) = ON for remote, OFF for local (a slow local VLM would just delay the command); 1/0 force.
    # P2-2: gen = wake-id. A SLOW check may only store into its OWN wake, never into a turn that has already
    # consumed the note. Written only from the main thread; the worker just compares + stores (GIL-atomic).
    person_ctx = {"note": "", "gen": 0}

    def _person_check_enabled():
        v = os.environ.get("ROBOT_WAKE_PERSON_CHECK", "auto").strip().lower()
        if v in ("0", "off", "no", "false"):
            return False
        if v in ("1", "on", "yes", "true"):
            return True
        return vision.vision_backend(cfg) == "gemini"   # "auto": ON only when VISION is remote (fast; hidden in the record window)

    def _concierge_recognize_s():
        """Seconds to keep smiling + retrying face recognition on a Concierge motion wake (default 5).
        0 = a single shot (the old one-frame behavior)."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_RECOGNIZE_S", "5") or 5))
        except ValueError:
            return 5.0

    def _concierge_recognize_interval_s():
        """Pause BETWEEN capture attempts inside that window (default 0.25s). Unpaced, the loop ran the camera
        flat out at ~20 stills/second for the whole window -- ~100 near-identical frames per wake, which on a
        chattery PIR meant ~50,000 stills an hour, 24/7. A quarter-second still gives ~20 independent looks."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_RECOGNIZE_INTERVAL_S", "0.25") or 0.25))
        except ValueError:
            return 0.25

    def _concierge_face_hits():
        """How many frames in the window must show a face before we accept 'someone IS here' and greet them
        (default 2). The Haar detector false-positives on scenery, so a one-frame gate let a single fluke
        produce a GHOST greeting to an empty room. A recognised MATCH still greets on its first frame."""
        try:
            return max(1, int(float(os.environ.get("ROBOT_CONCIERGE_FACE_HITS", "2") or 2)))
        except ValueError:
            return 2

    def _concierge_probe_tries():
        """STAGE A: how many cheap one-frame presence looks to take before declaring the room empty
        (default 2, ~0.15s apart -- it returns early the moment one hits, so a real visitor costs one).
        0 = skip Stage A entirely and always run the full recognition window (the old behaviour)."""
        try:
            return max(0, int(float(os.environ.get("ROBOT_CONCIERGE_PROBE_TRIES", "2") or 0)))
        except ValueError:
            return 2

    def _concierge_probe_interval_s():
        """Pause between those Stage-A looks (default 0.15s). Short on purpose: it is meant to catch a
        visitor mid-stride, not to wait for one to arrive -- that is Stage B's job."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_PROBE_INTERVAL_S", "0.15") or 0.15))
        except ValueError:
            return 0.15

    def _concierge_probe_max_s():
        """CEILING for 'keep looking while the sensor still asserts' (ROBOT_CONCIERGE_PROBE_MAX_S, default
        6s; 0 disables the whole behaviour and restores the fixed _concierge_probe_tries() budget).

        A fixed budget has to GUESS how long someone takes to walk into frame, and it guesses identically
        for a visitor already at the door and one who tripped the sensor 4m out. The sensor knows better: a
        PIR holds its output HIGH while it keeps re-triggering, so Stage A can just keep asking. The floor
        (probe tries) is still always taken, because the output can fall while a visitor is still walking in.

        The ceiling is a SAFETY STOP, not a tuning knob: a sensor stuck HIGH (failed part, a heat source
        parked in frame) would otherwise pin the camera at 100% indefinitely. Set it well past a realistic
        approach so the SENSOR decides when to stop, not the clock -- giving up while the sensor is still
        asserting is the one outcome that is never right."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_PROBE_MAX_S", "20") or 0))
        except ValueError:
            return 20.0

    def _concierge_smile_hold_s():
        """How long the SMILE outlasts the sensor's last pulse (ROBOT_CONCIERGE_SMILE_HOLD_S, default 1.5s).

        Deliberately much shorter than ROBOT_PIR_PRESENCE_HOLD_S, which governs how long we keep LOOKING.
        It only has to bridge the gap between a PIR's output pulses so the face cannot flicker mid-visit;
        past that, a smiling face with nobody in front of it is just wrong, and the field log showed a
        median 9.6s of it per look. Raise it if the face flickers while you stand there; lower it if the
        smile lingers after you have gone."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_SMILE_HOLD_S", "1.5") or 1.5))
        except ValueError:
            return 1.5

    def _concierge_recognize_max_s():
        """STAGE B safety stop for the same 'whichever lasts longer wins' rule
        (ROBOT_CONCIERGE_RECOGNIZE_MAX_S, default 45s; 0 = fixed window only).

        ROBOT_CONCIERGE_RECOGNIZE_S is the FLOOR -- always run at least that long, because a visitor can
        step out of the sensor's zones while still walking toward the camera. Past it, keep recognising for
        as long as the sensor says somebody is there. The cap bounds a stuck sensor to one long window per
        wake, after which the outcome cooldown takes over."""
        try:
            return max(0.0, float(os.environ.get("ROBOT_CONCIERGE_RECOGNIZE_MAX_S", "45") or 0))
        except ValueError:
            return 45.0

    def _concierge_cooldown_s(key, default):
        """One of the OUTCOME cooldowns (silent / greeted) armed on the PIR after a look -- see brain/pir.py.
        0 disables that one, restoring 'react to every accepted edge'."""
        try:
            return max(0.0, float(os.environ.get(key, "") or default))
        except ValueError:
            return float(default)

    # Concierge telemetry. Silent looks are now a DEBUG line (they were 3,741 INFO lines a day, which buried
    # every line that mattered), so the ONE thing that has to survive at INFO is the shape of the hour:
    # how many edges the PIR offered, how many died in the cheap probe, and how many became a greeting. That
    # single line is what makes the next tuning round possible -- without it you are back to guessing whether
    # the sensor or the recogniser is at fault.
    concierge_stats = {"probe_silent": 0, "probe_hit": 0, "window_lost": 0, "greeted": 0, "by_name": 0}

    def _concierge_summary_loop(period_s):
        base = {"woke": 0, "outcome": 0, "debounce": 0, "spike": 0}
        while True:
            time.sleep(period_s)
            try:
                seen = dict(concierge_stats)
                for k in concierge_stats:
                    concierge_stats[k] = 0
                woke = motion_wake.accepted - base["woke"]
                out = motion_wake.rejected_outcome - base["outcome"]
                deb = motion_wake.rejected_cooldown - base["debounce"]
                spk = motion_wake.rejected_spike - base["spike"]
                base.update({"woke": motion_wake.accepted, "outcome": motion_wake.rejected_outcome,
                             "debounce": motion_wake.rejected_cooldown, "spike": motion_wake.rejected_spike})
                if not (woke or out or deb or spk or any(seen.values())):
                    continue          # a genuinely quiet hour stays quiet in the log
                log.info("concierge: last %.0f min -- PIR %d wake(s) (+%d held by cooldown, +%d debounce, "
                         "+%d noise) | %d silent (probe), %d looked at, %d lost in the window, "
                         "%d greeting(s) (%d by name)",
                         period_s / 60.0, woke, out, deb, spk, seen["probe_silent"], seen["probe_hit"],
                         seen["window_lost"], seen["greeted"], seen["by_name"])
            except Exception as e:
                log.debug("concierge summary error: %s", e)

    # Started HERE, not up with the other concierge warmups, because it closes over the helpers defined in
    # this scope -- referencing them any earlier in main() is an unbound local. 0 = off; needs a live PIR
    # (with no motion sensor there is nothing for it to summarise).
    if concierge.enabled() and motion_wake.available:
        try:
            _summary_s = max(0.0, float(os.environ.get("ROBOT_CONCIERGE_SUMMARY_S", "3600") or 0))
        except ValueError:
            _summary_s = 3600.0
        if _summary_s > 0:
            threading.Thread(target=_concierge_summary_loop, args=(_summary_s,), daemon=True,
                             name="concierge-summary").start()

    def _keep_capture(jpeg, outcome, name=None):
        """Keep the frame that DECIDED a motion wake in ~/detect-captures, so you can see what Jarvis saw
        when it greeted (or didn't). Silent looks ('noface') are only kept with ROBOT_DETECT_CAPTURE_MODE=all
        -- they're the bulk of the frames, and keeping every one is what fills a disk. A cron job purges the
        folder by age (scripts/purge_detect_captures.sh); brain/captures.py also self-prunes.

        Logs the saved PATH at INFO, and returns it so the caller can name it in its own decision line. That
        link is the point: reviewing a folder of 'unknown' captures is only useful if each image can be tied
        back to the scores that produced it -- otherwise you have pictures with no verdicts and verdicts with
        no pictures. Never raises."""
        if outcome == "noface" and not captures.keep_all():
            return None
        path = captures.save(jpeg, outcome, name)
        if path:
            log.info("concierge: kept the deciding frame -> %s", path)
        return path

    def _wake_person_check(gen):
        try:
            res = vision.check_person(cfg, cam.capture_jpeg())
            note = (f"(Right now a person is in front of the camera: "
                    f"{res.get('description') or 'someone'}.)") if res.get("present") else ""
            if person_ctx["gen"] == gen:   # stale check (a later wake/turn moved on) -> drop, don't leak
                person_ctx["note"] = note
        except Exception as e:
            log.debug("wake person-check error: %s", e)

    def handle_command(text, source="voice"):
        """Run ONE command turn: text -> LLM intent JSON -> action (drive/say/remember/find/explore).
        Shared by the voice loop AND the phone HTTP endpoint; turn_lock keeps the two from overlapping.
        `source` is "voice" or "http"; over HTTP the CAMERA actions (REMEMBER/FIND/EXPLORE) are blocked
        unless ROBOT_HTTP_ALLOW_CAMERA=1. Returns (action, reply)."""
        with turn_lock:
            estop.clear()
            behavior_abort.clear()         # CRIT-03: a /stop belongs to the turn it arrived in, not the next one
            fc.set_expression("thinking")
            _start_thinking()              # proof-of-life cue while the model runs (backend-aware timing)
            t_think = time.monotonic()
            try:
                ctx = mem.prompt_context(query=text)   # rank memory (facts + scenes) for relevance to THIS ask
                if source == "voice":
                    if person_ctx.get("note"):
                        ctx = (person_ctx["note"] + " " + ctx).strip()
                    person_ctx["note"] = ""
                    person_ctx["gen"] += 1   # consume: invalidate any still-running check for this wake
                resp = query_ollama(cfg, text, context=ctx)
            except Exception as e:
                snd.stop_thinking()
                print(f"llm: {e}", file=sys.stderr)
                log.warning("llm failed after %.1fs: %s", time.monotonic() - t_think, e)
                driver.stop()  # fail safe
                # SPEAK the actual error (not just an 'uh oh' earcon) so you hear WHAT broke -- and the
                # phone gets it back as the JSON reply too. _short_error keeps it speakable (one line).
                reply = f"Uh oh. I think something went wrong. It seems like: {_short_error(e)}."
                say(reply)
                return "ERROR", reply
            snd.stop_thinking()            # stop BEFORE any say() so it doesn't fight TTS
            log.info("think: model replied in %.1fs", time.monotonic() - t_think)

            action = str(resp.get("action", "DRIVE")).upper()
            target = resp.get("target")
            reply = resp.get("reply", "")

            # --- Concierge Mode: STATIONARY persona -- refuse EVERY movement intent (drive/explore/find)
            #     with a random in-character line, before any driving dispatch can run. Halloween Mode (a
            #     Concierge sub-mode) refuses with a SPOOKY line instead. ---
            if concierge.enabled() and action in concierge.MOVEMENT_ACTIONS:
                reply = halloween.refusal() if halloween.enabled() else concierge.refusal()
                print(f"concierge: refusing {action} (stationary) -> SAY")
                say(reply)
                return "SAY", reply

            # --- Trigger gate: autonomy must be asked for, on the RAW transcript. ---
            if action in ("EXPLORE", "FIND") and not is_explore_command(text):
                print(f"(gate: '{action}' not triggered by an explore phrase -> DRIVE)")
                log.info("trigger gate: demoting %s -> DRIVE (no explore phrase uttered)", action)
                action = "DRIVE"
            if action == "FIND" and not target:
                action = "EXPLORE"   # asked to find but no object named -> just look around
            if action == "REMEMBER" and not is_remember_command(text):
                print("(gate: 'REMEMBER' not triggered by a teach phrase -> SAY)")
                action = "SAY"       # gemma said REMEMBER but no "this is/remember/..." in the transcript
            if action == "REMEMBER" and not str(target or "").strip():
                action = "SAY"       # asked to remember but no name -> just reply, store nothing

            # A live "what do you see / what is this" must LOOK (fresh capture) rather than answer from
            # stored scene memory -- promote SAY -> LOOK on those phrases even if the model chose SAY.
            if action == "SAY" and is_look_command(text):
                print("(gate: a 'what do you see' question -> LOOK, a fresh camera look)")
                log.info("look gate: promoting SAY -> LOOK (live view request)")
                action = "LOOK"

            # --- HTTP hardening: over the network, BLOCK the camera-using actions unless explicitly
            # allowed, so a stolen token can't even get a one-shot photo description. Downgrade to SAY. ---
            if source == "http" and action in ("REMEMBER", "FIND", "EXPLORE", "LOOK") \
                    and os.environ.get("ROBOT_HTTP_ALLOW_CAMERA", "0") != "1":
                log.info("http: blocking camera action %s (ROBOT_HTTP_ALLOW_CAMERA=0) -> SAY", action)
                action, reply = "SAY", "I can only drive and chat over the phone, not use my camera."

            if action == "STOP":
                print("action=STOP")
                driver.stop()
                say(reply)
            elif action == "SAY":
                # Not a movement command -- just speak gemma's conversational reply; don't drive.
                print(f"action=SAY reply={reply!r}")
                driver.stop()
                say(reply)
            elif action == "REMEMBER":
                # Teach the robot a person/thing: photograph what the camera sees, describe it with
                # the VLM, store name + relationship + photo in SQLite -- then it's prompt context.
                name = str(target).strip()
                note = str(resp.get("note") or "").strip()
                print(f"action=REMEMBER name={name!r} note={note!r}")
                driver.stop()
                fc.set_expression("capturing")
                jpeg = cam.capture_jpeg()
                desc = ""
                if jpeg:
                    fc.set_expression("thinking")
                    _start_thinking()
                    desc = vision.describe(cfg, jpeg).get("description", "")
                    snd.stop_thinking()
                img_path = mem.save_photo(jpeg, name)
                mem.remember_fact(name, note, desc, img_path)
                reply = reply or f"Okay, I'll remember {name}."
                say(reply)
            elif action == "LOOK":
                # Live look: capture a FRESH frame and describe what's visible NOW (never from memory/RAG).
                print("action=LOOK")
                driver.stop()
                fc.set_expression("capturing")
                jpeg = cam.capture_jpeg()
                fc.set_expression("thinking")
                _start_thinking()
                # Pass the operator's actual words so the VLM focuses on what they asked (e.g. the CONTENT of
                # a photo they're holding up) instead of a generic one-sentence scene summary.
                desc = vision.describe(cfg, jpeg, query=text)
                snd.stop_thinking()
                mem.record_scene(desc.get("description", ""), desc.get("objects", []), driver.distances())
                reply = desc.get("description") or "I'm not sure what I'm looking at."
                say(reply)
            elif action == "FIND":
                print(f"action=FIND target={target!r}")
                if estop.is_set():   # CRIT-03: /stop landed during this turn -> don't launch the behaviour
                    reply = "Okay, stopping."
                    say(reply)
                    return "STOP", reply
                say(reply)
                run_behavior(behavior.run_find, cfg, driver, cam, mem, fc, say, snd, str(target))
            elif action == "EXPLORE":
                print("action=EXPLORE")
                if estop.is_set():   # CRIT-03: /stop landed during this turn -> don't launch the behaviour
                    reply = "Okay, stopping."
                    say(reply)
                    return "STOP", reply
                say(reply)
                run_behavior(behavior.run_explore, cfg, driver, cam, mem, fc, say, snd)
            else:  # DRIVE (and any unknown action falls here as plain teleop)
                drive = str(resp.get("drive", "STOP")).upper()
                steer = str(resp.get("steer", "CENTER")).upper()
                if drive not in VALID_DRIVE:
                    print(f"invalid drive {drive!r} -> STOP")
                    drive = "STOP"
                if steer not in VALID_STEER:
                    steer = "CENTER"
                speed = _as_int(resp.get("speed"), 0)
                dur = _as_int(resp.get("duration_ms"), 0)
                if drive != "STOP":
                    crawl = _as_int(os.environ.get("ROBOT_DRIVE_CRAWL_SPEED"), 15)
                    if speed < 5:      # model gave 0/blank -> a gentle crawl, not a lurch
                        speed = crawl
                    if not wants_speed(text) and speed > crawl:   # CRAWL unless the user asked for speed
                        log.info("crawl default: capping speed %d -> %d (say 'fast' to override)", speed, crawl)
                        speed = crawl
                    if dur < 300:      # clamp tiny/zero durations up to a usable move
                        dur = 1500
                print(f"action=DRIVE drive={drive} steer={steer} speed={speed} dur={dur}ms reply={reply!r}")
                if estop.is_set():   # CRIT-03: /stop arrived during this turn (e.g. while the LLM was thinking)
                    log.info("estop: stop arrived during this turn -- suppressing drive")
                    driver.stop()
                else:
                    driver.command(drive, steer, speed, dur)  # non-blocking; motion in driver thread
                    if estop.is_set():   # stop landed between the check and command(): re-assert it
                        driver.stop()
                say(reply)                                 # synchronous; returns after playback
            return action, reply

    # LIVE needs a working wake word -- ENTER push-to-talk was removed from live (it survives only in
    # --dry). With no usable trigger, refuse to start rather than sit there dead.
    if not wake.dry_run and not wake.available:
        print("Live mode has no trigger: the wake word didn't load and ENTER push-to-talk is --dry "
              "only. Fix ROBOT_WAKEWORD_ENABLE/ROBOT_WAKEWORD_MODEL, or run with --dry.", file=sys.stderr)
        _close_devices()   # P2-3: same teardown as the main finally (also closes motion_wake + fan)
        return

    mode = "DRY RUN — no hardware will be driven" if driver.dry_run else "LIVE HARDWARE"
    if getattr(driver, "motors_off", False):
        mode += " — MOTORS OFF (stationary: wheels won't move; everything else live)"
    if getattr(driver, "hw_failed", False):
        mode += " — DEGRADED (some hardware failed; see the HARDWARE FAILURE lines above)"
    print(f"Robot Brain online (RC crawler + vision + face).  [{mode}]")
    # Backend banner. Text routing follows inference.text_backend(): gemini AND mixed hand conversation +
    # vision to Gemini WHEN a key is present; without a key (or backend=local) the rig stays fully local.
    _ab = cfg.get("ai_backend", "local")
    _have_key = bool((cfg.get("gemini_api_key") or "").strip())
    if _ab in ("gemini", "mixed") and _have_key:
        _vm = cfg.get("gemini_vision_model") or cfg.get("gemini_model")
        _split = f"text={cfg.get('gemini_model')}, vision={_vm}" if _vm != cfg.get("gemini_model") \
            else f"text + vision on {cfg.get('gemini_model')}"
        if _ab == "mixed":
            print(f"AI brain: MIXED — conversation + vision REMOTE Gemini ({_split}); auto-falls back to "
                  f"LOCAL ({cfg.get('model')}) if the network/remote is down (needs network).")
        else:
            print(f"AI brain: REMOTE Gemini ({_split}) (needs network).")
    elif _ab in ("gemini", "mixed"):   # a remote-capable backend was chosen but no key -> stay fully local
        print(f"AI brain: ROBOT_AI_BACKEND={_ab} but ROBOT_GEMINI_API_KEY is EMPTY — staying fully LOCAL "
              f"({cfg.get('model')}). Set the key for remote conversation + vision (docs/gemini-setup.md).",
              file=sys.stderr)
        log.warning("ROBOT_AI_BACKEND=%s with no ROBOT_GEMINI_API_KEY — running fully local", _ab)
    else:
        print(f"AI brain: LOCAL Ollama ({cfg.get('model')}) — fully offline.")
    if wake.dry_run:
        print("Dry-run: press ENTER to talk, 'r' to simulate the wake word, 'q' to quit.")
    else:
        # The wake word is whatever model is loaded (e.g. the stock 'hey jarvis') -- print the real
        # label so the banner never lies about what to say.
        print(f"Wake word ON: say '{getattr(wake, 'label', 'the wake word')}' to wake me. 'q' quits (over SSH).")
    print("Autonomy (look around / find X) only runs if you say an explore phrase.")
    print("During autonomy, Ctrl-C aborts back to the prompt; at the prompt, 'q' or Ctrl-C quits.")

    # Warm the model in the BACKGROUND (best-effort) so the first command isn't a multi-minute cold load.
    #
    # THIS USED TO BE A BLOCKING CALL, AND IT COST A FIELD ROBOT 17 MINUTES OF ITS LIFE. When ollama can't
    # bring its llama-server up (out of memory, model not pulled, a wedged daemon) the HTTP request does not
    # honour our timeout -- one run sat here for 17m12s and then failed anyway, with the log reporting a
    # cheerful "failed after 1 attempt(s) over 15s". For all of those 17 minutes the main loop had not
    # started: no motion wakes, no wake word, no greetings. A concierge that is deaf and blind for a
    # quarter of an hour after every boot is a far worse failure than a slow first sentence, which is the
    # ONLY thing the warmup was ever protecting against.
    #
    # So it runs on a daemon thread now, exactly like the face model above (see the faceid-warmup thread).
    # The robot is live immediately; the model finishes warming behind it, and if it never does, the first
    # command cold-loads -- which is precisely the fallback the old code already had on failure.
    if os.environ.get("ROBOT_WARMUP", "1") == "1":
        def _warm():
            try:
                ok = inference.warm_up(cfg, _llm_timeout())
                log.info("model warmup: %s", "ready" if ok
                         else "failed -- the first command will cold-load instead")
            except Exception as e:
                log.warning("model warmup errored (%s) -- the first command will cold-load", e)
        threading.Thread(target=_warm, daemon=True, name="llm-warmup").start()
        print("Model warmup: started in the background (the robot is live now).")
    snd.play("greeting")

    # ESC ARMING PROMPT (headless safety). The WP-1060 arms to the neutral it sees at ITS power-on, so the
    # brain must already be up + HOLDING NEUTRAL before the ESC is switched on -- otherwise it mis-arms and the
    # brain's forward/neutral pulses can drive REVERSE (a runaway that has thrown a wheel). Under systemd nobody
    # is watching the console, so SPEAK the cue: by now driver has held neutral since startup, so this is the
    # moment to power the ESC. LIVE + motors-on + crawler only (dry-run / motors-off / concierge never drive).
    # Disable with ROBOT_ESC_ARM_PROMPT=0 once your power sequence is muscle-memory.
    if (os.environ.get("ROBOT_ESC_ARM_PROMPT", "1") == "1"
            and not getattr(driver, "dry_run", False) and not getattr(driver, "motors_off", False)
            and not concierge.enabled()):
        try:
            speak(cfg, "Systems online, holding the motor at neutral. "
                       "Switch the speed controller on now, and wait for its ready beep.")
        except Exception as e:
            log.warning("ESC arm prompt speak failed (%s) -- continuing", e)

    # First run (or a name change): introduce itself out loud so a new owner learns who it is and what it can
    # do. Only on 'new'/'renamed' -- every later run stays quiet. speak() is best-effort (no speaker = logged).
    if identity_status in ("new", "renamed"):
        wakeword_label = (getattr(wake, "label", "") or "the wake word").replace("_", " ")
        if identity_status == "new":
            intro = f"Hello! {robot_caps} Say {wakeword_label} to talk to me."
        else:
            intro = f"Hello! My name is now {robot_name}. Say {wakeword_label} and I am ready to help."
        print(f"Intro ({identity_status}): {intro}")
        fc.set_expression("happy")
        try:
            speak(cfg, intro)
        except Exception as e:
            log.warning("intro speak failed (%s) -- continuing", e)

    # Phone command endpoint (HTTP; ON BY DEFAULT). A tiny stdlib server runs INSIDE the brain so your
    # phone can POST text commands over Wi-Fi -> handle_command (same pipeline as the wake word). run_me.sh
    # is unchanged. First run auto-generates + saves a token and prints the ready URL. See docs/http_setup.md.
    def remote_command(text, source="http"):
        """What the HTTP server calls. Holds turn_lock for the WHOLE turn (RLock) and PAUSES the wake mic
        around it so this turn's earcons/TTS can play -- the main thread is still in the wake listen
        (holding audio_lock), so without the pause say() would deadlock. handle_command re-enters
        turn_lock (RLock) fine. Concurrent requests serialise here, each re-asserting the pause."""
        with turn_lock:
            wake.pause_mic()
            driver.resume_sensing()  # an HTTP turn may drive -> ensure the ultrasonics are live for the gate
            snd.play("remote_ack")   # "got a remote command, on it now" -- the HTTP equivalent of ack
            try:
                return handle_command(text, source="http")
            finally:
                wake.resume_mic()
                driver.pause_sensing()   # back to idle -> quiesce again so the wake mic stays clean

    remote_srv = None
    if os.environ.get("ROBOT_HTTP_ENABLE", "1") == "1":
        try:
            port = _as_int(os.environ.get("ROBOT_HTTP_PORT"), 8080)
            token = env("ROBOT_HTTP_TOKEN", "").strip()
            if not token:
                import secrets
                token = secrets.token_urlsafe(16)
                _persist_env("ROBOT_HTTP_TOKEN", token)
                os.environ["ROBOT_HTTP_TOKEN"] = token
                log.info("generated ROBOT_HTTP_TOKEN and saved it to config/robot.env")
            remote_srv = remote.RemoteServer(port, token, on_command=remote_command, on_stop=on_stop)
            remote_srv.start()
            host = socket.gethostname()
            print(f">> Phone command endpoint READY:  http://{host}.local:{port}/speak")
            print(f"   token: {token}   (send as the X-Robot-Token header)  -- Android setup: docs/http_setup.md")
        except Exception as e:
            print(f"remote endpoint failed to start: {e}", file=sys.stderr)
            log.warning("remote endpoint failed to start: %s", e)
            remote_srv = None

    # Conversation follow-ups: after a wake, keep taking commands with NO wake word until the operator
    # goes quiet for ROBOT_FOLLOWUP_SILENCE_S -- so a back-and-forth doesn't need "hey jarvis" every turn.
    # Both keys have safe code defaults, so config/robot.env need not set them.
    followup_enable = os.environ.get("ROBOT_FOLLOWUP_ENABLE", "1") == "1"
    followup_silence_s = max(1, _as_int(os.environ.get("ROBOT_FOLLOWUP_SILENCE_S"), 12))
    log.info("conversation: follow-ups %s%s", "ON" if followup_enable else "OFF (wake word every turn)",
             f" (sleep after %ds of silence)" % followup_silence_s if followup_enable else "")

    # Battery-power watchdog: surfaces undervoltage/throttling (the silent cause of USB devices vanishing
    # mid-run on LiPo/UBEC power) as explicit log lines. No-op off-Pi / when ROBOT_POWER_MONITOR=0.
    _start_power_monitor()

    # Idle presentation. In CONCIERGE MODE the robot acts like a greeter: it sits AWAKE ('idle' = eyes open,
    # listening for the wake word) and, after followup_silence_s of nothing, DROWSES to the 'sleeping' face --
    # still listening the whole time (sleep is cosmetic). In CRAWLER MODE that drowse is OFF: behaviour is
    # UNCHANGED (stays on 'idle' until a chat ends), so normal driving is never affected. A dict so the drowse
    # Timer thread can flip the shared state; a timed-out chat sets it to 'sleeping' in either mode.
    concierge_mode = concierge.enabled()
    idle = {"face": "idle"}
    def _drowse():
        idle["face"] = "sleeping"
        fc.set_expression("sleeping")
    try:
        while True:
            fc.set_expression(idle["face"])
            # Concierge Mode ONLY: arm a one-shot drowse to 'sleeping' after the idle timeout (cancelled the
            # instant a trigger arrives). NEVER armed in crawler mode -> normal driving behaviour is untouched.
            drowse = None
            if concierge_mode and idle["face"] != "sleeping":
                drowse = threading.Timer(followup_silence_s, _drowse)
                drowse.daemon = True
                drowse.start()
            # IDLE: quiesce the ultrasonics so their software-timed threads don't starve the wake-word mic
            # while we wait (the "runs forever" fix). A wake / drive / HTTP command below resumes them.
            driver.pause_sensing()
            trigger = wait_for_trigger(cfg, wake, motion_event)
            if drowse is not None:
                drowse.cancel()   # a trigger arrived -> stop the pending drowse
            if trigger == "QUIT":
                driver.stop()
                print("Bye.")
                return
            if trigger == "FAILED":
                # MIC-03: the wake listener gave up after repeated faults (e.g. a wedged USB mic). Exit
                # NON-ZERO so systemd's Restart=on-failure relaunches a fresh process (a full ALSA/PortAudio
                # re-init from scratch -- the only reliable recovery). SystemExit isn't caught by the
                # KeyboardInterrupt handler, so the cleanup finally still neutralises the ESC + closes devices.
                driver.stop()
                log.error("wake listener failed permanently -- exiting 1 so systemd restarts the brain")
                sys.exit(1)
            if trigger in ("CANCELLED", "RETRY"):
                continue   # background voice-abort cancel, or MIC-03 transient retry -> just re-enter the wait
            if trigger == "MOTION":
                if halloween.enabled():
                    # Halloween Mode (Concierge sub-mode): the PIR MOTION wake IS the "someone's at the door"
                    # signal, so we spook on motion. We deliberately do NOT require a camera face-detection:
                    # trick-or-treaters wear MASKS / costumes that defeat face detection, so gating on it means
                    # a costumed kid never gets spooked (and the spooky face just flickers, then reverts -- the
                    # exact bug this replaces). Show a random spooky face and KEEP it up through the whole
                    # greeting (talk_expr/end_expr = the spooky state, so say() doesn't swap in the generic
                    # 'talking' mouth). Earcon + a random spooky phrase, then FALL THROUGH into the SAME
                    # conversation loop as concierge.
                    log.info("halloween: motion -> spook")
                    # Halloween has no face gate to arm a cooldown from, so arm the GREET one here: it used to
                    # ride on the PIR's old blanket 15s cooldown, and that is now a ~2s debounce. Without this
                    # a trick-or-treater standing at the door would be re-spooked every couple of seconds.
                    motion_wake.arm_cooldown(
                        _concierge_cooldown_s("ROBOT_CONCIERGE_GREET_COOLDOWN_S", 60), "spooked")
                    concierge_stats["greeted"] += 1
                    fc.set_expression(halloween.FACE_STATE)    # a random spooky face (held through the greeting)
                    snd.play_blocking("spook")                 # LOUD jump-scare stinger, played in FULL before
                                                               # the phrase -> the monster lands + lingers a beat
                    say(halloween.spooky_greeting(),
                        talk_expr=halloween.FACE_STATE, end_expr=halloween.FACE_STATE)
                else:
                    # Concierge motion wake -> STRICTLY LOCAL vision + RAG face pipeline, in TWO stages.
                    #
                    # STAGE A (cheap): one or two frames, detector only, "is anyone actually there?". The PIR
                    # is a liar -- a field log showed 3,741 of 3,786 motion wakes (98.8%) had nobody in front
                    # of the camera. Answering those with the full 15-frame recognition burst is what forced
                    # the old 15-second blind cooldown on the sensor, and THAT is what made Jarvis deaf to
                    # real visitors 85% of the time. One frame settles an empty room just as well.
                    #
                    # STAGE B (the recognition window) then runs only when Stage A saw somebody: keep
                    # capturing + matching for ROBOT_CONCIERGE_RECOGNIZE_S so one bad frame (pose/blur/light)
                    # isn't a missed greeting and the smile doesn't "flash". Three outcomes: recognised ->
                    # by name; a face was seen but not matched -> a generic greeting (still say hello
                    # warmly); lost them again -> silent (a cat / curtain / passer-by isn't greeted).
                    #
                    # Either way the throttle is applied AFTER we know what we saw (motion_wake.arm_cooldown),
                    # never before -- that inversion is the whole fix.
                    # Wake latency: how long between the visitor tripping the PIR and us deciding. This is the
                    # number to tune against -- "it feels slow" is unactionable, "1.4s, of which 0.9s was the
                    # probe" tells you exactly which knob to turn. 0 when the PIR isn't the trigger source.
                    _woke_at = getattr(motion_wake, "last_wake_at", 0.0) or 0.0
                    _since_wake = (lambda: (time.monotonic() - _woke_at) if _woke_at else 0.0)
                    # Seconds since the wake sensor last asserted. One reading, two windows: the face uses a
                    # short one, the retry loop a long one (see _face_tick below). inf when unsupported, so
                    # a source without presence_age() simply never keeps the smile up.
                    _presence_age = (lambda: getattr(motion_wake, "presence_age", lambda: float("inf"))())
                    grab = (lambda: cam.capture_jpeg() if getattr(cam, "available", False) else None)
                    # Smile the INSTANT motion fires (default), held through the whole look -- a greeter that
                    # waits to be sure before acknowledging you feels broken. The trade-off is new: the PIR is
                    # now answered ~2.4x more often than under the old blind cooldown, so on a chattering
                    # sensor an empty room gets a brief happy flash every few seconds all night. Set
                    # ROBOT_CONCIERGE_SMILE_ON_MOTION=0 to hold the smile until Stage A has actually seen
                    # somebody -- ~0.3s later, and then only for real visitors.
                    smile_now = os.environ.get("ROBOT_CONCIERGE_SMILE_ON_MOTION", "1") != "0"
                    # THE FACE FOLLOWS THE SENSOR, NOT THE LOOK. Presence is now sticky enough to keep
                    # hunting for a face well after the pin last pulsed, and holding one expression across
                    # the whole look meant a median 9.6s (max 15.6s) of smiling -- most of it at an empty
                    # hallway, because the visitor had already walked off. So the smile gets its OWN, much
                    # shorter window off the same reading: bridge the ~2.3-3s pulse gaps so it cannot
                    # flicker, then drop within ~0.5s of the sensor genuinely going quiet. The retry loop
                    # keeps running on the longer window regardless -- looking a little longer than we smile
                    # is free, and is what catches someone the instant before they leave.
                    _smile_hold_s = _concierge_smile_hold_s()

                    def _face_tick(_last=[None]):
                        want = "happy" if _presence_age() < _smile_hold_s else idle["face"]
                        if want != _last[0]:      # only redraw on CHANGE; the panel is serial and slow
                            _last[0] = want
                            fc.set_expression(want)
                    if smile_now:
                        fc.set_expression("happy")
                    # DROP A STALE QUEUED WAKE. Motion that fires DURING a look is held by the event and
                    # handled the moment that look ends -- so a long look is immediately followed by a
                    # second one for an edge that is already many seconds old. The field log shows it as a
                    # look whose pacing is impossible: 4 frames spread over 8.77s, against 0.42-0.64s per
                    # frame everywhere else. On screen it is the face dropping to sleeping and instantly
                    # smiling again at nobody. If the edge is old AND the sensor is no longer asserting,
                    # that visitor has been and gone; the one we just finished was theirs.
                    if (_woke_at and _since_wake() > _smile_hold_s
                            and _presence_age() >= _concierge_probe_max_s()):
                        log.debug("concierge: dropping a stale motion wake (%.1fs old, sensor quiet for "
                                  "%.1fs) -- it was queued behind the look that just finished",
                                  _since_wake(), _presence_age())
                        fc.set_expression(idle["face"])
                        continue
                    probe_tries = _concierge_probe_tries()
                    if probe_tries:
                        # still_present: keep looking while the sensor ITSELF still says someone is there,
                        # rather than stopping at a fixed frame budget that has to guess how long an
                        # approach takes. probe_tries stays the FLOOR (the output can fall while a visitor
                        # is still walking in) and _concierge_probe_max_s() the ceiling that stops a
                        # stuck-HIGH sensor pinning the camera. Both degrade: a source without is_active
                        # reads False and Stage A behaves exactly as it did before.
                        probe = concierge.presence_probe(
                            grab, face_identifier.detect, attempts=probe_tries,
                            interval_s=_concierge_probe_interval_s(),
                            still_present=(lambda: getattr(motion_wake, "is_active", False)),
                            max_s=_concierge_probe_max_s(), on_tick=_face_tick)
                        if not probe["present"]:
                            # Nobody there. This is the overwhelmingly common case (98.8% of wakes in the
                            # field log), so it logs at DEBUG -- at INFO it produced 3,741 lines a day and
                            # buried every line that mattered. The hourly summary below counts it instead.
                            motion_wake.arm_cooldown(
                                _concierge_cooldown_s("ROBOT_CONCIERGE_SILENT_COOLDOWN_S", 8), "silent")
                            concierge_stats["probe_silent"] += 1
                            if probe.get("capped"):
                                # We did NOT stop because the sensor said the room was empty -- it never
                                # released, and the safety watchdog fired. That is a hardware symptom, not a
                                # tuning problem, and it must not look like an ordinary early give-up or you
                                # will spend the evening raising the wrong number.
                                log.warning(
                                    "concierge: wake sensor STILL asserting after %.0fs and %d frames with "
                                    "no face -- stopped by the ROBOT_CONCIERGE_PROBE_MAX_S watchdog. The "
                                    "sensor is holding HIGH at something the camera cannot see: aimed at a "
                                    "heat source / sunlit window / the Pi's own exhaust, or a failed part. "
                                    "Check ~/detect-captures and where it is pointed -- raising the cap "
                                    "would only make the camera run longer at the same empty view.",
                                    _concierge_probe_max_s(), probe["attempts"])
                            log.debug("concierge: motion but nobody there (%d probe frame(s), %.2fs since "
                                      "the PIR fired) -> back to %r",
                                      probe["attempts"], _since_wake(), idle["face"])
                            _keep_capture(probe.get("jpeg"), "noface")
                            fc.set_expression(idle["face"])
                            continue
                        concierge_stats["probe_hit"] += 1
                    if not smile_now:
                        fc.set_expression("happy")   # deferred smile: somebody IS there, so greet the face now
                    # WHICHEVER LASTS LONGER WINS: run the fixed window, then keep going for as long as the
                    # wake sensor still says somebody is there. Stopping while the sensor is still asserting
                    # is the one outcome that is never right -- it is the robot concluding nobody is present
                    # while its own sensor disagrees. Bounded by _concierge_recognize_max_s().
                    res = concierge.recognize_within(
                        grab, face_identifier.identify, face_identifier.detect, _concierge_recognize_s(),
                        interval_s=_concierge_recognize_interval_s(),
                        need_hits=_concierge_face_hits(),
                        still_present=(lambda: getattr(motion_wake, "is_active", False)),
                        max_s=_concierge_recognize_max_s(), on_tick=_face_tick)
                    name = res["name"]
                    if not name and not res["face_seen"]:
                        # Stage A saw somebody but the window couldn't confirm them -- they walked past, or
                        # Stage A's box was a false positive. Go BACK to the state we were in (awake or
                        # asleep), NOT a forced sleep. (The smile lasted only as long as the look.)
                        # `face_hits` is in the message on purpose: 0 means the camera genuinely saw no face
                        # (a real false PIR trigger), while a NON-zero count below the ROBOT_CONCIERGE_FACE_HITS
                        # gate means we suppressed a probable detector fluke -- two very different things to chase.
                        motion_wake.arm_cooldown(
                            _concierge_cooldown_s("ROBOT_CONCIERGE_SILENT_COOLDOWN_S", 8), "silent")
                        concierge_stats["window_lost"] += 1
                        log.info("concierge: motion but no face in %d try(s) (%d face hit(s), need %d) "
                                 "-> back to %r (silent)",
                                 res["attempts"], res["face_hits"], _concierge_face_hits(), idle["face"])
                        _keep_capture(res.get("jpeg"), "noface")
                        fc.set_expression(idle["face"])
                        continue
                    # A person IS here -> greet, then FALL THROUGH into the conversation loop for follow-ups
                    # (say() transitions smile -> talking -> idle; sleeps after followup_silence_s).
                    # Hold the PIR off for the whole greeting + chat: the visitor is standing right in front
                    # of the sensor, so without this every wave of their arm re-triggers the greeting they
                    # are already in the middle of (the log shows the same person greeted twice, 30s apart).
                    motion_wake.arm_cooldown(
                        _concierge_cooldown_s("ROBOT_CONCIERGE_GREET_COOLDOWN_S", 60), "greeted")
                    concierge_stats["greeted"] += 1
                    if name:
                        # recognised: greet BY NAME. Use the stored PRONUNCIATION (a phonetic respelling) for
                        # the SPOKEN greeting when set (e.g. Ramesh -> "Ruh-mesh").
                        concierge_stats["by_name"] += 1
                        pron = mem.pronunciation_for(name)
                        log.info("concierge: recognised %r%s in %d try(s) (best %.3f) -> greeting "
                                 "[%.2fs from PIR to decision]",
                                 name, f" (say-as '{pron}')" if pron else "", res["attempts"],
                                 res["best_score"], _since_wake())
                        _keep_capture(res.get("jpeg"), "known", name)
                        snd.play("wake_ack")
                        say(concierge.greeting(name, pron))
                    else:
                        # present but unrecognised after the window -> a warm, name-less greeting (still chats).
                        log.info("concierge: person present but unrecognised after %d try(s) (%d face hit(s), "
                                 "best %.3f vs threshold %.2f) -> generic greeting "
                                 "[%.2fs from PIR to decision]", res["attempts"],
                                 res["face_hits"], res["best_score"], face_identifier.threshold, _since_wake())
                        _keep_capture(res.get("jpeg"), "unknown")
                        snd.play("wake_ack")
                        say(concierge.greeting_unknown())
            if trigger == "WAKE":
                driver.resume_sensing()    # the turn may drive/explore -> bring the ultrasonics back up
                fc.set_expression("inquisitive")   # curious "just woken" face, held through the first record
                snd.play("wake_ack")       # wake-ack: "yes?/hi" -- responding to being summoned, before recording
                # Snap + person-check IN PARALLEL with the record+transcribe below (so it's ~free on remote).
                if _person_check_enabled() and getattr(cam, "available", False):
                    person_ctx["note"] = ""
                    person_ctx["gen"] += 1   # P2-2: this check belongs to THIS wake only
                    threading.Thread(target=_wake_person_check, args=(person_ctx["gen"],),
                                     daemon=True).start()

            # --- Conversation loop: the wake OPENED it; now take follow-ups with NO wake word until
            #     followup_silence_s of quiet (or a capture error) drops us back to the wake word. The
            #     person-check + wake-ack above are wake-ONLY -- follow-ups just listen. ---
            last_voice = time.monotonic()
            first_turn = True   # hold the inquisitive wake face through the FIRST record; then show "listening"
            while True:
                if not first_turn:
                    fc.set_expression("listening")
                first_turn = False
                try:
                    text = listen_and_transcribe(cfg)
                except Exception as e:
                    print(f"listen: {e}", file=sys.stderr)
                    log.warning("listen failed: %s -- ending the conversation, back to the wake word", e)
                    break

                text = text.strip()
                if len(text) < 2 or is_noise(text):
                    print("(ignored: empty / noise)")
                    if not followup_enable:
                        break
                    silent_for = time.monotonic() - last_voice
                    if silent_for >= followup_silence_s:
                        log.info("conversation: %.0fs of silence -> sleeping (say the wake word to talk again)",
                                 silent_for)
                        fc.set_expression("sleeping")   # peaceful closed-eyes + Zzz
                        # The spoken goodbye ("Okay, chat later." / "Okay, see ya.") is OPTIONAL. It is right
                        # after a real conversation, and wrong for a hallway greeter that people walk past:
                        # there, the follow-up window almost always times out with nobody there, so the clip
                        # is a farewell to an empty corridor. It is also BLOCKING -- it holds audio_lock until
                        # it finishes, which is time the next arrival cannot be greeted in.
                        # ROBOT_CONCIERGE_GREET_ONLY=1 drops it: the greeting stays the only thing Jarvis says,
                        # and it goes quiet the moment the follow-up window expires. The face still changes to
                        # 'sleeping', so you can see it happen without hearing it.
                        if os.environ.get("ROBOT_CONCIERGE_GREET_ONLY", "0") == "1":
                            log.info("conversation: sleeping SILENTLY (ROBOT_CONCIERGE_GREET_ONLY=1)")
                        else:
                            snd.play_blocking("sleep")  # say goodbye FULLY before we re-enter the wake listen
                                                        # (which then holds audio_lock)
                        idle["face"] = "sleeping"       # stay "asleep" until the next wake / motion
                        # DISCARD any motion that fired DURING the conversation. It is almost always the
                        # person we were just talking to -- they stood in front of the sensor for the whole
                        # exchange -- so servicing it now makes Jarvis smile at the back of someone who has
                        # already walked off. A field log caught exactly that: a wake-word chat ran
                        # 09:14:52-09:15:32, the sensor tripped mid-conversation, and the queued wake was
                        # honoured the instant the chat ended -- "motion but nobody there, 30.54s since the
                        # PIR fired". The greet cooldown doesn't cover this case because it is only armed
                        # when the CONCIERGE greets; a conversation opened by the wake word never arms it.
                        # (A genuinely different visitor arriving mid-chat is not lost: they are still
                        # there, so the next edge -- or their next movement -- wakes it normally.)
                        if motion_event is not None and motion_event.is_set():
                            motion_event.clear()
                            log.debug("concierge: discarded a motion wake that arrived during the "
                                      "conversation (almost certainly the person we were talking to)")
                        motion_wake.arm_cooldown(
                            _concierge_cooldown_s("ROBOT_CONCIERGE_GREET_COOLDOWN_S", 60), "just-talked")
                        break
                    fc.set_expression("idle")   # nothing heard yet; keep waiting for a follow-up
                    continue

                print(f"Heard: {text!r}")
                snd.play("ack")                # command-ack: "got it/on it" -- accepted, AFTER the noise filter
                try:
                    handle_command(text, source="voice")   # thinking -> LLM intent -> action (shared w/ HTTP)
                except Exception as e:
                    # A single turn must NEVER crash the whole robot: log the FULL traceback (so we can see
                    # exactly what broke), stop the motors, tell the operator, and keep the conversation alive.
                    log.exception("voice turn crashed: %s", e)
                    driver.stop()
                    try:
                        say("Uh oh. Something went wrong on my end.")
                    except Exception:
                        pass
                last_voice = time.monotonic()
                if not followup_enable:
                    break
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        # A SECOND Ctrl-C (or a SIGTERM) must NOT abort this cleanup -- it neutralises the ESC and
        # closes the devices, so a double-tap can't be allowed to leave the motor live. Ignore those
        # signals for the duration; every step below is bounded (gpiozero's thread joins time out),
        # so making cleanup uninterruptible can't hang. (Without this, a 2nd Ctrl-C raises
        # KeyboardInterrupt -- a BaseException the per-closer `except Exception` does NOT catch.)
        # Uses the MODULE-level `import signal` (top of file). Do NOT `import signal` here: that would
        # make `signal` a local of main(), shadowing the global and breaking the nested run_behavior's
        # closure reference to it ("NameError: free variable 'signal' ... not associated with a value").
        for _sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(_sig, signal.SIG_IGN)
            except Exception:
                pass
        if remote_srv is not None:      # stop accepting phone commands before tearing devices down
            try:
                remote_srv.stop()
            except Exception:
                pass
        _close_devices()   # P2-3: shared with the early no-trigger return so the two can't drift apart


if __name__ == "__main__":
    main()
