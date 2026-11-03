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
# install_stack_pi5.sh handles either. The "Richard" model itself is a small keyword classifier you
# train separately (NOT TTS voice cloning). A ready-made one ships at models/hey_richard.onnx (the
# ROBOT_WAKEWORD_MODEL default); swap it, or point at a bundled stock model
# (find brain/.venv -path "*openwakeword*" -name "*.onnx"). See docs/openwakeword/.

import logging
import os
import queue
import select
import sys
import threading
import time
from math import gcd

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
        # Self-heal: if the mic OVERFLOWS continuously for this long, model.reset() alone isn't clearing it
        # -- a PortAudio InputStream can wedge into an overflow spiral (the classic "ran fine for a while,
        # then went permanently deaf"). When that persists, tear the stream DOWN and reopen a fresh one (the
        # HTTP-yield path already reopens cleanly, so this reuses a proven-safe rebuild). 0 disables it.
        self.stream_reset_s = _envf("ROBOT_WAKEWORD_STREAM_RESET_S", 15.0)
        # Health: log a DEGRADED warning when this %% of mic chunks get dropped in a ~15s window -- the
        # early-warning that the predict loop can't keep up (before the wake word gets sluggish).
        self.degraded_pct = _envf("ROBOT_WAKEWORD_DEGRADED_PCT", 20.0)
        # Decouple mic capture from predict(): a PortAudio callback drains the mic into a bounded queue
        # (~q_max chunks of jitter buffer, chunk = 80ms) while this loop predicts. Bigger = absorbs longer
        # predict spikes before a chunk is dropped, at the cost of a little worst-case wake latency.
        self._q_max = max(4, _envi("ROBOT_WAKEWORD_QUEUE_MAX", 32))
        # MIC-02/03 recovery: a raised listen error retries (RETRY) up to max_errors, then FAILED (-> main
        # exits 1 -> systemd restarts). A dead stream is rebuilt in-process (PortAudio re-init) and, if no
        # clean audio returns within rebuild_max_s, also FAILED -- so the mic can never wedge deaf forever.
        self._listen_errors = 0
        self.max_listen_errors = max(1, _envi("ROBOT_WAKEWORD_MAX_ERRORS", 10))
        self.rebuild_max_s = _envf("ROBOT_WAKEWORD_REBUILD_MAX_S", 60.0)
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
        listener when a behaviour ends, and by the PIR callback so a motion wake pre-empts the wake word.

        A cancel that arrives BETWEEN listens is not lost: the next _listen() sees the flag still set and
        returns 'CANCELLED' immediately rather than clearing it (see the comment there). That is what makes
        a single-pulse motion arrival survive the gap while the robot is between listens."""
        self._cancel.set()

    # ---------- internals ----------
    def _listen(self, audio_lock):
        import numpy as np
        import sounddevice as sd

        self._model.reset()
        # HONOUR a cancel that arrived BEFORE this listen started, instead of discarding it.
        #
        # cancel_listen() only interrupts a listen that is ALREADY RUNNING, and this clear used to throw away
        # anything that landed in the gap between the caller deciding to listen and us getting here (model
        # reset + two imports). For the PIR that gap is a lost visitor: the callback sets motion_event AND
        # calls cancel_listen(), the cancel is cleared here, and the listen then blocks for minutes with
        # motion_event set but nobody looking at it. If the operator eventually says the wake word,
        # wait_for_trigger clears the flag and reports WAKE -- the motion is consumed with no trace at all.
        # A moving visitor self-heals (their next edge, >2s later, cancels the now-running listen); a
        # single-pulse arrival is simply lost.
        #
        # Returning CANCELLED here hands the decision back to wait_for_trigger, which already knows how to
        # read it: motion_event set -> MOTION, not set -> the main loop just re-enters the wait. The
        # worst case is therefore one spare loop iteration after a STALE cancel (e.g. a behaviour's
        # voice-abort listener shutting down), which costs nothing and self-corrects immediately.
        if self._cancel.is_set():
            self._cancel.clear()
            return "CANCELLED"
        self._cancel.clear()                                 # fresh listen; cancel_listen() sets it to break us out
        if self._cancel.wait(max(0.0, self.cooldown_s)):     # cooldown, but honour cancel_listen()
            return "CANCELLED"                               # behaviour ended mid-cooldown -> stop promptly
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
        # Health telemetry: how many mic reads OVERFLOWED (were dropped, tearing the model) vs total, in
        # the current ~15s window + cumulatively. A high overflow % is the signature of "worked for a few
        # minutes then went sluggish/deaf" -- surfaced every window so it's DETECTABLE in the log (the whole
        # point: a robot that must run 'forever' can't silently degrade). See the HEALTH/DEGRADED lines below.
        ov_window = 0        # dropped chunks this window
        rd_window = 0        # total chunks consumed this window
        ov_total = 0         # cumulative dropped chunks since this listen began
        # Deep diagnostics (per ~15s window) -- to find the ACTUAL wall instead of guessing: is predict()
        # over the 80ms budget? is the drop a PortAudio starve (CPU maxed) or the consumer lagging? is the
        # queue pinned full? is the whole Pi oversubscribed (loadavg)? See the expanded HEALTH line below.
        pa_drops = 0         # drops flagged by PortAudio input_overflow (callback itself starved -> CPU maxed)
        q_drops = 0          # drops from a FULL queue (this consumer loop can't keep up)
        predict_ms_sum = 0.0; predict_ct = 0; predict_ms_max = 0.0
        resample_ms_sum = 0.0; resample_ct = 0
        skipped = 0          # chunks below the silence floor (predict skipped)
        qdepth_max = 0
        empty_since = 0.0    # first time get() came back empty in a stall (0 = not stalling)
        consec_drops = 0     # MIC-01: consecutive dropped chunks -- a SINGLE drop is bridged, not reset
        drop_reset_n = max(1, _envi("ROBOT_WAKEWORD_DROP_RESET_N", 3))
        rebuilding = False   # MIC-02: last stream (re)open followed a stream-death -> re-init PortAudio first
        rebuild_since = 0.0  # MIC-02: when the current dead-mic outage began (0 = healthy)
        rebuild_backoff = 1.0
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
                # MIC-02: this iteration follows a STREAM DEATH. Re-initialize PortAudio so a re-plugged /
                # re-enumerated USB mic is rediscovered (sounddevice caches the device table; the reopen
                # against a stale/wedged device is exactly the ~163 s hang seen in the field). Back off
                # between attempts, and if no clean audio has returned within rebuild_max_s, give up with
                # FAILED -> main exits 1 (cleanly neutralising the ESC) -> systemd restarts -- the only
                # reliable recovery from a wedged USB audio stack, and far better than wedging deaf forever.
                if rebuilding:
                    if rebuild_since == 0.0:
                        rebuild_since = time.time()
                    down = time.time() - rebuild_since
                    if self.rebuild_max_s > 0 and down >= self.rebuild_max_s:
                        log.critical("wake: mic dead for %.0fs -- giving up (FAILED, exit for restart).", down)
                        return "FAILED"
                    try:
                        sd._terminate(); time.sleep(0.5); sd._initialize()   # force device re-enumeration
                    except Exception as e:
                        log.warning("wake: PortAudio re-init failed (%s)", e)
                    if self._cancel.wait(rebuild_backoff):
                        return "CANCELLED"
                    rebuild_backoff = min(rebuild_backoff * 2, 8.0)
                # Hold audio_lock while the stream is open: the wake mic must not be live while Piper or an
                # earcon plays (self-trigger defense). It's dropped above whenever another turn asks (yield).
                with audio_lock:
                    # Decouple mic READ from predict(): a PortAudio CALLBACK (its own high-priority thread)
                    # drains the mic into audio_q; THIS loop only consumes + predicts. So a slow predict can
                    # no longer back PortAudio's buffer up -> the read-overflow "went deaf" failure is gone,
                    # and the queue absorbs predict jitter with NO fixed added latency. If the consumer truly
                    # can't keep up for ~q_max chunks, the callback drops the OLDEST chunk and flags a tear
                    # (a clean, chosen drop) instead of PortAudio silently shredding the buffer.
                    audio_q = queue.Queue(maxsize=self._q_max)

                    def _cb(indata, frames, tinfo, status):
                        # Runs in PortAudio's thread -- keep it TINY (copy + enqueue) so the mic stays drained.
                        # drop flag: 0 = clean, 1 = PortAudio dropped before us (callback starved = CPU maxed),
                        # 2 = our queue was full (this consumer loop is behind). Distinguishing them is the
                        # whole point of this instrumentation -- they need different fixes.
                        flag = 1 if status.input_overflow else 0
                        try:
                            audio_q.put_nowait((indata[:, 0].copy(), flag))
                        except queue.Full:
                            try:
                                audio_q.get_nowait()      # consumer behind -> drop oldest, mark this one torn
                            except queue.Empty:
                                pass
                            try:
                                audio_q.put_nowait((indata[:, 0].copy(), 2))
                            except queue.Full:
                                pass

                    with sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=chunk,
                                        device=self._device, latency=self._latency, callback=_cb):
                        if not logged:
                            # Log the resolved audio path once: "capture 44100->16000Hz resampled" vs the
                            # threshold/silence_floor is the first thing to check when a wake won't fire.
                            log.info("wake: listening for '%s' on device=%s (capture %dHz%s, chunk=%d, "
                                     "threshold=%.2f, silence_floor=%d, callback+queue<=%d)%s", self._label,
                                     self._device if self._device is not None else "default", rate,
                                     f"->{_SAMPLE_RATE}Hz resampled" if rate != _SAMPLE_RATE else "",
                                     chunk, self.threshold, _SILENCE_FLOOR, self._q_max,
                                     "" if watch_stdin else " [headless]")
                            logged = True
                        last_clean = time.time()   # for the persistent-drop stream-rebuild watchdog
                        while True:
                            if self._cancel.is_set():
                                result = "CANCELLED"
                                break   # a behaviour ended -> stop the background voice-abort listener
                            if self._yield.is_set():
                                break   # drop audio_lock + the stream so the waiting turn can play
                            if watch_stdin:
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
                            try:
                                audio, drop = audio_q.get(timeout=0.5)
                            except queue.Empty:
                                # No audio delivered -> the callback isn't firing. A brief gap is normal; a
                                # SUSTAINED one means PortAudio/the stream is dead (a prime "went unresponsive"
                                # suspect). Detect it and self-heal by reopening the stream.
                                if empty_since == 0.0:
                                    empty_since = time.time()
                                elif time.time() - empty_since >= 3.0:
                                    log.warning("wake: NO audio from the mic for %.0fs -- the capture callback "
                                                "stopped (stream dead); rebuilding PortAudio.",
                                                time.time() - empty_since)
                                    rebuilding = True   # MIC-02: re-init PortAudio before the reopen
                                    break
                                continue
                            empty_since = 0.0
                            qdepth_max = max(qdepth_max, audio_q.qsize())
                            rd_window += 1
                            if drop:
                                ov_window += 1
                                ov_total += 1
                                if drop == 1:
                                    pa_drops += 1
                                else:
                                    q_drops += 1
                                consec_drops += 1
                                # MIC-01: a SINGLE missing 80ms chunk is BRIDGED with one chunk of model-rate
                                # silence (keeps openWakeWord's rolling mel/embedding buffer time-aligned),
                                # NOT reset. A full reset flushes the whole phrase context; at the ~7% drop
                                # rate a cheap USB mic shows, a reset-every-drop makes "hey jarvis" unscorable
                                # (field log: loud speech, best score 0.00). Only a real GAP (drop_reset_n
                                # consecutive drops) or a queue-full drop (a larger discarded backlog, drop==2)
                                # tears the buffer enough to warrant a reset.
                                if drop == 2 or consec_drops >= drop_reset_n:
                                    self._model.reset()      # a real gap -> rebuild from contiguous audio
                                    hits = 0
                                else:
                                    self._model.predict(np.zeros(_CHUNK, dtype=np.int16))   # 16kHz model rate
                                if time.time() >= next_overflow_log:
                                    log.warning("wake: mic chunk DROPPED (%s) -- model reset. See 'wake HEALTH' "
                                                "for predict-ms / load / queue depth.",
                                                "PortAudio starved" if drop == 1 else "queue full")
                                    next_overflow_log = time.time() + 10.0
                                # Self-heal: if NO clean chunk has been consumed for stream_reset_s, the stream
                                # itself is wedged (reset can't fix that). Break to the outer loop, which closes
                                # this InputStream and opens a fresh one -- recovers a permanently-stuck mic.
                                if self.stream_reset_s > 0 and (time.time() - last_clean) >= self.stream_reset_s:
                                    log.warning("wake: rebuilding the mic stream after %.0fs with no clean "
                                                "audio (self-heal -- reopening PortAudio).",
                                                time.time() - last_clean)
                                    rebuilding = True   # MIC-02: re-init PortAudio before the reopen
                                    break
                                continue
                            last_clean = time.time()   # a clean chunk landed -> reset the rebuild watchdog
                            consec_drops = 0           # MIC-01: contiguous audio resumed
                            self._listen_errors = 0    # MIC-03: clean audio proves the listener is healthy
                            if rebuilding:             # MIC-02: recovered from a stream death -> announce + clear
                                log.warning("wake: mic stream reopened after %.1fs",
                                            time.time() - (rebuild_since or last_clean))
                                rebuilding = False
                                rebuild_since = 0.0
                                rebuild_backoff = 1.0
                            if audio.ndim > 1:
                                audio = audio.flatten()
                            if rate != _SAMPLE_RATE:
                                _t0 = time.perf_counter()
                                audio = self._to_16k(np, audio, rate)
                                resample_ms_sum += (time.perf_counter() - _t0) * 1000.0
                                resample_ct += 1
                            amp = int(np.max(np.abs(audio)))
                            if amp > best_amp:
                                best_amp = amp
                            if amp < _SILENCE_FLOOR:                 # CPU guard: skip silence
                                hits = 0
                                skipped += 1
                            else:
                                _t0 = time.perf_counter()
                                scores = self._model.predict(audio)
                                _pm = (time.perf_counter() - _t0) * 1000.0
                                predict_ms_sum += _pm; predict_ct += 1
                                if _pm > predict_ms_max:
                                    predict_ms_max = _pm
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
                            # Periodic HEALTH read-out (every ~15s). ALWAYS logged -- a robot that must run
                            # "forever" can't degrade silently, so this makes the drop signature (dropped %)
                            # visible in the log even in a quiet room. The threshold hint is only added when
                            # someone actually spoke (so a silent room isn't nagged).
                            if time.time() >= next_report:
                                pct = (100.0 * ov_window / rd_window) if rd_window else 0.0
                                spoke = best_amp >= _SILENCE_FLOOR
                                p_avg = (predict_ms_sum / predict_ct) if predict_ct else 0.0
                                r_avg = (resample_ms_sum / resample_ct) if resample_ct else 0.0
                                try:
                                    load1 = os.getloadavg()[0]
                                except Exception:
                                    load1 = -1.0
                                hint = ((" (threshold=%.2f -- lower ROBOT_WAKEWORD_THRESHOLD to wake more "
                                         "easily, raise it if it false-fires)") % self.threshold) if spoke else ""
                                # ONE rich diagnostic line: predict-ms is the smoking gun (avg > 80ms = predict
                                # itself is the wall); pa vs queue drops say WHERE it's lost; load1 (vs 4 cores)
                                # says if the whole Pi is oversubscribed (e.g. the always-on camera pipeline).
                                log.info("wake HEALTH: dropped %d/%d (%.0f%%: %dq %dpa) | predict avg %.0fms "
                                         "max %.0fms n=%d skip=%d | resample %.1fms | qdepth max %d/%d | "
                                         "load %.1f/%dcpu | best %.2f loud %d%s",
                                         ov_window, rd_window, pct, q_drops, pa_drops, p_avg, predict_ms_max,
                                         predict_ct, skipped, r_avg, qdepth_max, self._q_max, load1, os.cpu_count() or 0,
                                         best_score, best_amp, hint)
                                if pct >= self.degraded_pct and rd_window >= 5:
                                    log.warning("wake DEGRADED: %.0f%% of mic chunks dropped in the last ~15s "
                                                "-- predict avg %.0fms (budget 80ms). If predict is the wall, "
                                                "reduce competing CPU load; else raise ROBOT_WAKEWORD_QUEUE_MAX.",
                                                pct, p_avg)
                                best_score = 0.0
                                best_amp = 0
                                ov_window = 0
                                rd_window = 0
                                pa_drops = 0
                                q_drops = 0
                                predict_ms_sum = 0.0; predict_ct = 0; predict_ms_max = 0.0
                                resample_ms_sum = 0.0; resample_ct = 0
                                skipped = 0
                                qdepth_max = 0
                                next_report = time.time() + 15.0
        except Exception as e:
            # MIC-03: a raised listen fault (USB mic re-enumerating -> PortAudioError on reopen, an ALSA
            # hiccup, a transient ONNX error) used to return "QUIT" == a CLEAN exit-0, so systemd's
            # Restart=on-failure never restarted -> the robot went permanently deaf. Instead RETRY (main
            # re-enters the listen) up to max_listen_errors, then FAILED (main exits 1 -> systemd restarts).
            # A clean chunk resets the counter (above). 'q'-typed QUIT + the no-model refusal stay exit-0.
            self._listen_errors += 1
            if self._listen_errors >= self.max_listen_errors:
                log.error("wake listen failed %d times (last: %s) -- giving up (FAILED, exit for restart).",
                          self._listen_errors, e)
                return "FAILED"
            log.warning("wake listen failed (%s) -- retrying (%d/%d).", e, self._listen_errors,
                        self.max_listen_errors)
            if self._cancel.wait(2.0):   # honour cancel_listen() during the backoff (voice-abort listener)
                return "CANCELLED"
            return "RETRY"
        time.sleep(0.2)   # let PortAudio/ALSA release the mic before arecord grabs it
        return result

    def _capture_rate(self, sd):
        """Pick a sample rate the input device can OPEN, PREFERRING 16 kHz (no resample) then CLEAN
        integer-ratio rates (48k=3:1, 32k=2:1) over the device default / 44.1 kHz. Why the order matters:
        44100->16000 is a 160:441 polyphase resample whose ~8800-tap filter is redesigned every 80ms chunk
        -- that per-chunk cost can push the predict loop over real-time and DROP audio (the wake goes deaf).
        48000->16000 is a trivial 3:1 decimation (~61-tap filter). ROBOT_WAKEWORD_CAPTURE_RATE forces one."""
        forced = _envi("ROBOT_WAKEWORD_CAPTURE_RATE", 0)
        candidates = ([forced] if forced > 0 else []) + [_SAMPLE_RATE, 48000, 32000]
        try:
            candidates.append(int(sd.query_devices(self._device, "input")["default_samplerate"]))
        except Exception:
            pass
        candidates.append(44100)
        for rate in dict.fromkeys(candidates):   # dedupe, keep order
            try:
                sd.check_input_settings(device=self._device, samplerate=rate,
                                        channels=1, dtype="int16")
                if rate != _SAMPLE_RATE:
                    ratio = ("clean %d:1 decimation" % (rate // _SAMPLE_RATE)
                             if rate % _SAMPLE_RATE == 0 else "polyphase %d:%d -- costlier per chunk"
                             % (_SAMPLE_RATE // gcd(_SAMPLE_RATE, rate), rate // gcd(_SAMPLE_RATE, rate)))
                    log.info("wake: mic can't open 16 kHz -- capturing at %d Hz and resampling (%s)",
                             rate, ratio)
                return rate
            except Exception:
                continue
        return _SAMPLE_RATE   # nothing validated -> let InputStream surface the real error

    @staticmethod
    def _to_16k(np, audio, rate):
        """Resample one int16 chunk from `rate` to 16 kHz (scipy ships with openwakeword). At a clean
        ratio (e.g. 48000->16000 = 3:1) the polyphase filter is tiny and this is cheap; _capture_rate
        prefers those exactly so this stays well under the per-chunk budget."""
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
