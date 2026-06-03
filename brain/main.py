#!/usr/bin/env python3
# brain/main.py -- Local AI robot orchestrator (Raspberry Pi 5 "brain").
#
# Push-to-talk -> whisper.cpp (STT) -> Ollama gemma3:4b (intent JSON) -> Piper (TTS),
# driving an RC crawler (ESC throttle + steering servo) through brain/driver.py.
#
# Design carried over from the v1 review:
#   - No shell: Piper gets the reply text via stdin (an LLM reply can't inject commands).
#   - Half-duplex audio: a single lock means the mic is never open while the speaker talks
#     (no self-listening feedback loop) and two aplay processes never fight the device.
#   - Timeouts on every subprocess + the Ollama HTTP call, so nothing can wedge the robot.
#   - The Pi whitelists actions and clamps speed/duration; unknown -> STOP (fail-safe).
#   - Motion is handed to driver.py, whose control thread enforces the software failsafe.

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

from driver import RobotDriver, VALID_DRIVE, VALID_STEER


def env(key, default):
    v = os.environ.get(key)
    return v if v else default


def load_config():
    home = os.path.expanduser("~")
    return {
        "ollama_url": env("ROBOT_OLLAMA_URL", "http://localhost:11434/api/generate"),
        "model": env("ROBOT_MODEL", "gemma3:4b"),
        "whisper_bin": env("ROBOT_WHISPER_BIN", os.path.join(home, "whisper.cpp/build/bin/whisper-cli")),
        "whisper_model": env("ROBOT_WHISPER_MODEL", os.path.join(home, "whisper.cpp/models/ggml-base.en.bin")),
        "piper_bin": env("ROBOT_PIPER_BIN", os.path.join(home, "piper/piper/piper")),
        "piper_voice": env("ROBOT_PIPER_VOICE", os.path.join(home, "piper/en_US-lessac-medium.onnx")),
        "mic": env("ROBOT_MIC", "plughw:1,0"),
        "record_secs": env("ROBOT_RECORD_SECS", "4"),
        "tmpdir": env("ROBOT_TMPDIR", "/dev/shm"),
    }


# Common whisper hallucinations on silence/noise -- ignore these outright.
WHISPER_NOISE = {
    "", ".", "you", "bye.", "thank you.",
    "thanks for watching.", "thank you for watching.",
    "[blank_audio]", "(silence)", "(buzzing)",
}


def is_noise(text):
    return text.strip().lower() in WHISPER_NOISE


# audio_lock makes the physical audio path half-duplex: at most one of {record, playback}
# at a time. Kills the self-listening feedback loop AND concurrent-aplay garbling.
audio_lock = threading.Lock()


def listen_and_transcribe(cfg):
    wav = os.path.join(cfg["tmpdir"], "command.wav")
    with audio_lock:
        subprocess.run(
            ["arecord", "-D", cfg["mic"], "-d", cfg["record_secs"],
             "-f", "S16_LE", "-r", "16000", "-c", "1", wav],
            check=True, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    out = subprocess.run(
        [cfg["whisper_bin"], "-m", cfg["whisper_model"], "-f", wav, "-nt"],
        check=True, timeout=30, capture_output=True, text=True,
    )
    return out.stdout.strip()


SYSTEM_PROMPT = (
    "You are the brain of a remote-control crawler car with Ackermann steering "
    "(one drive motor plus one steering servo -- it CANNOT strafe or spin in place). "
    "Convert the user's spoken command into ONE JSON object and nothing else.\n"
    "Keys:\n"
    '- "drive": EXACTLY one of FORWARD, REVERSE, STOP.\n'
    '- "steer": EXACTLY one of LEFT, CENTER, RIGHT.\n'
    '- "speed": integer 0-100 (throttle; keep it low unless asked to go fast).\n'
    '- "duration_ms": integer 300-5000 (how long to move; use 0 only for STOP).\n'
    '- "reply": a short spoken response, max 12 words, plain text only.\n'
    "To turn, set steer LEFT or RIGHT together with drive FORWARD (the car must be "
    "moving to steer). If the command is unclear or unsafe, use drive STOP, steer "
    "CENTER, and say why in reply.\n"
    "User command: "
)


def query_ollama(cfg, user_text):
    payload = json.dumps({
        "model": cfg["model"],
        "prompt": SYSTEM_PROMPT + user_text,
        "stream": False,
        "format": "json",
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["ollama_url"], data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    # Ollama wraps the model output in {"response": "<json string>"}.
    envelope = json.loads(raw)
    response = envelope.get("response")
    if not response:
        raise ValueError(f"ollama returned no response (raw: {raw[:200]!r})")
    obj = json.loads(response)
    if not isinstance(obj, dict):
        # JSON mode guarantees valid JSON, not a JSON *object* -- guard so a list/string
        # can't crash the loop later at resp.get(...). Callers treat the raise as fail-safe.
        raise ValueError(f"model did not return a JSON object: {response[:200]!r}")
    return obj


def speak(cfg, text):
    text = (text or "").strip()
    if not text:
        return
    wav = os.path.join(cfg["tmpdir"], f"reply-{os.getpid()}-{int(time.time() * 1000)}.wav")
    try:
        # Synthesize: pipe text via stdin (no shell -> no injection).
        subprocess.run(
            [cfg["piper_bin"], "-m", cfg["piper_voice"], "--output_file", wav],
            input=text.encode("utf-8"), check=True, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Play back under the audio lock (serialized, never overlaps recording).
        with audio_lock:
            subprocess.run(["aplay", wav], timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"tts: {e}", file=sys.stderr)
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
    cfg = load_config()
    driver = RobotDriver()

    def on_obstacle(distance_cm):
        print(f"Obstacle {distance_cm:.1f} cm ahead -> stopping")
        # Run TTS off the control thread so the safety loop keeps ticking.
        threading.Thread(
            target=speak, args=(cfg, "Obstacle ahead. Stopping."), daemon=True
        ).start()

    driver.on_obstacle = on_obstacle

    print("Robot Brain online (RC crawler).")
    print("Push-to-talk: press ENTER, speak after the prompt, then wait.")
    print("Type 'q' then ENTER to quit.")

    try:
        while True:
            line = input("\n[ENTER]=talk  q=quit > ")
            if line.strip().lower() == "q":
                driver.stop()
                print("Bye.")
                return

            try:
                text = listen_and_transcribe(cfg)
            except Exception as e:
                print(f"listen: {e}", file=sys.stderr)
                continue

            text = text.strip()
            if len(text) < 2 or is_noise(text):
                print("(ignored: empty / noise)")
                continue
            print(f"Heard: {text!r}")

            try:
                resp = query_ollama(cfg, text)
            except Exception as e:
                print(f"llm: {e}", file=sys.stderr)
                driver.stop()  # fail safe
                continue

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

            reply = resp.get("reply", "")
            print(f"drive={drive} steer={steer} speed={speed} dur={dur}ms reply={reply!r}")

            driver.command(drive, steer, speed, dur)  # non-blocking; motion in driver thread
            speak(cfg, reply)                          # synchronous; returns after playback
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        driver.close()


if __name__ == "__main__":
    main()
