#!/usr/bin/env python3
# brain/main.py -- Local AI robot orchestrator (Raspberry Pi 5 "brain").
#
# Wake word (or ENTER in --dry) -> whisper.cpp (STT) -> Ollama gemma4:e4b-it-qat (intent JSON) -> Piper (TTS),
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
import signal
import subprocess
import sys
import threading
import time

import behavior
import inference
import vision
from camera import Camera
from driver import RobotDriver, VALID_DRIVE, VALID_STEER
from face import Face
from memory import Memory
from sounds import Sounds
from wakeword import WakeWord

log = logging.getLogger("robot.main")


def env(key, default):
    v = os.environ.get(key)
    return v if v else default


def load_config():
    home = os.path.expanduser("~")
    return {
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
    }


# Common whisper hallucinations on silence/noise -- ignore these outright.
WHISPER_NOISE = {
    "", ".", "you", "bye.", "thank you.",
    "thanks for watching.", "thank you for watching.",
    "[blank_audio]", "(silence)", "(buzzing)",
}


def is_noise(text):
    return text.strip().lower() in WHISPER_NOISE


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
    with audio_lock:
        # Capture arecord's stderr instead of /dev/null: when the mic device is missing/busy/renamed
        # we want ALSA's actual reason in the log, not a bare 'exit status 1'.
        rec = subprocess.run(
            ["arecord", "-D", cfg["mic"], "-d", cfg["record_secs"],
             "-f", "S16_LE", "-r", "16000", "-c", "1", wav],
            timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    if rec.returncode != 0:
        raise RuntimeError(f"arecord failed on ROBOT_MIC={cfg['mic']!r} (exit {rec.returncode}): "
                           f"{_last_line(rec.stderr)}  -- check `arecord -l` (card renamed/unplugged?)")
    rec_s = time.monotonic() - t0
    out = subprocess.run(
        [cfg["whisper_bin"], "-m", cfg["whisper_model"], "-f", wav, "-nt"],
        timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"whisper failed (exit {out.returncode}): {_last_line(out.stderr)}")
    text = out.stdout.strip()
    log.info("STT: %.1fs record + %.1fs transcribe -> %r", rec_s, time.monotonic() - t0 - rec_s, text[:60])
    return text


def wait_for_trigger(cfg, wake):
    """Block until the operator starts a turn. Returns 'WAKE' | 'TALK' | 'QUIT'.

    LIVE triggers ONLY on the wake word (+ 'q' to quit over SSH; headless needs no stdin). In --dry,
    ENTER starts a turn and 'r' simulates a wake, for bench testing with no mic. The trigger ONLY
    starts capture -- the EXPLORE/FIND autonomy gate is separate."""
    return wake.wait(audio_lock)


SYSTEM_PROMPT = (
    "You are the brain of a remote-control crawler car with Ackermann steering "
    "(one drive motor plus one steering servo -- it CANNOT strafe or spin in place). "
    "It has a forward camera and can look around. Convert the user's spoken input into "
    "ONE JSON object and nothing else.\n"
    "Keys:\n"
    '- "action": one of DRIVE, EXPLORE, FIND, STOP, SAY, REMEMBER.\n'
    "    DRIVE = ordinary movement (go / turn / reverse / stop).\n"
    "    EXPLORE = only if the user asks to look around or explore.\n"
    "    FIND = only if the user asks to find, go to, or move towards a named object.\n"
    "    STOP = stop moving now.\n"
    "    REMEMBER = the user is naming a person or thing for you to remember (e.g. 'this is my "
    "wife Shani', 'remember this is the kitchen'). The car will photograph what its camera sees "
    "and store it. Do NOT move.\n"
    "    SAY = anything else that is NOT a movement, look, or remember request -- a question, a "
    "greeting, chit-chat, or something the car cannot physically do. Answer in words; do NOT move.\n"
    '- "target": for FIND, the object to look for (e.g. "sofa"); for REMEMBER, the NAME to store '
    '(e.g. "Shani"); otherwise omit.\n'
    '- "note": for REMEMBER, the relationship or description the user gave (e.g. "my wife"); otherwise omit.\n'
    '- "drive": EXACTLY one of FORWARD, REVERSE, STOP (used when action is DRIVE).\n'
    '- "steer": EXACTLY one of LEFT, CENTER, RIGHT.\n'
    '- "speed": integer 0-100 (throttle; keep it low unless asked to go fast).\n'
    '- "duration_ms": integer 300-5000 (how long to move; use 0 only for STOP).\n'
    '- "reply": the spoken response. For SAY, a natural, helpful answer (max ~30 words); '
    "for movement, a brief confirmation (max 12 words). Plain text only.\n"
    "To turn, set steer LEFT or RIGHT together with drive FORWARD (the car must be moving to "
    "steer). Only choose DRIVE / EXPLORE / FIND when the user clearly wants the car to move or "
    "look; when in doubt, choose SAY and just answer.\n"
)


def _llm_timeout():
    try:
        return float(os.environ.get("ROBOT_LLM_TIMEOUT_S", "120") or 120)
    except ValueError:
        return 120.0


def query_ollama(cfg, user_text, context=""):
    prompt = SYSTEM_PROMPT
    if context:
        prompt += "Known surroundings: " + context + "\n"
    prompt += "User command: " + user_text
    # Routed through inference.ollama_generate: one shared model_lock serialises this with the
    # vision call (so they never hit Ollama concurrently), and keep_alive keeps the model warm.
    # It unwraps the {"response": ...} envelope and validates a dict; a raise is fail-safe here.
    return inference.ollama_generate(cfg, {
        "model": cfg["model"],
        "prompt": prompt,
        "format": "json",
    }, _llm_timeout())


def speak(cfg, text):
    text = (text or "").strip()
    if not text:
        return
    wav = os.path.join(cfg["tmpdir"], f"reply-{os.getpid()}-{int(time.time() * 1000)}.wav")
    t0 = time.monotonic()
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
            pb = subprocess.run(["aplay", wav], timeout=30,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if pb.returncode != 0:
            raise RuntimeError(f"aplay exit {pb.returncode}: {_last_line(pb.stderr)}  -- check ~/.asoundrc / `aplay -l`")
        log.info("TTS: spoke %d chars in %.1fs", len(text), time.monotonic() - t0)
    except Exception as e:
        print(f"tts: {e}", file=sys.stderr)
        log.warning("TTS failed: %s", e)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def main():
    # Hardware actions (intended PWM/GPIO/I2C) are logged by driver.py on the "robot.hw"
    # logger -- configure logging FIRST so the startup banner + mapping show on the console.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    driver = RobotDriver()
    cam = Camera()
    mem = Memory()
    fc = Face()
    snd = Sounds(audio_lock, cfg)
    wake = WakeWord(cfg)

    def say(text):
        text = (text or "").strip()
        if not text:
            return
        fc.set_expression("talking")
        speak(cfg, text)
        fc.set_expression("idle")

    def on_obstacle(distance_cm):
        print(f"Obstacle {distance_cm:.1f} cm ahead -> stopping")
        fc.set_expression("alarmed")
        try:
            mem.record("obstacle", notes=f"{distance_cm:.0f}cm ahead", distances=driver.distances())
        except Exception:
            pass
        snd.play("error")   # fire-and-forget earcon; never on the synchronous driver.stop() path
        # Run TTS off the control thread so the safety loop keeps ticking.
        threading.Thread(
            target=speak, args=(cfg, "Obstacle ahead. Stopping."), daemon=True
        ).start()

    def run_behavior(fn, *args):
        # Run an autonomous behaviour with a graceful abort: while it runs, Ctrl-C sets an abort
        # flag (honoured at the next look/step boundary) and returns to the prompt instead of
        # killing the program. Any behaviour-internal error is contained, not fatal, and we always
        # leave the ESC (Electronic Speed Controller) at neutral.
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
        try:
            fn(*args, aborted=ev.is_set)
        except Exception as e:
            print(f"behaviour: {e}", file=sys.stderr)
        finally:
            driver.stop()
            fc.set_expression("idle")
            if installed:
                try:
                    signal.signal(signal.SIGINT, prev)
                except Exception:
                    pass

    driver.on_obstacle = on_obstacle

    # LIVE needs a working wake word -- ENTER push-to-talk was removed from live (it survives only in
    # --dry). With no usable trigger, refuse to start rather than sit there dead.
    if not wake.dry_run and not wake.available:
        print("Live mode has no trigger: the wake word didn't load and ENTER push-to-talk is --dry "
              "only. Fix ROBOT_WAKEWORD_ENABLE/ROBOT_WAKEWORD_MODEL, or run with --dry.", file=sys.stderr)
        for c in (driver.stop, driver.close, cam.close, fc.close, mem.close, snd.close, wake.close):
            try:
                c()
            except Exception:
                pass
        return

    mode = "DRY RUN — no hardware will be driven" if driver.dry_run else "LIVE HARDWARE"
    if getattr(driver, "hw_failed", False):
        mode += " — DEGRADED (some hardware failed; see the HARDWARE FAILURE lines above)"
    print(f"Robot Brain online (RC crawler + vision + face).  [{mode}]")
    if wake.dry_run:
        print("Dry-run: press ENTER to talk, 'r' to simulate the wake word, 'q' to quit.")
    else:
        # The wake word is whatever model is loaded (e.g. the stock 'hey jarvis') -- print the real
        # label so the banner never lies about what to say.
        print(f"Wake word ON: say '{getattr(wake, 'label', 'the wake word')}' to wake me. 'q' quits (over SSH).")
    print("Autonomy (look around / find X) only runs if you say an explore phrase.")
    print("During autonomy, Ctrl-C aborts back to the prompt; at the prompt, 'q' or Ctrl-C quits.")

    # Warm the model NOW (best-effort) so the first command isn't a multi-minute cold load, then
    # an audible greeting confirms the stack is up -- useful when running headless under systemd.
    if os.environ.get("ROBOT_WARMUP", "1") == "1":
        fc.set_expression("warmup")
        ok = inference.warm_up(cfg, _llm_timeout())
        print(f"Model warmup: {'ready' if ok else 'skipped (will cold-load on first command)'}")
    snd.play("greeting")

    try:
        while True:
            fc.set_expression("idle")
            trigger = wait_for_trigger(cfg, wake)
            if trigger == "QUIT":
                driver.stop()
                print("Bye.")
                return
            if trigger == "WAKE":
                snd.play("wake_ack")       # wake-ack: "yes?/hi" -- responding to being summoned, before recording

            fc.set_expression("listening")
            try:
                text = listen_and_transcribe(cfg)
            except Exception as e:
                print(f"listen: {e}", file=sys.stderr)
                log.warning("listen failed: %s", e)
                continue

            text = text.strip()
            if len(text) < 2 or is_noise(text):
                print("(ignored: empty / noise)")
                continue
            print(f"Heard: {text!r}")
            snd.play("ack")                # command-ack: "got it/on it" -- command accepted, AFTER the noise filter

            fc.set_expression("thinking")
            snd.start_thinking()           # proof-of-life cue while the (slow) model runs
            t_think = time.monotonic()
            try:
                resp = query_ollama(cfg, text, context=mem.prompt_context())
            except Exception as e:
                snd.stop_thinking()
                snd.play("error")
                print(f"llm: {e}", file=sys.stderr)
                log.warning("llm failed after %.1fs: %s", time.monotonic() - t_think, e)
                driver.stop()  # fail safe
                continue
            snd.stop_thinking()            # stop BEFORE any say() so it doesn't fight TTS
            log.info("think: model replied in %.1fs", time.monotonic() - t_think)

            action = str(resp.get("action", "DRIVE")).upper()
            target = resp.get("target")
            reply = resp.get("reply", "")

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
                    snd.start_thinking()
                    desc = vision.describe(cfg, jpeg).get("description", "")
                    snd.stop_thinking()
                img_path = mem.save_photo(jpeg, name)
                mem.remember_fact(name, note, desc, img_path)
                say(reply or f"Okay, I'll remember {name}.")
            elif action == "FIND":
                print(f"action=FIND target={target!r}")
                say(reply)
                run_behavior(behavior.run_find, cfg, driver, cam, mem, fc, say, snd, str(target))
            elif action == "EXPLORE":
                print("action=EXPLORE")
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
                    if speed < 5:      # a model 0/blank shouldn't mean "don't move"
                        speed = 40
                    if dur < 300:      # clamp tiny/zero durations up to a usable move
                        dur = 1500
                print(f"action=DRIVE drive={drive} steer={steer} speed={speed} dur={dur}ms reply={reply!r}")
                driver.command(drive, steer, speed, dur)  # non-blocking; motion in driver thread
                say(reply)                                 # synchronous; returns after playback
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
        for closer in (driver.stop, driver.close, cam.close, fc.close, mem.close, snd.close, wake.close):
            try:
                closer()
            except Exception:
                pass


if __name__ == "__main__":
    main()
