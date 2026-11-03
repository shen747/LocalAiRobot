#!/usr/bin/env bash
# enable_persistent_journal.sh -- make the systemd journal survive reboots, the ROBUST way.
#
# WHY: the (headless) boot service's journal is the ONLY record of why it crashed / brown-out-rebooted --
# but Raspberry Pi OS ships journald as Storage=auto, which persists ONLY IF /var/log/journal already exists
# when journald starts. That's a timing trap: a robot can run for weeks RAM-only (every reboot wipes the
# log), and you only discover it after a crash you can no longer read. (Seen in the field: /var/log/journal
# present but EMPTY, journald still logging to RAM.)
#
# FIX: set Storage=persistent EXPLICITLY via a drop-in, so journald always creates + uses /var/log/journal
# regardless of dir-existence timing; then restart journald and VERIFY a real journal file appears. Bounded
# by SystemMaxUse so DEBUG-level logs can't fill the disk (journald auto-rotates within the cap). Idempotent;
# honours ROBOT_PERSIST_JOURNAL=0 (skip). Undo with:
#   sudo rm /etc/systemd/journald.conf.d/localairobot-persistent.conf && sudo systemctl restart systemd-journald
set -uo pipefail

if [ "${ROBOT_PERSIST_JOURNAL:-1}" = 0 ]; then
  echo ">> persistent journal: skipped (ROBOT_PERSIST_JOURNAL=0)."
  exit 0
fi
command -v journalctl >/dev/null 2>&1 || { echo ">> persistent journal: journalctl not found -- skipping (not systemd?)."; exit 0; }

DROPIN=/etc/systemd/journald.conf.d/localairobot-persistent.conf
MAXUSE="${ROBOT_JOURNAL_MAX_USE:-500M}"   # cap on-disk journal size (DEBUG logs are chatty; journald rotates within this)

if ! sudo mkdir -p /etc/systemd/journald.conf.d /var/log/journal; then
  echo "!! persistent journal: could not create dirs (sudo?) -- skipping (journal stays in RAM)." >&2
  exit 0
fi
# Write the drop-in (Storage=persistent is the part that actually fixes the RAM-only trap).
printf '%s\n' \
  '# LocalAiRobot: keep the journal on disk so a boot-service crash / brown-out is readable next boot.' \
  '# (managed by scripts/enable_persistent_journal.sh -- rm this file + restart journald to undo)' \
  '[Journal]' \
  'Storage=persistent' \
  "SystemMaxUse=${MAXUSE}" | sudo tee "$DROPIN" >/dev/null

sudo systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
sudo systemctl restart systemd-journald >/dev/null 2>&1 || true

# VERIFY: with Storage=persistent a real journal file should exist almost immediately. If it doesn't, say so
# plainly instead of the old silent no-op that let a robot look configured while still logging to RAM.
sleep 1
if ls /var/log/journal/*/system.journal >/dev/null 2>&1; then
  echo ">> persistent journal: ON (Storage=persistent, cap ${MAXUSE}, /var/log/journal)."
  echo "   read a PREVIOUS boot's brain log with:  journalctl -u robot.service -b -1"
else
  echo "!! persistent journal: drop-in written but no journal file appeared yet -- it should populate on the"
  echo "   next boot. Re-check with:  ls -la /var/log/journal/*/   and   journalctl --list-boots"
fi
