#!/usr/bin/env bash
# Run every wake-path harness. No hardware, no pytest -- each test is a plain script that exits non-zero
# on failure, so this works on the robot and on a dev box identically.
#   ./scripts/run_wake_tests.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Pick an interpreter that actually RUNS. `command -v python3` is not enough: on Windows/Git-Bash it
# resolves to the Microsoft Store shim, which exists, is executable, and does nothing but print an advert
# -- so every test "fails" identically and the failure looks like the code. Probe each candidate.
PY=""
for cand in "$REPO/brain/.venv/bin/python" "$REPO/brain/.venv/Scripts/python.exe" py python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "no working python found (tried the venv, py, python3, python)" >&2; exit 2; }
if [ -t 1 ]; then GRN=$'\033[1;32m'; RED=$'\033[1;31m'; RST=$'\033[0m'; else GRN=''; RED=''; RST=''; fi
fail=0
for t in "$REPO"/tests/test_*.py; do
  name="$(basename "$t" .py)"
  if BRAIN_DIR="$REPO/brain" "$PY" "$t" >/dev/null 2>&1; then
    printf '  %-18s %sPASS%s\n' "$name" "$GRN" "$RST"
  else
    printf '  %-18s %sFAIL%s  -- rerun for detail:  BRAIN_DIR=%s/brain %s %s\n' \
           "$name" "$RED" "$RST" "$REPO" "$PY" "$t"
    fail=1
  fi
done
[ "$fail" = 0 ] && echo "${GRN}all wake-path harnesses green${RST}" || echo "${RED}FAILURES above${RST}"
exit $fail
