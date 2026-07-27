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


# Common words that carry no retrieval signal -- dropped so the FTS ranking keys on the CONTENT words
# ("who is *shani*", "what's in the *garage*") instead of "who"/"is"/"the".
_FTS_STOP = frozenset(
    "a an and the is are was were be been being of to in on at it its this that these those "
    "who what which where when why how do does did done can could would should will shall you "
    "your yours me my mine we our us i he she him her they them his their there here please "
    "tell show say about with for from into over under near have has had get got".split())


def _fts_query(text):
    """Turn a raw utterance into a safe FTS5 MATCH string, or None if there's nothing worth matching.
    Alnum tokens only, stopwords + 1-char noise dropped, de-duped, capped at 8, each quoted as a LITERAL
    term and OR-joined -- so FTS5 operator characters in the user's speech (\" * : - NEAR) can never
    break the query or change its meaning."""
    seen, out = set(), []
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(t) < 2 or t in _FTS_STOP or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= 8:
            break
    return " OR ".join(f'"{t}"' for t in out) or None


class Memory:
    _PRUNE_EVERY = 500   # opportunistic prune cadence, in inserted observation rows

    def __init__(self, db_path=None):
        db_path = db_path or os.environ.get("ROBOT_MEMORY_DB", "~/.localairobot/memory.db")
        self.path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        # Step 13(a): WAL + synchronous=NORMAL -- one fsync per txn instead of per journal phase. MUST run
        # right after connect, before any DDL/DML (journal_mode can't change mid-transaction). journal_mode
        # persists in the DB file; synchronous is per-connection (re-set on every open).
        try:
            mode = self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self._db.execute("PRAGMA synchronous=NORMAL")
            if str(mode).lower() != "wal":
                log.warning("memory: journal_mode=WAL unavailable (got %s) -- continuing", mode)
        except Exception as e:
            log.warning("memory: could not set WAL pragmas (%s) -- continuing", e)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS observations (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, source TEXT, target TEXT, visible INTEGER,
                   horizontal TEXT, distance TEXT, confidence REAL, notes TEXT,
                   front_cm REAL, left_cm REAL, right_cm REAL)""")
        # User-taught people / things ("this is my wife Shani"): a name + relationship/note, an optional photo
        # on disk, a VLM description, and an optional PRONUNCIATION (a phonetic respelling of the name used for
        # the spoken greeting instead of the real name -- e.g. Ramesh -> "Ruh-mesh"). Injected into the prompt.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, name TEXT, fact TEXT, description TEXT, image_path TEXT, pronunciation TEXT)""")
        # Migration: add `pronunciation` to a facts table created BEFORE this column existed. ADD COLUMN raises
        # if it's already there (SQLite has no IF NOT EXISTS for it), so swallow that -- it makes this a no-op.
        try:
            self._db.execute("ALTER TABLE facts ADD COLUMN pronunciation TEXT")
        except Exception:
            pass
        # Singleton key/value facts. The robot's OWN identity (who it is + what it can do) lives here and is
        # injected FIRST into every prompt (prompt_context) -- top-priority memory so the robot always knows
        # itself, and the marker used to detect a first run (introduce itself) or a name change.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, ts REAL)")
        # Enrolled faces for Concierge Mode (brain/face_id.py): a name + an L2-normalised ArcFace embedding
        # stored as raw float32 bytes. Written by ./scripts/enroll_person.sh; matched on each motion wake. Empty
        # unless you enrol someone -- the rest of the robot ignores this table.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS faces (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL, name TEXT, embedding BLOB)""")
        # Step 14: indexes for the per-turn prompt-build queries (after the CREATE TABLEs, before commit).
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_obs_visible_ts ON observations(visible, ts)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_obs_source_ts  ON observations(source, ts)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_ts       ON facts(ts)")
        # ---------- FTS5 relevance index (the actual "retrieval" in RAG) ----------
        # So retrieval can rank taught facts + explore scenes against the CURRENT question (BM25) instead
        # of always dumping the newest N. Two content-owning FTS5 tables, kept in sync by triggers (rowid ==
        # the source row's id) so rag.sh / raw sqlite edits stay consistent too. If this SQLite build lacks
        # FTS5, we warn once and every retrieval path falls back to pure recency -- identical to before.
        self._fts_ok = False
        try:
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
                             "USING fts5(name, body, tokenize='porter unicode61')")
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS scenes_fts "
                             "USING fts5(body, tokenize='porter unicode61')")
            self._db.executescript("""
                CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                  INSERT INTO facts_fts(rowid, name, body)
                    VALUES (new.id, coalesce(new.name,''),
                            coalesce(new.fact,'')||' '||coalesce(new.description,''));
                END;
                CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                  DELETE FROM facts_fts WHERE rowid = old.id;
                END;
                CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                  DELETE FROM facts_fts WHERE rowid = old.id;
                  INSERT INTO facts_fts(rowid, name, body)
                    VALUES (new.id, coalesce(new.name,''),
                            coalesce(new.fact,'')||' '||coalesce(new.description,''));
                END;
                CREATE TRIGGER IF NOT EXISTS scene_ai AFTER INSERT ON observations
                  WHEN new.source='scene' BEGIN
                  INSERT INTO scenes_fts(rowid, body) VALUES (new.id, coalesce(new.notes,''));
                END;
                CREATE TRIGGER IF NOT EXISTS scene_ad AFTER DELETE ON observations
                  WHEN old.source='scene' BEGIN
                  DELETE FROM scenes_fts WHERE rowid = old.id;
                END;
            """)
            self._fts_ok = True
        except sqlite3.Error as e:
            log.warning("memory: FTS5 unavailable (%s) -- retrieval falls back to recency only", e)
        self._db.commit()
        if self._fts_ok:
            self._backfill_fts()   # index any rows that predate the FTS tables (an existing DB gaining FTS)
        n = self._db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        log.info("memory: %s (%d observations)", self.path, n)
        # Step 12: bounded retention. Runs once here (before any behaviour/control thread exists) and
        # opportunistically every _PRUNE_EVERY inserted rows from record()/record_scene().
        self._since_prune = 0
        self._prune()

    def _prune(self, keep=None):
        """Keep the newest ROBOT_MEMORY_RETAIN *non-scene* observations, and -- SEPARATELY -- the newest
        ROBOT_MEMORY_RETAIN_SCENES scene rows. Scenes are the expensive, often remote-gathered explore
        summaries a later LOCAL run reuses, so routine 'look' churn must NEVER evict them: the old code
        pruned by one global budget and a busy driver could throw the scenes away. Log-and-continue on any
        error. NEVER call while holding self._lock -- it acquires the (non-reentrant) lock itself."""
        if keep is None:
            try:
                keep = int(os.environ.get("ROBOT_MEMORY_RETAIN", "5000") or 5000)
            except ValueError:
                keep = 5000
        try:
            keep_scenes = int(os.environ.get("ROBOT_MEMORY_RETAIN_SCENES", "500") or 500)
        except ValueError:
            keep_scenes = 500
        try:
            with self._lock:
                self._db.execute(
                    "DELETE FROM observations WHERE (source IS NULL OR source!='scene') AND id NOT IN "
                    "(SELECT id FROM observations WHERE (source IS NULL OR source!='scene') "
                    "ORDER BY ts DESC LIMIT ?)", (keep,))
                self._db.execute(
                    "DELETE FROM observations WHERE source='scene' AND id NOT IN "
                    "(SELECT id FROM observations WHERE source='scene' ORDER BY ts DESC LIMIT ?)",
                    (keep_scenes,))
                self._db.commit()
                self._since_prune = 0
        except Exception as e:
            log.warning("memory: prune failed (%s) -- continuing", e)

    def _backfill_fts(self, force=False):
        """Populate the FTS tables from the base rows. On init this is a no-op once they're populated;
        with force=True (reindex_fts) it wipes and rebuilds. FTS is an optimisation, never load-bearing --
        so a failure just logs and leaves retrieval on the recency fallback."""
        if not getattr(self, "_fts_ok", False):
            return
        try:
            with self._lock:
                if force:
                    self._db.execute("DELETE FROM facts_fts")
                    self._db.execute("DELETE FROM scenes_fts")
                if force or not self._db.execute("SELECT 1 FROM facts_fts LIMIT 1").fetchone():
                    self._db.execute(
                        "INSERT INTO facts_fts(rowid, name, body) SELECT id, coalesce(name,''), "
                        "coalesce(fact,'')||' '||coalesce(description,'') FROM facts")
                if force or not self._db.execute("SELECT 1 FROM scenes_fts LIMIT 1").fetchone():
                    self._db.execute(
                        "INSERT INTO scenes_fts(rowid, body) SELECT id, coalesce(notes,'') "
                        "FROM observations WHERE source='scene'")
                self._db.commit()
        except sqlite3.Error as e:
            log.warning("memory: FTS backfill failed (%s) -- continuing", e)

    def reindex_fts(self):
        """Wipe and rebuild the FTS index from the base tables (used by rag.sh --reindex, e.g. after raw
        sqlite edits). Returns (n_facts, n_scenes) indexed; (0, 0) if FTS5 isn't available."""
        self._backfill_fts(force=True)
        if not self._fts_ok:
            return (0, 0)
        with self._lock:
            nf = self._db.execute("SELECT count(*) FROM facts_fts").fetchone()[0]
            ns = self._db.execute("SELECT count(*) FROM scenes_fts").fetchone()[0]
        return (nf, ns)

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
                self._since_prune += 1
        except Exception as e:
            # Memory is non-critical telemetry: a disk-full / I/O error must NEVER crash the
            # control loop or a running explore/find. Log and carry on.
            log.warning("memory write failed (%s) -- continuing", e)
            return
        if target:
            log.info("memory: recorded %s target=%s visible=%s %s/%s",
                     source, target, visible, horizontal, distance)
        if self._since_prune >= self._PRUNE_EVERY:
            self._prune()   # OUTSIDE the with-block: self._lock is NOT held here (deadlock otherwise)

    def record_look(self, obs, distances=None):
        """Record a vision.look() result dict."""
        self.record("look", target=obs.get("object"), visible=obs.get("visible"),
                    horizontal=obs.get("horizontal"), distance=obs.get("distance"),
                    confidence=obs.get("confidence"), notes=obs.get("notes", ""),
                    distances=distances)

    def record_scene(self, description, objects=None, distances=None):
        """Record an agentic-explore look: the FULL scene description (a source='scene' row) PLUS one row
        per named object -- so recent_scenes() can replay the rich descriptions a REMOTE run gathered for a
        later LOCAL run to reuse, while context_string() still lists the individual objects.
        Step 13(b): one executemany + ONE commit (one fsync) instead of K+1 per-row commits."""
        description = (description or "").strip()
        distances = distances or {}
        ts = time.time()
        front, left, right = distances.get("front"), distances.get("left"), distances.get("right")
        rows, targets = [], []
        if description:
            #      ts, source,  target, visible, horiz, dist, conf, notes,       front, left, right
            rows.append((ts, "scene", None, None, None, None, None, description, front, left, right))
        for o in (objects or []):
            o = str(o).strip()
            if o:
                rows.append((ts, "explore", o, 1, None, None, None, "", front, left, right))
                targets.append(o)
        if not rows:
            return
        try:
            with self._lock:
                self._db.executemany(
                    """INSERT INTO observations
                       (ts, source, target, visible, horizontal, distance, confidence, notes,
                        front_cm, left_cm, right_cm)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
                self._db.commit()
                self._since_prune += len(rows)
        except Exception as e:
            log.warning("memory write failed (%s) -- continuing", e)
            return
        for o in targets:    # preserve record()'s per-target line, emitted outside the lock
            log.info("memory: recorded %s target=%s visible=%s %s/%s", "explore", o, True, None, None)
        if self._since_prune >= self._PRUNE_EVERY:
            self._prune()   # self._lock is NOT held here

    # ---------- user-taught facts (the "remember this person/thing" feature) ----------
    def remember_fact(self, name, fact="", description="", image_path=None, pronunciation=""):
        """Store a person/thing the user named. `pronunciation` is an optional phonetic respelling of the name
        for the spoken greeting (e.g. 'Ruh-mesh'). Non-critical: a write error is logged, not raised."""
        name = (name or "").strip()
        if not name:
            return
        pronunciation = (pronunciation or "").strip() or name   # default say-as = the name itself
        try:
            with self._lock:
                self._db.execute(
                    "INSERT INTO facts (ts, name, fact, description, image_path, pronunciation) "
                    "VALUES (?,?,?,?,?,?)",
                    (time.time(), name, (fact or "").strip(), (description or "").strip(), image_path,
                     pronunciation))
                self._db.commit()
            log.info("memory: remembered '%s'%s%s%s", name, f" ({fact})" if fact else "",
                     f" say-as '{pronunciation}'" if pronunciation != name else "",
                     f" [photo {image_path}]" if image_path else "")
        except Exception as e:
            log.warning("memory: remember_fact failed (%s) -- continuing", e)

    def pronunciation_for(self, name):
        """The stored phonetic respelling for `name` (newest non-empty), or '' if none. Used for the spoken
        greeting so a tricky name is said right (e.g. Ramesh -> 'Ruh-mesh')."""
        name = (name or "").strip()
        if not name:
            return ""
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT pronunciation FROM facts WHERE lower(name)=lower(?) AND pronunciation IS NOT NULL "
                    "AND pronunciation != '' ORDER BY ts DESC LIMIT 1", (name,)).fetchone()
            return (row[0] or "").strip() if row else ""
        except Exception as e:
            log.warning("memory: pronunciation_for failed (%s)", e)
            return ""

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

    def forget_fact(self, name):
        """Delete all taught facts for `name` (case-insensitive, like rag.sh --forget). Returns rows removed.
        Used by enroll_person.sh --replace so a re-enroll REPLACES a person instead of stacking duplicates."""
        try:
            with self._lock:
                cur = self._db.execute("DELETE FROM facts WHERE lower(name)=lower(?)", ((name or "").strip(),))
                self._db.commit()
                return cur.rowcount
        except Exception as e:
            log.warning("memory: forget_fact failed (%s)", e)
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

    def _fts_ids(self, table, query, limit=50):
        """Source-row ids best matching `query` in an FTS table, BM25-ranked best-first. [] if FTS5 is
        unavailable or the query has no content words. `table` is always an internal literal, never user
        input, so the f-string is safe."""
        q = _fts_query(query)
        if not (self._fts_ok and q):
            return []
        try:
            with self._lock:
                return [r[0] for r in self._db.execute(
                    f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
                    (q, limit)).fetchall()]
        except sqlite3.Error as e:
            log.debug("memory: FTS query on %s failed (%s) -- recency only", table, e)
            return []

    def facts_string(self, query=None, max_items=12):
        """One-line summary of taught people/things for the LLM prompt. With a `query`, the most RELEVANT
        facts (BM25 against the utterance -- e.g. a name) lead, then the newest fill the rest; without one
        (or if FTS is unavailable) it's purely newest-first, exactly as before. Deduped by name, capped."""
        relevant = self._fts_ids("facts_fts", query, limit=max_items * 4)
        with self._lock:
            recent = self._db.execute(
                "SELECT id, name, fact, description FROM facts ORDER BY ts DESC LIMIT 200").fetchall()
        detail = {r[0]: (r[1], r[2], r[3]) for r in recent}
        missing = [i for i in relevant if i not in detail]   # relevant rows older than the newest-200 window
        if missing:
            with self._lock:
                ph = ",".join("?" * len(missing))
                for r in self._db.execute(
                        f"SELECT id, name, fact, description FROM facts WHERE id IN ({ph})", missing):
                    detail[r[0]] = (r[1], r[2], r[3])
        seen, parts = set(), []
        for fid in relevant + [r[0] for r in recent]:        # relevant first (rank order), then newest
            row = detail.get(fid)
            if not row:
                continue
            name, fact, desc = row
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

    def recent_scenes(self, query=None, n=None):
        """Scene descriptions (source='scene' rows) for the prompt + rag.sh --scenes. With a `query`, the
        most RELEVANT scenes lead (BM25 -- so 'what did you see in the garage?' finds the garage scene even
        if it's old), then the newest fill in; without one it's newest-first, as before. This is how a
        REMOTE explore's rich observations survive into a later offline/local session. Deduped, capped."""
        n = n or _max_scenes()
        relevant = self._fts_ids("scenes_fts", query, limit=max(n * 8, 40))
        with self._lock:
            recent = self._db.execute(
                "SELECT id, notes FROM observations WHERE source='scene' AND notes IS NOT NULL "
                "AND notes != '' ORDER BY ts DESC LIMIT 200").fetchall()
        detail = {r[0]: r[1] for r in recent}
        missing = [i for i in relevant if i not in detail]
        if missing:
            with self._lock:
                ph = ",".join("?" * len(missing))
                for r in self._db.execute(
                        f"SELECT id, notes FROM observations WHERE id IN ({ph})", missing):
                    detail[r[0]] = r[1]
        seen, out = set(), []
        for sid in relevant + [r[0] for r in recent]:        # relevant first (rank order), then newest
            notes = detail.get(sid)
            if not notes:
                continue
            key = (notes or "").strip().lower()[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(notes.strip())
            if len(out) >= n:
                break
        return out

    def prompt_context(self, query=None, max_items=None):
        """Combined LLM-prompt context: robot identity + taught people/things + recently seen objects +
        scene descriptions (so a LOCAL run reuses the rich scenes a REMOTE explore gathered). When `query`
        (the user's utterance) is given, facts + scenes are RANKED for relevance to it (BM25) instead of
        always dumping the newest -- a tighter, more on-topic context, and fewer tokens for the small local
        model. Objects stay recency-based (single words, cheap). Falls back to newest-first with no query."""
        scenes = self.recent_scenes(query=query)
        scene_str = ("Earlier, while exploring, I saw (from MEMORY -- NOT the current camera view): "
                     + " | ".join(s[:160] for s in scenes)) if scenes else ""
        # identity_string() goes FIRST -- it is top-priority memory (who the robot is + what it can do).
        return " ".join(p for p in (self.identity_string(), self.facts_string(query=query),
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
