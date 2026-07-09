#!/usr/bin/env bash
# rag.sh -- inspect / edit the robot's environment memory (the "RAG" SQLite store at ROBOT_MEMORY_DB).
# Uses the brain venv's Python (sqlite3 is stdlib -- nothing extra to install). Examples:
#   ./scripts/rag.sh                                 # taught people/things (the 'facts' table)
#   ./scripts/rag.sh --seen                          # recent things seen (the 'observations' table)
#   ./scripts/rag.sh --scenes                        # recent scene descriptions (from agentic explore)
#   ./scripts/rag.sh --all                           # facts + seen + scenes
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

FACTS="SELECT id, datetime(ts,'unixepoch','localtime') AS taught, name, fact AS relationship, substr(description,1,50) AS description FROM facts ORDER BY ts DESC"
SEEN="SELECT id, datetime(ts,'unixepoch','localtime') AS seen, target, visible, horizontal, distance, round(confidence,2) AS conf FROM observations WHERE target IS NOT NULL AND target!='' ORDER BY ts DESC LIMIT 50"
SCENES="SELECT id, datetime(ts,'unixepoch','localtime') AS explored, substr(notes,1,90) AS scene FROM observations WHERE source='scene' AND notes IS NOT NULL AND notes!='' ORDER BY ts DESC LIMIT 30"

case "${1:---facts}" in
  --facts|-f)               echo "== taught people/things (facts) =="; run "$FACTS" ;;
  --seen|--observations|-o) echo "== recently seen (observations, last 50) =="; run "$SEEN" ;;
  --scenes|-s)              echo "== recently explored (scene descriptions, last 30) =="; run "$SCENES" ;;
  --all|--show-all|-a)      echo "== taught people/things (facts) =="; run "$FACTS"
                            echo; echo "== recently seen (observations, last 50) =="; run "$SEEN"
                            echo; echo "== recently explored (scenes, last 30) =="; run "$SCENES" ;;
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
