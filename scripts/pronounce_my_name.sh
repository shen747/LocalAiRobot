#!/usr/bin/env bash
# pronounce_my_name.sh -- audition how Piper says a name, and (optionally) save that respelling.
#
# Piper mis-says some names (it reads "Ramesh" as "RAM-esh"). Feed it a phonetic RESPELLING and hear it in
# the robot's OWN voice -- once bare, once inside a greeting -- so you can nail the spelling without editing
# any file. Uses the SAME Piper binary + voice the brain uses, so what you hear is what Jarvis will say.
#
#   ./scripts/pronounce_my_name.sh Ruh-mesh                  # just SPEAK it (bare, then in a greeting)
#   ./scripts/pronounce_my_name.sh Ruh-mesh --name=Ramesh    # ...and set Ramesh's say-as to "Ruh-mesh" in enroll_all.sh
#
# With --name=<Name> it finds that person in ~/LocalAiRobot/enroll_all.sh (the batch enrolment script) and
# rewrites their say-as (the 3rd quoted field) to the respelling you just heard. It fails clearly if the name
# isn't in enroll_all.sh, or if enroll_all.sh is missing. Re-run `bash enroll_all.sh` on the Pi afterwards to
# push the change into the RAG db. Respell by SOUND, joined with hyphens ("Ruh-mesh") -- never space out single
# letters ("R uh m e sh"), which Piper reads as the alphabet.
set -uo pipefail
cd "$(dirname "$0")/.."   # repo root (= ~/LocalAiRobot on the Pi, where enroll_all.sh lives)

PRON=""
NAME=""
for arg in "$@"; do
  case "$arg" in
    --name=*)  NAME="${arg#--name=}" ;;
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} {exit}' "$0"; exit 0 ;;
    --*)       echo "pronounce_my_name: unknown option '$arg'" >&2; exit 2 ;;
    *)         if [ -z "$PRON" ]; then PRON="$arg";
               else echo "pronounce_my_name: unexpected extra argument '$arg' (quote a multi-word respelling)" >&2; exit 2; fi ;;
  esac
done
if [ -z "$PRON" ]; then
  echo "usage: $0 <RESPELLING> [--name=Name]" >&2
  echo "  e.g. $0 Ruh-mesh --name=Ramesh" >&2
  exit 2
fi
if [ -n "$NAME" ] && [ "$NAME" != "${NAME#--}" ]; then   # guards against --name=--foo typos
  echo "pronounce_my_name: '--name=$NAME' looks wrong (name starts with --)" >&2; exit 2
fi

# Any working python3 (the brain venv if present -- guaranteed on the Pi -- else the system one). Stdlib only.
PY="brain/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "pronounce_my_name: python3 not found" >&2; exit 1; }

PRON="$PRON" NAME="$NAME" ENROLL="enroll_all.sh" "$PY" - <<'PYEOF'
import os, re, subprocess, sys, tempfile, time

PRON   = os.environ["PRON"]
NAME   = os.environ.get("NAME", "")          # empty => speak only, don't touch enroll_all.sh
ENROLL = os.environ.get("ENROLL", "enroll_all.sh")

# SAME Piper binary + voice the robot's speak() uses (main.py cfg piper_bin/piper_voice defaults).
piper = os.path.expanduser(os.environ.get("ROBOT_PIPER_BIN",   "~/piper/piper/piper"))
voice = os.path.expanduser(os.environ.get("ROBOT_PIPER_VOICE", "~/piper/en_US-lessac-medium.onnx"))
# Multi-speaker voices: audition the SAME speaker the robot speaks with, or you tune the respelling
# against a voice nobody will ever hear (speaker 0 on en_US-arctic is awb, a Scot).
_spk  = (os.environ.get("ROBOT_PIPER_SPEAKER", "") or "").strip()
SPK   = ["--speaker", _spk] if _spk.isdigit() else []
tmp   = tempfile.gettempdir()

def say(text):
    """Synthesize with Piper and play via aplay (same ALSA default as the robot voice). Returns True on sound."""
    wav = os.path.join(tmp, f"pron-{os.getpid()}-{int(time.time()*1000)}.wav")
    try:
        syn = subprocess.run([piper, "-m", voice] + SPK + ["--output_file", wav], input=text.encode("utf-8"),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    except FileNotFoundError:
        print(f"   !! piper not found at {piper} -- set ROBOT_PIPER_BIN (run ./scripts/install_stack_pi5.sh)")
        return False
    if syn.returncode != 0:
        print(f"   !! piper failed: {syn.stderr.decode('utf-8','replace').strip()[-120:]}")
        return False
    pb = subprocess.run(["aplay", wav], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
    if pb.returncode != 0:
        print(f"   !! aplay failed: {pb.stderr.strip()[-120:]}  (check ~/.asoundrc / `aplay -l`)")
        return False
    try:
        os.remove(wav)
    except OSError:
        pass
    return True

# 1) SPEAK: bare, then inside a greeting (what a visitor actually hears).
print(f">> '{NAME or PRON}' spoken as '{PRON}'   (voice: {voice}{' speaker ' + _spk if SPK else ''})")
ok = say(PRON)
time.sleep(0.9)
ok = say(f"Hello, how are you doing, {PRON}?") or ok

# 2) No --name -> just an audition; done.
if not NAME:
    sys.exit(0 if ok else 1)

# 3) --name given -> save the respelling as that person's say-as in enroll_all.sh.
if not os.path.exists(ENROLL):
    print(f"\n!! {ENROLL} not found at {os.path.abspath(ENROLL)}.")
    print("   Generate it on your PC:  ./scripts/create_enrollments.sh")
    print("   then copy it to the Pi:  scp enroll_all.sh <pi>:~/LocalAiRobot/")
    sys.exit(1)

# Match an enrolment line and capture: (1) prefix, (2) NAME, (3) ' \"desc\"', (4) optional ' \"say-as\"', (5) rest(photos).
LINE = re.compile(r'^(\s*\./scripts/enroll_person\.sh\s+(?:--\S+\s+|\d+\s+)*)"([^"]*)"(\s+"[^"]*")(\s+"[^"]*")?(.*)$')
with open(ENROLL, encoding="utf-8") as fh:
    lines = fh.readlines()

names, hits = [], []
for i, ln in enumerate(lines):
    m = LINE.match(ln)
    if not m:
        continue
    names.append(m.group(2))
    if m.group(2) == NAME or m.group(2).lower() == NAME.lower():
        hits.append((i, m))

if not hits:
    print(f"\n!! '{NAME}' is not enrolled in {ENROLL}.")
    if names:
        print("   Enrolled names (the 1st quoted field of each enroll_person.sh line):")
        print("     " + ", ".join(names))
    else:
        print("   (No ./scripts/enroll_person.sh lines found -- is this the right file?)")
    sys.exit(1)

sayas = f'"{PRON}"'
for i, m in hits:
    prefix, nm, desc, old, rest = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    if old is None:                                    # older 2-field line -> INSERT a say-as after the desc
        lines[i] = f'{prefix}"{nm}"{desc} {sayas}{rest}\n'
    else:                                              # 3-field line -> REPLACE the say-as (keep its spacing)
        ws = old[:len(old) - len(old.lstrip())]
        lines[i] = f'{prefix}"{nm}"{desc}{ws}{sayas}{rest}\n'

with open(ENROLL, "w", encoding="utf-8") as fh:
    fh.writelines(lines)

canon = hits[0][1].group(2)
print(f"\n>> Set {canon}'s say-as to \"{PRON}\" in {ENROLL}:")
for i, _ in hits:
    print("   " + lines[i].rstrip())
print(f"   Apply it to the RAG db on the Pi:  bash {ENROLL}   (idempotent -- only {canon} changes).")
sys.exit(0)
PYEOF
