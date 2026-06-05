# brain/memory.py -- persistent environment memory (SQLite).
#
# Every "look" and obstacle event is recorded with what was seen and the 3-beam distances, so
# the robot doesn't start from scratch each time: context_string() builds a short rolling
# summary of the most recent DISTINCT objects (deduped, capped) that main.py prepends to the
# LLM prompt. Pure stdlib (sqlite3); survives reboots. Thread-safe (the behaviour loop and the
# main loop both write), via check_same_thread=False + a lock.

import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger("robot.memory")


def _max_context():
    try:
        return int(os.environ.get("ROBOT_MEMORY_MAX_CONTEXT", "8") or 8)
    except ValueError:
        return 8


def _ago(seconds):
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"~{s // 60}m ago"
    return f"~{s // 3600}h ago"


class Memory:
    def __init__(self, db_path=None):
        db_path = db_path or os.environ.get("ROBOT_MEMORY_DB", "~/.localairobot/memory.db")
        self.path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS observations (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, source TEXT, target TEXT, visible INTEGER,
                   horizontal TEXT, distance TEXT, confidence REAL, notes TEXT,
                   front_cm REAL, left_cm REAL, right_cm REAL)""")
        self._db.commit()
        n = self._db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        log.info("memory: %s (%d observations)", self.path, n)

    def record(self, source, target=None, visible=None, horizontal=None, distance=None,
               confidence=None, notes="", distances=None):
        distances = distances or {}
        row = (time.time(), source, target,
               None if visible is None else int(bool(visible)),
               horizontal, distance, confidence, notes or "",
               distances.get("front"), distances.get("left"), distances.get("right"))
        with self._lock:
            self._db.execute(
                """INSERT INTO observations
                   (ts, source, target, visible, horizontal, distance, confidence, notes,
                    front_cm, left_cm, right_cm)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""", row)
            self._db.commit()
        if target:
            log.info("memory: recorded %s target=%s visible=%s %s/%s",
                     source, target, visible, horizontal, distance)

    def record_look(self, obs, distances=None):
        """Record a vision.look() result dict."""
        self.record("look", target=obs.get("object"), visible=obs.get("visible"),
                    horizontal=obs.get("horizontal"), distance=obs.get("distance"),
                    confidence=obs.get("confidence"), notes=obs.get("notes", ""),
                    distances=distances)

    def context_string(self, max_items=None):
        """One-line rolling summary of recent distinct seen objects, for the LLM prompt.
        Empty string if nothing useful is stored yet."""
        max_items = max_items or _max_context()
        with self._lock:
            rows = self._db.execute(
                """SELECT target, horizontal, distance, ts FROM observations
                   WHERE visible=1 AND target IS NOT NULL AND target != ''
                   ORDER BY ts DESC LIMIT 200""").fetchall()
        now = time.time()
        seen, parts = set(), []
        for target, horiz, dist, ts in rows:
            key = target.lower()
            if key in seen:
                continue
            seen.add(key)
            where = ", ".join(p for p in (horiz, dist) if p)
            stamp = _ago(now - ts)
            parts.append(f"{target} ({where + ', ' if where else ''}{stamp})")
            if len(parts) >= max_items:
                break
        return ("Recently seen: " + "; ".join(parts) + ".") if parts else ""

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass
