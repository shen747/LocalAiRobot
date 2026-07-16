# brain/memory.py -- persistent environment memory (SQLite).
#
# Every "look" and obstacle event is recorded with what was seen and the 3-beam distances, so
# the robot doesn't start from scratch each time: context_string() builds a short rolling
# summary of the most recent DISTINCT objects (deduped, capped) that main.py prepends to the
# LLM prompt. Pure stdlib (sqlite3); survives reboots. Thread-safe (the behaviour loop and the
# main loop both write), via check_same_thread=False + a lock.

import logging
import os
import re
import sqlite3
import threading
import time

log = logging.getLogger("robot.memory")


def _max_context():
    try:
        return int(os.environ.get("ROBOT_MEMORY_MAX_CONTEXT", "8") or 8)
    except ValueError:
        return 8


def _max_scenes():
    try:
        return int(os.environ.get("ROBOT_MEMORY_MAX_SCENES", "3") or 3)
    except ValueError:
        return 3


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
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS observations (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, source TEXT, target TEXT, visible INTEGER,
                   horizontal TEXT, distance TEXT, confidence REAL, notes TEXT,
                   front_cm REAL, left_cm REAL, right_cm REAL)""")
        # User-taught people / things ("this is my wife Shani"): a name + relationship/note, an
        # optional photo on disk, and a VLM description of that photo. Injected into the prompt.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, name TEXT, fact TEXT, description TEXT, image_path TEXT)""")
        # Singleton key/value facts. The robot's OWN identity (who it is + what it can do) lives here and is
        # injected FIRST into every prompt (prompt_context) -- top-priority memory so the robot always knows
        # itself, and the marker used to detect a first run (introduce itself) or a name change.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, ts REAL)")
        # Enrolled faces for Concierge Mode (brain/face_id.py): a name + an L2-normalised ArcFace embedding
        # stored as raw float32 bytes. Written by ./scripts/enroll_face.sh; matched on each motion wake. Empty
        # unless you enrol someone -- the rest of the robot ignores this table.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS faces (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, name TEXT, embedding BLOB)""")
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
        try:
            with self._lock:
                self._db.execute(
                    """INSERT INTO observations
                       (ts, source, target, visible, horizontal, distance, confidence, notes,
                        front_cm, left_cm, right_cm)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""", row)
                self._db.commit()
        except Exception as e:
            # Memory is non-critical telemetry: a disk-full / I/O error must NEVER crash the
            # control loop or a running explore/find. Log and carry on.
            log.warning("memory write failed (%s) -- continuing", e)
            return
        if target:
            log.info("memory: recorded %s target=%s visible=%s %s/%s",
                     source, target, visible, horizontal, distance)

    def record_look(self, obs, distances=None):
        """Record a vision.look() result dict."""
        self.record("look", target=obs.get("object"), visible=obs.get("visible"),
                    horizontal=obs.get("horizontal"), distance=obs.get("distance"),
                    confidence=obs.get("confidence"), notes=obs.get("notes", ""),
                    distances=distances)

    def record_scene(self, description, objects=None, distances=None):
        """Record an agentic-explore look: the FULL scene description (a source='scene' row) PLUS one row
        per named object -- so recent_scenes() can replay the rich descriptions a REMOTE run gathered for a
        later LOCAL run to reuse, while context_string() still lists the individual objects."""
        description = (description or "").strip()
        if description:
            self.record("scene", target=None, notes=description, distances=distances)
        for o in (objects or []):
            o = str(o).strip()
            if o:
                self.record("explore", target=o, visible=True, distances=distances)

    # ---------- user-taught facts (the "remember this person/thing" feature) ----------
    def remember_fact(self, name, fact="", description="", image_path=None):
        """Store a person/thing the user named. Non-critical: a write error is logged, not raised."""
        name = (name or "").strip()
        if not name:
            return
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO facts (ts, name, fact, description, image_path) VALUES (?,?,?,?,?)",
                    (time.time(), name, (fact or "").strip(), (description or "").strip(), image_path))
                self._db.commit()
            log.info("memory: remembered '%s'%s%s", name, f" ({fact})" if fact else "",
                     f" [photo {image_path}]" if image_path else "")
        except Exception as e:
            log.warning("memory: remember_fact failed (%s) -- continuing", e)

    def save_photo(self, jpeg_bytes, name):
        """Save a captured JPEG into a 'photos' dir next to the DB. Returns the path, or None."""
        if not jpeg_bytes:
            return None
        photos = os.path.join(os.path.dirname(self.path) or ".", "photos")
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "unknown").lower()).strip("-") or "unknown"
        path = os.path.join(photos, f"{slug}-{int(time.time())}.jpg")
        try:
            os.makedirs(photos, exist_ok=True)
            with open(path, "wb") as f:
                f.write(jpeg_bytes)
            return path
        except Exception as e:
            log.warning("memory: photo save failed (%s)", e)
            return None

    # ---------- enrolled faces (Concierge Mode; brain/face_id.py) ----------
    def add_face(self, name, embedding_bytes):
        """Store a face embedding (raw float32 bytes) under `name`. Multiple rows per person are fine --
        each is compared independently, so enrolling several angles improves matching."""
        try:
            with self._lock:
                self._db.execute("INSERT INTO faces (ts, name, embedding) VALUES (?,?,?)",
                                 (time.time(), (name or "").strip(), sqlite3.Binary(embedding_bytes)))
                self._db.commit()
        except Exception as e:
            log.warning("memory: add_face failed (%s) -- continuing", e)

    def all_faces(self):
        """[(name, embedding_bytes), ...] for every enrolled face. Empty if nobody is enrolled."""
        try:
            with self._lock:
                return [(r[0], r[1]) for r in
                        self._db.execute("SELECT name, embedding FROM faces").fetchall()]
        except Exception as e:
            log.warning("memory: all_faces failed (%s)", e)
            return []

    def forget_face(self, name):
        """Delete all enrolled embeddings for `name`. Returns the row count removed."""
        try:
            with self._lock:
                cur = self._db.execute("DELETE FROM faces WHERE name=?", ((name or "").strip(),))
                self._db.commit()
                return cur.rowcount
        except Exception as e:
            log.warning("memory: forget_face failed (%s)", e)
            return 0

    # ---------- the robot's own identity (top-priority memory) ----------
    def get_meta(self, key):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        try:
            with self._lock:
                self._db.execute("INSERT OR REPLACE INTO meta (key, value, ts) VALUES (?,?,?)",
                                 (key, value, time.time()))
                self._db.commit()
        except Exception as e:
            log.warning("memory: set_meta(%s) failed (%s) -- continuing", key, e)

    def ensure_identity(self, name, summary):
        """Persist the robot's own identity (name + what it can do) as top-priority memory. Returns 'new'
        the FIRST time any identity is stored (fresh DB -> the robot introduces itself), 'renamed' if the
        configured ROBOT_NAME differs from what was stored (say the new name once), else 'known'."""
        name = (name or "").strip() or "Jarvis"
        prev = self.get_meta("robot_name")
        self.set_meta("robot_name", name)
        self.set_meta("robot_identity", (summary or "").strip())
        if prev is None:
            return "new"
        return "renamed" if prev.strip().lower() != name.lower() else "known"

    def identity_string(self):
        """The robot's identity, for the FRONT of the LLM prompt context (top priority). Empty if unset."""
        name = self.get_meta("robot_name")
        if not name:
            return ""
        summary = (self.get_meta("robot_identity") or "").strip()
        return f"Your own identity (most important -- this is you): {summary}" if summary \
            else f"Your name is {name}."

    def facts_string(self, max_items=12):
        """One-line summary of taught people/things (newest first, deduped) for the LLM prompt."""
        with self._lock:
            rows = self._db.execute(
                "SELECT name, fact, description FROM facts ORDER BY ts DESC LIMIT 200").fetchall()
        seen, parts = set(), []
        for name, fact, desc in rows:
            key = (name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            bit = name
            if fact:
                bit += f" ({fact})"
            if desc:
                bit += f": {(desc or '')[:120]}"
            parts.append(bit)
            if len(parts) >= max_items:
                break
        return ("Known people/things: " + "; ".join(parts) + ".") if parts else ""

    def recent_scenes(self, n=None):
        """Newest DISTINCT scene descriptions (source='scene' rows), for the prompt + rag.sh --scenes.
        This is how a REMOTE explore's rich observations survive into a later offline/local session."""
        n = n or _max_scenes()
        with self._lock:
            rows = self._db.execute(
                "SELECT notes, ts FROM observations WHERE source='scene' AND notes IS NOT NULL "
                "AND notes != '' ORDER BY ts DESC LIMIT 200").fetchall()
        seen, out = set(), []
        for notes, _ts in rows:
            key = (notes or "").strip().lower()[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(notes.strip())
            if len(out) >= n:
                break
        return out

    def prompt_context(self, max_items=None):
        """Combined LLM-prompt context: taught people/things + recently seen objects + recent scene
        descriptions (so a LOCAL run reuses the rich scenes a REMOTE explore gathered)."""
        scenes = self.recent_scenes()
        scene_str = ("Earlier, while exploring, I saw (from MEMORY -- NOT the current camera view): "
                     + " | ".join(s[:160] for s in scenes)) if scenes else ""
        # identity_string() goes FIRST -- it is top-priority memory (who the robot is + what it can do).
        return " ".join(p for p in (self.identity_string(), self.facts_string(),
                                    self.context_string(max_items), scene_str) if p)

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
