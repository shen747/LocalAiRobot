#!/usr/bin/env bash
# check_config.sh -- after a git pull, list the settings that are new in config/robot.env.example and
# missing from your config/robot.env, plus any of yours the template no longer has. Read-only.
#
#   ./scripts/check_config.sh
set -uo pipefail
cd "$(dirname "$0")/.."
EX=config/robot.env.example
MINE=config/robot.env
[ -f "$MINE" ] || { echo "No config/robot.env yet. Make one with:  cp $EX $MINE"; exit 0; }

keys() { sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' "$1" | sort -u; }
new=$(comm -23 <(keys "$EX") <(keys "$MINE"))
old=$(comm -13 <(keys "$EX") <(keys "$MINE"))

if [ -n "$new" ]; then
  echo "New in the template. Until you add one, the robot uses its built-in default for it:"
  for k in $new; do grep -m1 "^$k=" "$EX" | cut -c1-160 | sed 's/^/  /'; done
  echo "To use a template value, copy its line into config/robot.env."
else
  echo "Your config/robot.env has every setting in the template."
fi
if [ -n "$old" ]; then
  echo
  echo "In your config but not in the template (probably no longer used, safe to delete):"
  printf '  %s\n' $old
fi
