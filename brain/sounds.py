# brain/sounds.py -- non-verbal audio cues (earcons) for a SLOW, offline robot.
#
# On the Pi 5 CPU the model is slow (text up to ROBOT_LLM_TIMEOUT_S=120s, vision up to
# ROBOT_VISION_TIMEOUT_S=600s). The only audio out today is Piper TTS, so during a long
# think/look the operator gets NO sign the robot heard them. This module adds three cues,
# adapted from the reference project's sounds/{greeting,ack,thinking,error} system but
# re-grounded on THIS repo's aplay + half-duplex audio_lock discipline (NOT sounddevice):
#
#   greeting  -- once, after the stack is up / model warmed (audible "I'm online", vital headless)
#   ack       -- instant, the moment a wake word fires or a command is accepted ("heard you")
#   thinking  -- a loop played WHILE the model blocks (proof-of-life across a multi-minute look)
#   error     -- optional, on obstacle / LLM failure (fire-and-forget; see SAFETY below)
#
# SAFETY: every cue is fire-and-forget on a daemon thread and is NEVER on the synchronous path
# to driver.stop()/_neutral(). An obstacle stop is not delayed by a sound.
#
# COLLISION: cues share main.py's single audio_lock with arecord (record) and Piper (playback),
# so audio is globally half-duplex -- a cue can never garble TTS or be recorded into a command.
# The thinking loop acquires the lock PER CLIP (never across the whole think) so stop_thinking()
# + the reply's say() can grab it promptly. Caller MUST stop_thinking() before the first say().
#
# DRY RUN / disabled: logs "SOUND -> <cue>" on the robot.sound logger instead of playing, so a
# laptop with no speaker runs cleanly (mirrors driver.py fakes and face.py simulated backend).

import logging
import os
import random
import subprocess
import threading
import time

from driver import dry_run_enabled

log = logging.getLogger("robot.sound")

# cue name -> asset subdirectory (mirrors the reference layout)
_CATEGORY_DIR = {
    "greeting": "greeting_sounds",
    "ack": "ack_sounds",
    "thinking": "thinking_sounds",
    "error": "error_sounds",
}

# repo-root/sounds by default (this file is <repo>/brain/sounds.py); resolves under the
# systemd unit's WorkingDirectory=.../brain too because the path is absolute.
_DEFAULT_SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds")


class Sounds:
    """Earcon player. Shares main.py's audio_lock so all audio stays half-duplex.
    Every method is best-effort and never raises into the caller."""

    def __init__(self, audio_lock, cfg=None):
        self._audio_lock = audio_lock
        self.enabled = os.environ.get("ROBOT_SOUNDS_ENABLE", "1") == "1"
        self.dry_run = dry_run_enabled()
        # log-only when globally disabled OR in dry run (no aplay, just the robot.sound trace)
        self._log_only = (not self.enabled) or self.dry_run
        self._dir = os.environ.get("ROBOT_SOUNDS_DIR", "").strip() or _DEFAULT_SOUNDS_DIR
        self._device = os.environ.get("ROBOT_SOUNDS_DEVICE", "").strip()  # USB speaker card; '' = aplay default
        self._cat_enable = {
            cat: os.environ.get(f"ROBOT_SOUNDS_{cat.upper()}", "1") == "1" for cat in _CATEGORY_DIR
        }
        self._lead_ms = _envi("ROBOT_THINKING_LEAD_MS", 500)  # stay silent on fast turns
        self._gap_ms = _envi("ROBOT_THINKING_GAP_MS", 0)      # gap between looped thinking clips
        self._thinking = threading.Event()
        self._thinking_thread = None
        if not self.enabled:
            log.info("sounds disabled (ROBOT_SOUNDS_ENABLE=0)")
        else:
            log.info("sounds %s (dir=%s, device=%s)",
                     "DRY RUN (log only)" if self.dry_run else "live",
                     self._dir, self._device or "default")

    # ---------- one-shot cues ----------
    def play(self, category):
        """Fire a one-shot cue (greeting/ack/error). Returns immediately; plays on a daemon thread."""
        if not self.enabled or not self._cat_enable.get(category, False):
            return
        if self._log_only:
            log.info("SOUND -> %s", category)
            return
        wav = self._pick(category)
        if not wav:
            return
        threading.Thread(target=self._play_locked, args=(wav,), daemon=True).start()

    # ---------- thinking loop ----------
    def start_thinking(self):
        """Begin the 'thinking' loop in the background (idempotent). Lead-in delay keeps fast
        turns silent. Call stop_thinking() before the first say() of the turn."""
        if not self.enabled or not self._cat_enable.get("thinking", False):
            return
        if self._thinking.is_set():
            return
        # Fresh Event per session: a later start_thinking() installs a NEW event object, so a
        # superseded loop thread that's still finishing a clip watches its OWN (now-cleared) event
        # and exits -- it can never be "rescued" back to life by the next session re-arming a
        # shared flag. (The old single-shared-Event design could leak overlapping loop threads in
        # the tight FIND/EXPLORE cadence.)
        ev = threading.Event()
        ev.set()
        self._thinking = ev
        if self._log_only:
            log.info("SOUND -> thinking loop start")
            return
        self._thinking_thread = threading.Thread(
            target=self._thinking_loop, args=(ev,), daemon=True)
        self._thinking_thread.start()

    def stop_thinking(self):
        """Stop the loop within one clip boundary. Safe to call when not running."""
        if not self._thinking.is_set():
            return
        self._thinking.clear()
        if self._log_only:
            log.info("SOUND -> thinking loop stop")

    def close(self):
        self.stop_thinking()

    # ---------- internals ----------
    def _thinking_loop(self, ev):
        # `ev` is THIS session's event (not self._thinking) -- see start_thinking(). The whole loop
        # watches `ev`, so once it's cleared this thread exits for good regardless of later sessions.
        # Lead-in: stay quiet so a cache-warm fast turn never triggers a cue (reference does 0.5s).
        if self._wait_or_stopped(ev, self._lead_ms / 1000.0):
            return
        while ev.is_set():
            wav = self._pick("thinking")
            if wav:
                # Acquire the lock PER CLIP, never across the whole loop, or say()/arecord starve.
                with self._audio_lock:
                    if not ev.is_set():
                        return
                    self._aplay(wav)
                if self._wait_or_stopped(ev, self._gap_ms / 1000.0):
                    return
            else:
                # no thinking assets present -> don't busy-spin, just idle until stopped
                if self._wait_or_stopped(ev, 0.5):
                    return

    def _wait_or_stopped(self, ev, secs):
        """Sleep up to `secs`, checking THIS session's event every 100ms. True if it was cleared."""
        end = time.monotonic() + max(0.0, secs)
        while time.monotonic() < end:
            if not ev.is_set():
                return True
            time.sleep(0.1)
        return not ev.is_set()

    def _pick(self, category):
        d = os.path.join(self._dir, _CATEGORY_DIR[category])
        try:
            files = [f for f in os.listdir(d) if f.lower().endswith(".wav")]
        except OSError:
            return None
        return os.path.join(d, random.choice(files)) if files else None

    def _play_locked(self, wav):
        with self._audio_lock:
            self._aplay(wav)

    def _aplay(self, wav):
        cmd = ["aplay"]
        if self._device:
            cmd += ["-D", self._device]
        cmd.append(wav)
        try:
            subprocess.run(cmd, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.warning("aplay failed (%s)", e)


def _envi(key, default):
    try:
        return int(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return int(default)
