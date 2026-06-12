# brain/wakeword.py -- optional "Richard" wake word (openWakeWord), in front of push-to-talk.
#
# Richard = Robotic Interactive Computing Hub Autonomous Response Device.
#
# Say "Richard" and the robot wakes -> main.py plays an ack earcon, sets the face to listening,
# then runs the EXISTING 4s arecord + whisper command capture. This REPLACES, but does not
# remove, the SSH ENTER push-to-talk: the CODE default is ROBOT_WAKEWORD_ENABLE=0 (plain ENTER),
# though the shipped config/robot.env turns it ON. Even when enabled, ENTER/q still work over a
# TTY. Crucially it also lets the robot run HEADLESS under scripts/robot.service / run_me.sh
# --detach, where there is no usable stdin for ENTER at all.
#
# The wake word ONLY starts capture. It is NOT the autonomy gate -- EXPLORE/FIND are still
# demoted to DRIVE unless the raw transcript contains an explore phrase (main.is_explore_command).
#
# Design choices for THIS project (vs the desktop reference):
#   - Continuous detection needs a persistent stream, so we use sounddevice (PortAudio), like the
#     reference, NOT per-chunk arecord (which would drop frames between captures and miss wakes).
#     The wake stream addresses the mic via PortAudio (ROBOT_WAKEWORD_DEVICE, default = system
#     default input); the COMMAND capture still uses arecord on ROBOT_MIC. Same physical mic, two
#     addressings -- documented in config/robot.env.
#   - Half-duplex: wait() holds main.py's audio_lock while the stream is open, so the wake mic is
#     never live while Piper/earcons play (self-trigger defense), and a cooldown runs first so we
#     don't re-wake on the tail of the previous reply.
#   - CPU guards from the reference: skip predict() below a silence floor, and (if the device
#     can't do 16 kHz) the caller would resample -- here we just request 16 kHz directly.
#   - Fail-safe: if openwakeword/sounddevice/the model are missing, available=False and main.py
#     transparently uses ENTER PTT (mirrors camera.py/driver.py degrading to fakes).
#   - Dry run: a fake detector lets the whole woken turn be bench-tested with NO mic
#     (type 'r' to simulate "Richard", or set ROBOT_FAKE_WAKE=1 to auto-fire once).
#
# NOTE: openWakeWord needs its feature models (melspectrogram/embedding). The pinned 0.4.0 wheel
# BUNDLES them (and stock wake words) inside the package; 0.5+ would download them instead --
# install_stack.sh handles either. The "Richard" model itself is a small keyword classifier you
# train separately (NOT TTS voice cloning); until then, point ROBOT_WAKEWORD_MODEL at a bundled
# stock model (find brain/.venv -path "*openwakeword*" -name "*.onnx") to prove the pipeline.

import logging
import os
import sys
import time

from driver import dry_run_enabled

log = logging.getLogger("robot.wake")

_SILENCE_FLOOR = 200      # skip predict() below this peak amplitude (reference: current_max > 200)
_SAMPLE_RATE = 16000      # openWakeWord's required rate
_CHUNK = 1280             # 80 ms frames at 16 kHz


def _envf(key, default):
    try:
        return float(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def _envi(key, default):
    try:
        return int(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return int(default)


class WakeWord:
    """Optional always-listening wake-word trigger. wait() returns 'WAKE' | 'PTT' | 'QUIT'.
    available is False unless enabled AND (dry-run, or the model + libs loaded) -- so the caller
    can transparently fall back to ENTER push-to-talk."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = os.environ.get("ROBOT_WAKEWORD_ENABLE", "0") == "1"
        self.dry_run = dry_run_enabled()
        self.available = False
        self._model = None
        self._fake = False
        self._fake_auto = os.environ.get("ROBOT_FAKE_WAKE", "0") == "1"
        self._fired = False
        self.threshold = _envf("ROBOT_WAKEWORD_THRESHOLD", 0.5)
        self.trigger_level = max(1, _envi("ROBOT_WAKEWORD_TRIGGER_LEVEL", 1))
        self.cooldown_s = _envf("ROBOT_WAKEWORD_COOLDOWN_S", 2.0)

        model = os.path.expanduser(
            os.environ.get("ROBOT_WAKEWORD_MODEL", "~/openwakeword/richard.onnx"))
        self._model_path = model
        self._label = os.path.splitext(os.path.basename(model))[0] or "wake word"
        dev = os.environ.get("ROBOT_WAKEWORD_DEVICE", "").strip()
        self._device = int(dev) if dev.isdigit() else (dev or None)

        if not self.enabled:
            log.info("wake word disabled (ROBOT_WAKEWORD_ENABLE=0) -- ENTER push-to-talk")
            return
        if self.dry_run:
            self._fake = True
            self.available = True
            log.info("wake word: DRY RUN (no mic). Type 'r' to simulate '%s'%s.",
                     self._label, ", or ROBOT_FAKE_WAKE=1 to auto-fire once" if self._fake_auto else "")
            return
        # --- live: lazy-import so the module imports fine on a box without these deps ---
        try:
            import sounddevice  # noqa: F401
            import numpy         # noqa: F401
            from openwakeword.model import Model
        except Exception as e:
            log.warning("wake word unavailable (import failed: %s) -- ENTER push-to-talk", e)
            return
        if not os.path.exists(model):
            log.warning("wake word model not found: %s -- ENTER push-to-talk "
                        "(set ROBOT_WAKEWORD_MODEL or train a 'Richard' model)", model)
            return
        # We install onnxruntime (NOT tflite-runtime -- it has no Python 3.13 / aarch64 wheel), and the
        # wake model is .onnx, so ask openWakeWord for the ONNX framework explicitly. Otherwise 0.4.x
        # defaults to tflite and fails to load on this stack. Try newest->oldest call signatures.
        attempts = (
            {"wakeword_models": [model], "inference_framework": "onnx"},
            {"wakeword_model_paths": [model], "inference_framework": "onnx"},
            {"wakeword_models": [model]},
            {"wakeword_model_paths": [model]},
        )
        last_err = None
        for kw in attempts:
            try:
                self._model = Model(**kw)
                break
            except TypeError as e:
                last_err = e            # wrong signature for this version -> try the next shape
                continue
            except Exception as e:
                last_err = e            # right signature, but the load itself failed -> stop trying
                break
        if self._model is not None:
            self.available = True
            log.info("wake word: loaded %s (threshold=%.2f, trigger_level=%d, cooldown=%.1fs, device=%s)",
                     model, self.threshold, self.trigger_level, self.cooldown_s,
                     self._device if self._device is not None else "default")
        else:
            log.warning("wake word model load failed (%s) -- ENTER push-to-talk", last_err)
            self.available = False

    def wait(self, audio_lock):
        """Block until the operator initiates a turn. Returns 'WAKE' | 'PTT' | 'QUIT'."""
        if not self.available:
            return self._ptt_fallback(sys.stdin.isatty())
        if self._fake:
            return self._fake_wait()
        return self._listen(audio_lock)

    def close(self):
        pass  # no persistent resources; the stream lives only inside wait()

    # ---------- internals ----------
    def _listen(self, audio_lock):
        import numpy as np
        import sounddevice as sd

        self._model.reset()
        time.sleep(max(0.0, self.cooldown_s))   # don't re-wake on the tail of the last reply
        watch_stdin = sys.stdin.isatty()         # headless (systemd) has no TTY -> wake-only
        hits = 0
        result = None
        try:
            # Hold audio_lock for the whole listen: the wake mic must not be live while Piper or
            # an earcon plays. Nothing else wants the lock at the top of a turn, so this is free.
            with audio_lock:
                with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=_CHUNK, device=self._device) as stream:
                    log.info("wake: listening for '%s' (ENTER=talk, q=quit)"
                             if watch_stdin else "wake: listening for '%s' (headless)", self._label)
                    while True:
                        if watch_stdin:
                            import select
                            r, _, _ = select.select([sys.stdin], [], [], 0)
                            if r:
                                line = sys.stdin.readline()
                                if line == "":
                                    # EOF: the terminal went away (SSH logout under a detached
                                    # run). select() would report readable forever -- if we
                                    # treated it as ENTER we'd hot-loop recording ambient noise.
                                    # Stop watching stdin; stay wake-word-only.
                                    log.info("wake: stdin closed -- continuing wake-word-only")
                                    watch_stdin = False
                                    continue
                                result = "QUIT" if line.strip().lower() == "q" else "PTT"
                                break
                        data, _ = stream.read(_CHUNK)
                        audio = np.frombuffer(data, dtype=np.int16)
                        if audio.ndim > 1:
                            audio = audio.flatten()
                        if int(np.max(np.abs(audio))) < _SILENCE_FLOOR:   # CPU guard: skip silence
                            hits = 0
                            continue
                        scores = self._model.predict(audio)
                        score = max(scores.values()) if scores else 0.0
                        if score >= self.threshold:
                            hits += 1
                            if hits >= self.trigger_level:
                                log.info("wake: '%s' detected (score=%.2f)", self._label, score)
                                result = "WAKE"
                                break
                        else:
                            hits = 0
        except Exception as e:
            log.warning("wake listen failed (%s) -- falling back to ENTER push-to-talk", e)
            return self._ptt_fallback(watch_stdin)
        time.sleep(0.2)   # let PortAudio/ALSA release the mic before arecord grabs it
        return result

    def _fake_wait(self):
        if self._fake_auto and not self._fired:
            self._fired = True
            log.info("wake (FAKE): auto-firing '%s' (ROBOT_FAKE_WAKE=1)", self._label)
            time.sleep(0.3)
            return "WAKE"
        try:
            line = input("\n[dry-run: no live mic]  type r=simulate wake  ·  ENTER=talk  ·  q=quit > ")
        except EOFError:
            return "QUIT"
        s = line.strip().lower()
        if s == "q":
            return "QUIT"
        if s in ("r", "richard", self._label.lower()):
            log.info("wake (FAKE): simulated '%s'", self._label)
            return "WAKE"
        return "PTT"

    def _ptt_fallback(self, watch_stdin):
        if watch_stdin:
            try:
                line = input("\n[ENTER]=talk  q=quit > ")
            except EOFError:
                return "QUIT"
            return "QUIT" if line.strip().lower() == "q" else "PTT"
        # No TTY and no working wake stream: avoid a tight loop, then cycle a capture.
        time.sleep(1.0)
        return "PTT"
