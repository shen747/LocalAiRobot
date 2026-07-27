#!/usr/bin/env bash
# rag.sh -- inspect / edit the robot's environment memory (the "RAG" SQLite store at ROBOT_MEMORY_DB).
# Uses the brain venv's Python (sqlite3 is stdlib -- nothing extra to install). Examples:
#   ./scripts/rag.sh                                 # taught people/things (the 'facts' table)
#   ./scripts/rag.sh --seen                          # recent things seen (the 'observations' table)
#   ./scripts/rag.sh --scenes                        # recent scene descriptions (from agentic explore)
#   ./scripts/rag.sh --faces                         # enrolled Concierge faces + how many photos each (the 'faces' table)
#   ./scripts/rag.sh --all                           # facts + seen + scenes + faces
#   ./scripts/rag.sh --search "garage tools"         # RANKED search (FTS5/BM25): the facts + scenes that best match the words
#   ./scripts/rag.sh --reindex                       # rebuild the FTS search index from the base tables (after raw sqlite edits)
#   ./scripts/rag.sh --forget Shawn                  # delete a taught person/thing by name
#   ./scripts/rag.sh --clear-facts                   # wipe all taught facts (they're re-teachable)
#   ./scripts/rag.sh --clear-scenes                  # wipe stored explore scene descriptions (stale RAG context)
#   ./scripts/rag.sh --clear-all                     # wipe facts AND observations (asks first)
#   ./scripts/rag.sh --query "SELECT * FROM facts"   # run ANY SQL (read or write)
# (For raw interactive poking, the installer also adds the sqlite3 CLI: sqlite3 "$ROBOT_MEMORY_DB")
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/config/robot.env" ] && { set -a; . "$REPO/config/robot.env"; set +a; }
PY="$REPO/brain/.venv/bin/python"
DB="${ROBOT_MEMORY_DB:-$HOME/.localairobot/memory.db}"
DB="${DB/#\~/$HOME}"   # expand a leading ~ if robot.env left it literal
[ -x "$PY" ] || { echo "venv python not found ($PY) -- run scripts/install_stack_pi5.sh first." >&2; exit 1; }

run() {  # run "<SQL>" [params...] -- prints an aligned table for SELECT, a rowcount for writes
  local sql="$1"; shift
  "$PY" - "$DB" "$sql" "$@" <<'PY'
import os, sqlite3, sys
db, sql, params = sys.argv[1], sys.argv[2], sys.argv[3:]
if not os.path.exists(db):
    print(f"(no memory DB yet at {db} -- the robot creates it on first run)"); sys.exit(0)
con = sqlite3.connect(db)
try:
    cur = con.execute(sql, params)
except sqlite3.Error as e:
    print(f"SQL error: {e}", file=sys.stderr); sys.exit(1)
if cur.description:                                    # a SELECT -> aligned table
    cols = [d[0] for d in cur.description]
    rows = [[("" if v is None else str(v)) for v in r] for r in cur.fetchall()]
    w = [len(c) for c in cols]
    for r in rows:
        for i, v in enumerate(r): w[i] = max(w[i], len(v))
    fmt = lambda vals: "  ".join(s.ljust(w[i]) for i, s in enumerate(vals))
    print(fmt(cols)); print("  ".join("-" * x for x in w))
    for r in rows: print(fmt(r))
    print(f"\n{len(rows)} row(s).")
else:                                                  # INSERT/UPDATE/DELETE
    con.commit(); print(f"OK -- {cur.rowcount} row(s) affected.")
con.close()
PY
}

search() {  # search "<words>" -- BM25-ranked facts + scenes that best match the words (what the robot would retrieve)
  "$PY" - "$DB" "$REPO" "$1" <<'PY'
import os, sqlite3, sys
db, repo, terms = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(db):
    print(f"(no memory DB yet at {db} -- the robot creates it on first run)"); sys.exit(0)
sys.path.insert(0, os.path.join(repo, "brain"))
try:
    from memory import _fts_query                       # reuse the SAME safe MATCH builder the robot uses
except Exception as e:
    print(f"could not import brain/memory.py: {e}", file=sys.stderr); sys.exit(1)
m = _fts_query(terms)
print(f"FTS match: {m!r}\n")
if not m:
    print("(no content words to search -- try more specific terms)"); sys.exit(0)
con = sqlite3.connect(db)
def show(title, sql):
    print(f"== {title} ==")
    try:
        rows = con.execute(sql, (m,)).fetchall()
    except sqlite3.Error as e:
        print(f"  (unavailable: {e})\n"); return
    if not rows:
        print("  (no matches)\n"); return
    for r in rows:
        print("  " + "  ".join("" if v is None else str(v) for v in r))
    print()
show("facts (score high = better)",
     "SELECT round(-rank,3) AS score, f.name, substr(coalesce(f.fact,''),1,18) AS rel, "
     "substr(coalesce(f.description,''),1,60) AS about FROM facts_fts JOIN facts f ON f.id=facts_fts.rowid "
     "WHERE facts_fts MATCH ? ORDER BY rank LIMIT 10")
show("scenes (score high = better)",
     "SELECT round(-rank,3) AS score, substr(o.notes,1,80) AS scene FROM scenes_fts "
     "JOIN observations o ON o.id=scenes_fts.rowid WHERE scenes_fts MATCH ? ORDER BY rank LIMIT 10")
con.close()
PY
}

reindex() {  # rebuild the FTS index from the base tables (via the real Memory.reindex_fts())
  "$PY" - "$DB" "$REPO" <<'PY'
import os, sys
db, repo = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "brain"))
try:
    from memory import Memory
except Exception as e:
    print(f"could not import brain/memory.py: {e}", file=sys.stderr); sys.exit(1)
mem = Memory(db_path=db)
nf, ns = mem.reindex_fts()
mem.close()
print(f"reindexed: {nf} fact row(s), {ns} scene row(s)." if (nf or ns) or mem._fts_ok
      else "FTS5 not available in this SQLite build -- retrieval uses recency only.")
PY
}

FACTS="SELECT id, datetime(ts,'unixepoch','localtime') AS taught, name, fact AS relationship, substr(description,1,50) AS description FROM facts ORDER BY ts DESC"
SEEN="SELECT id, datetime(ts,'unixepoch','localtime') AS seen, target, visible, horizontal, distance, round(confidence,2) AS conf FROM observations WHERE target IS NOT NULL AND target!='' ORDER BY ts DESC LIMIT 50"
SCENES="SELECT id, datetime(ts,'unixepoch','localtime') AS explored, substr(notes,1,90) AS scene FROM observations WHERE source='scene' AND notes IS NOT NULL AND notes!='' ORDER BY ts DESC LIMIT 30"
FACES="SELECT name, COUNT(*) AS photos, datetime(MAX(ts),'unixepoch','localtime') AS last_enrolled FROM faces GROUP BY name ORDER BY last_enrolled DESC"

case "${1:---facts}" in
  --facts|-f)               echo "== taught people/things (facts) =="; run "$FACTS" ;;
  --seen|--observations|-o) echo "== recently seen (observations, last 50) =="; run "$SEEN" ;;
  --scenes|-s)              echo "== recently explored (scene descriptions, last 30) =="; run "$SCENES" ;;
  --faces)                  echo "== enrolled Concierge faces (photos per person) =="; run "$FACES" ;;
  --all|--show-all|-a)      echo "== taught people/things (facts) =="; run "$FACTS"
                            echo; echo "== recently seen (observations, last 50) =="; run "$SEEN"
                            echo; echo "== recently explored (scenes, last 30) =="; run "$SCENES"
                            echo; echo "== enrolled Concierge faces (photos per person) =="; run "$FACES" ;;
  --search|--find)          [ $# -ge 2 ] || { echo "usage: $(basename "$0") --search \"<words>\""; exit 2; }
                            echo "== ranked memory search for: $2 =="; search "$2" ;;
  --reindex)                echo "== rebuilding FTS search index =="; reindex ;;
  --forget)                 [ $# -ge 2 ] || { echo "usage: $(basename "$0") --forget <name>"; exit 2; }
                            run "DELETE FROM facts WHERE lower(name)=lower(?)" "$2" ;;
  --clear-facts)            run "DELETE FROM facts" ;;
  --clear-scenes)           run "DELETE FROM observations WHERE source='scene'" ;;
  --clear-all)              read -r -p "Delete ALL facts AND observations from $DB? [y/N] " a
                            case "$a" in y|Y) run "DELETE FROM facts"; run "DELETE FROM observations";;
                                         *)   echo "aborted.";; esac ;;
  --query|-q)               [ $# -ge 2 ] || { echo "usage: $(basename "$0") --query \"<SQL>\""; exit 2; }
                            run "$2" ;;
  -h|--help)                grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//' ;;
  *)                        echo "unknown option: $1  (try: $(basename "$0") --help)"; exit 2 ;;
esac
