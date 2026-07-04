# brain/wakeword.py -- the wake word (openWakeWord). LIVE trigger; ENTER survives only in --dry.
#
# Richard = Robotic Interactive Computing Hub Autonomous Response Device.
#
# Say the wake word and the robot wakes -> main.py plays an ack earcon, sets the face to listening,
# then runs the EXISTING 4s arecord + whisper command capture. In LIVE mode the wake word is the
# ONLY trigger (just 'q' quits over SSH); the old ENTER push-to-talk is GONE from live and survives
# ONLY in --dry (bench testing with no mic). Headless (scripts/robot.service / run_me.sh --detach)
# is wake-only by nature anyway -- there's no usable stdin there.
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
#   - Fail-safe: if openwakeword/sounddevice/the model are missing, available=False -- in LIVE that
#     means no trigger, so main.py prints why and exits (use --dry, which never needs the model).
#   - Dry run: a fake detector lets the whole turn be bench-tested with NO mic
#     (ENTER=talk, 'r'=simulate the wake word, or ROBOT_FAKE_WAKE=1 to auto-fire once).
#
# NOTE: openWakeWord needs its feature models (melspectrogram/embedding). The pinned 0.4.0 wheel
# BUNDLES them (and stock wake words) inside the package; 0.5+ would download them instead --
# install_stack.sh handles either. The "Richard" model itself is a small keyword classifier you
# train separately (NOT TTS voice cloning). A ready-made one ships at models/hey_richard.onnx (the
# ROBOT_WAKEWORD_MODEL default); swap it, or point at a bundled stock model
# (find brain/.venv -path "*openwakeword*" -name "*.onnx"). See docs/opnewakeword/.

import logging
import os
import sys
import threading
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
    """Always-listening wake-word trigger. wait() returns 'WAKE' | 'TALK' | 'QUIT' ('TALK' is the
    --dry ENTER only). available is False unless (dry-run) or (enabled AND model + libs loaded);
    in live, not-available means no trigger and main.py exits (no ENTER fallback)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = os.environ.get("ROBOT_WAKEWORD_ENABLE", "1") == "1"   # ON by default (matches config/robot.env + README)
        self.dry_run = dry_run_enabled()
        self.available = False
        self._model = None
        self._fake = False
        self._fake_auto = os.environ.get("ROBOT_FAKE_WAKE", "0") == "1"
        self._fired = False
        self.threshold = _envf("ROBOT_WAKEWORD_THRESHOLD", 0.5)
        self.trigger_level = max(1, _envi("ROBOT_WAKEWORD_TRIGGER_LEVEL", 1))
        self.cooldown_s = _envf("ROBOT_WAKEWORD_COOLDOWN_S", 2.0)
        # A non-voice turn (e.g. an HTTP command) needs the audio path WHILE we're listening. pause_mic()
        # sets _yield so the listen loop drops audio_lock + the mic (confirmed via _yielded); resume_mic()
        # clears it. Without this the listener holds audio_lock forever and the HTTP turn's TTS deadlocks.
        self._yield = threading.Event()
        self._yielded = threading.Event()
        self._cancel = threading.Event()   # cancel_listen() sets it -> a listen in progress returns 'CANCELLED'

        model = os.path.expanduser(
            os.environ.get("ROBOT_WAKEWORD_MODEL", "~/LocalAiRobot/models/hey_richard.onnx"))
        self._model_path = model
        self._label = os.path.splitext(os.path.basename(model))[0] or "wake word"
        self.label = self._label.replace("_", " ")   # what to SAY (main.py banner); from the model file
        dev = os.environ.get("ROBOT_WAKEWORD_DEVICE", "").strip()
        self._device = int(dev) if dev.isdigit() else (dev or None)
        # Mic input buffer. On a busy Pi the wake loop can be briefly starved of the GIL (the software-timed
        # gpiozero sensors + the 20 Hz control loop), so PortAudio drops mic samples -> OVERFLOW -> torn model
        # -> deaf. A BIGGER input buffer absorbs those stalls (audio is delayed a little, not dropped). 'high'
        # = the device's generous default; a float = seconds; 'low' = the old tight buffer.
        _lat = os.environ.get("ROBOT_WAKEWORD_LATENCY", "high").strip() or "high"
        try:
            self._latency = float(_lat)
        except ValueError:
            self._latency = _lat

        # Dry-run is ALWAYS interactive (bench testing with no mic): ENTER=talk, 'r'=simulate a wake,
        # 'q'=quit. This is the ONLY place the ENTER trigger survives -- live mode is wake-word-only.
        if self.dry_run:
            self._fake = True
            self.available = True
            log.info("wake word: DRY RUN (no mic). ENTER=talk, 'r'=simulate '%s'%s, q=quit.",
                     self._label, ", or ROBOT_FAKE_WAKE=1 to auto-fire once" if self._fake_auto else "")
            return
        if not self.enabled:
            log.info("wake word disabled (ROBOT_WAKEWORD_ENABLE=0) -- live mode has NO trigger; "
                     "enable it (and set ROBOT_WAKEWORD_MODEL), or run with --dry.")
            return
        # --- live: lazy-import so the module imports fine on a box without these deps ---
        try:
            import sounddevice  # noqa: F401
            import numpy         # noqa: F401
            from openwakeword.model import Model
        except Exception as e:
            log.warning("wake word unavailable (import failed: %s) -- live mode has no trigger", e)
            return
        if not os.path.exists(model):
            log.warning("wake word model not found: %s -- live mode has no trigger "
                        "(set ROBOT_WAKEWORD_MODEL, or run with --dry)", model)
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
            log.warning("wake word model load failed (%s) -- live mode has no trigger", last_err)
            self.available = False

    def wait(self, audio_lock):
        """Block until the operator initiates a turn. Returns 'WAKE' | 'TALK' | 'QUIT'.
        'TALK' comes ONLY from --dry's ENTER; live yields 'WAKE'/'QUIT' only."""
        if not self.available:
            return "QUIT"          # live with no wake word -> main.py exits before the loop; defensive
        if self._fake:
            return self._fake_wait()
        return self._listen(audio_lock)

    def close(self):
        pass  # no persistent resources; the stream lives only inside wait()

    def pause_mic(self, timeout=2.0):
        """Ask the live listen loop to RELEASE the mic + audio_lock so another turn (e.g. an HTTP command)
        can play TTS/earcons. Blocks until it confirms (or `timeout`). No-op in dry-run / when not
        available (there's no live stream holding the lock). Always pair with resume_mic()."""
        if not self.available or self._fake:
            return
        self._yielded.clear()
        self._yield.set()
        self._yielded.wait(timeout=timeout)

    def resume_mic(self):
        """Let the listen loop re-open the mic after a pause_mic()."""
        self._yield.clear()

    def cancel_listen(self):
        """Break a listen in progress (it returns 'CANCELLED') -- used to stop the background voice-abort
        listener when a behaviour ends. A fresh _listen() clears this flag at its start, so the normal
        wake loop is unaffected afterwards."""
        self._cancel.set()

    # ---------- internals ----------
    def _listen(self, audio_lock):
        import numpy as np
        import sounddevice as sd

        self._model.reset()
        self._cancel.clear()                    # fresh listen; cancel_listen() sets it to break us out
        time.sleep(max(0.0, self.cooldown_s))   # don't re-wake on the tail of the last reply
        watch_stdin = sys.stdin.isatty()         # headless (systemd) has no TTY -> wake-only
        hits = 0
        result = None
        # PortAudio/ALSA does NOT auto-resample like arecord's plughw: many mics (and the ALSA
        # 'default' device) can't open 16 kHz and fail with paInvalidSampleRate -9997. So capture
        # at a rate the device CAN open and resample each chunk down to the model's 16 kHz.
        rate = self._capture_rate(sd)
        chunk = _CHUNK if rate == _SAMPLE_RATE else int(round(rate * _CHUNK / _SAMPLE_RATE))
        # Per-listen tuning telemetry: track the best (highest) score and loudest chunk seen, and
        # surface them periodically. A user whose mic/voice/model is marginal can read these straight
        # from the robot log to pick a threshold (or spot a too-quiet mic) -- no separate test script.
        best_score = 0.0
        best_amp = 0
        next_report = time.time() + 15.0
        next_overflow_log = 0.0
        logged = False
        try:
            while result is None:
                if self._cancel.is_set():
                    result = "CANCELLED"
                    break
                # If another turn (e.g. an HTTP command) asked for the audio path, DROP the mic +
                # audio_lock and wait until it's done -- else the listener holds audio_lock forever and
                # that turn's TTS/earcons deadlock. (A real wake / 'q' / cancel exits this loop instead.)
                if self._yield.is_set():
                    self._yielded.set()
                    while self._yield.is_set() and not self._cancel.is_set():
                        time.sleep(0.05)
                    self._yielded.clear()
                    if self._cancel.is_set():
                        result = "CANCELLED"
                        break
                    self._model.reset()
                    hits = 0
                # Hold audio_lock while the stream is open: the wake mic must not be live while Piper or an
                # earcon plays (self-trigger defense). It's dropped above whenever another turn asks (yield).
                with audio_lock:
                    with sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=chunk,
                                        device=self._device, latency=self._latency) as stream:
                        if not logged:
                            # Log the resolved audio path once: "capture 44100->16000Hz resampled" vs the
                            # threshold/silence_floor is the first thing to check when a wake won't fire.
                            log.info("wake: listening for '%s' on device=%s (capture %dHz%s, chunk=%d, "
                                     "threshold=%.2f, silence_floor=%d)%s", self._label,
                                     self._device if self._device is not None else "default", rate,
                                     f"->{_SAMPLE_RATE}Hz resampled" if rate != _SAMPLE_RATE else "",
                                     chunk, self.threshold, _SILENCE_FLOOR,
                                     "" if watch_stdin else " [headless]")
                            logged = True
                        while True:
                            if self._cancel.is_set():
                                result = "CANCELLED"
                                break   # a behaviour ended -> stop the background voice-abort listener
                            if self._yield.is_set():
                                break   # drop audio_lock + the stream so the waiting turn can play
                            if watch_stdin:
                                import select
                                r, _, _ = select.select([sys.stdin], [], [], 0)
                                if r:
                                    line = sys.stdin.readline()
                                    if line == "":
                                        # EOF: the terminal went away (SSH logout under a detached run).
                                        # select() would report readable forever -- stop watching stdin.
                                        log.info("wake: stdin closed -- continuing wake-word-only")
                                        watch_stdin = False
                                        continue
                                    if line.strip().lower() == "q":
                                        result = "QUIT"      # 'q' still quits over SSH
                                        break
                                    # any other key (incl. ENTER) is ignored: live is wake-word-only
                            data, overflowed = stream.read(chunk)
                            if overflowed:
                                # PortAudio DROPPED input samples -- the loop couldn't read the mic in time
                                # (CPU/GIL contention from the control loop + sensors). The model's rolling
                                # feature buffer is now TORN, which collapses the wake score -- the classic
                                # "worked for a while, then went deaf". Reset so it rebuilds from clean audio,
                                # and skip this torn chunk. Repeated logs = the load is starving the mic read.
                                self._model.reset()
                                hits = 0
                                if time.time() >= next_overflow_log:
                                    log.warning("wake: mic input OVERFLOWED -- too busy to read audio in time; "
                                                "model reset. If this keeps logging, the wake word stays "
                                                "sluggish until the load eases.")
                                    next_overflow_log = time.time() + 10.0
                                continue
                            audio = np.frombuffer(data, dtype=np.int16)
                            if audio.ndim > 1:
                                audio = audio.flatten()
                            if rate != _SAMPLE_RATE:
                                audio = self._to_16k(np, audio, rate)
                            amp = int(np.max(np.abs(audio)))
                            if amp > best_amp:
                                best_amp = amp
                            if amp < _SILENCE_FLOOR:                 # CPU guard: skip silence
                                hits = 0
                            else:
                                scores = self._model.predict(audio)
                                score = max(scores.values()) if scores else 0.0
                                if score > best_score:
                                    best_score = score
                                if score >= self.threshold:
                                    hits += 1
                                    if hits >= self.trigger_level:
                                        log.info("wake: '%s' detected (score=%.2f)", self._label, score)
                                        result = "WAKE"
                                        break
                                else:
                                    hits = 0
                            # Periodic tuning read-out -- only for windows where someone actually spoke
                            # (so a silent room doesn't spam the log); resets each window.
                            if time.time() >= next_report:
                                if best_amp >= _SILENCE_FLOOR:
                                    log.info("wake: still listening -- best score=%.2f, loudest=%d over the "
                                             "last ~15s (threshold=%.2f). Lower ROBOT_WAKEWORD_THRESHOLD to "
                                             "wake more easily; raise it if it false-fires.",
                                             best_score, best_amp, self.threshold)
                                best_score = 0.0
                                best_amp = 0
                                next_report = time.time() + 15.0
        except Exception as e:
            log.warning("wake listen failed (%s) -- live has no ENTER fallback, stopping.", e)
            return "QUIT"
        time.sleep(0.2)   # let PortAudio/ALSA release the mic before arecord grabs it
        return result

    def _capture_rate(self, sd):
        """Pick a sample rate the input device can actually OPEN: 16 kHz if possible, else the
        device's own default, else the common 48/44.1 kHz. Anything != 16 kHz is resampled."""
        candidates = [_SAMPLE_RATE]
        try:
            candidates.append(int(sd.query_devices(self._device, "input")["default_samplerate"]))
        except Exception:
            pass
        candidates += [48000, 44100]
        for rate in dict.fromkeys(candidates):   # dedupe, keep order
            try:
                sd.check_input_settings(device=self._device, samplerate=rate,
                                        channels=1, dtype="int16")
                if rate != _SAMPLE_RATE:
                    log.info("wake: mic can't open 16 kHz -- capturing at %d Hz and resampling", rate)
                return rate
            except Exception:
                continue
        return _SAMPLE_RATE   # nothing validated -> let InputStream surface the real error

    @staticmethod
    def _to_16k(np, audio, rate):
        """Resample one int16 chunk from `rate` to 16 kHz (scipy ships with openwakeword)."""
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(_SAMPLE_RATE, rate)
        res = resample_poly(audio.astype(np.float32), _SAMPLE_RATE // g, rate // g)
        return np.clip(res, -32768, 32767).astype(np.int16)

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
        return "TALK"      # plain ENTER in --dry -> start a command capture (bench testing)
