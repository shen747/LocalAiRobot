#!/usr/bin/env bash
# generate_spook_stingers.sh -- synthesize LOUD jump-scare stingers into sounds/spook_sounds/*.wav.
# These are the earcon played (one at random) the instant a trick-or-treater trips the PIR in
# ROBOT_HALLOWEEN_MODE=1 -- BEFORE the spoken spooky phrase. Unlike the Piper voice cues, these are
# synthetic, peak-amplitude, soft-clipped noise/tone hits: as LOUD and sudden as the hardware allows
# (they play through aplay at the ALSA mixer's 100%). Pure Python stdlib -- no Pillow, no Piper needed.
#
#   ./scripts/generate_spook_stingers.sh            # writes the 3 stingers; skips any that already exist
#   ./scripts/generate_spook_stingers.sh --force    # overwrite existing stingers
#
# They're committed, so a fresh clone already has them. Prefer your own SFX? Drop .wav files in
# sounds/spook_sounds/ (one is picked at random) -- but keep them peak-normalized to stay loud.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Any Python 3 works (stdlib only): the tools venv, the Pi brain venv, then whatever's on PATH.
PY=""
for cand in "$HOME/.localairobot-tools/venv/bin/python" "$REPO/brain/.venv/bin/python" \
            "$(command -v python3 || true)" "$(command -v python || true)" "$(command -v py || true)"; do
  if [ -n "$cand" ] && "$cand" -c 'import wave' >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then echo "No Python 3 found on PATH." >&2; exit 1; fi

LAR_REPO="$REPO" "$PY" - "$@" <<'PYEOF'
import argparse, array, math, os, random, wave

repo = os.environ["LAR_REPO"]
ap = argparse.ArgumentParser()
ap.add_argument("--force", action="store_true", help="overwrite existing stingers")
ap.add_argument("--out", default=os.path.join(repo, "sounds", "spook_sounds"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

SR = 44100
PEAK = 0.98                      # of full scale -- as hot as we go without hard digital clipping
random.seed(1031)               # deterministic noise (reproducible byte-for-byte run to run)

def _sat(x, drive=1.9):
    """Soft-clip: pushes RMS up (perceptually louder) while taming hard-clip crackle."""
    return math.tanh(x * drive)

def _env(i, n, atk_s=0.004, rel_s=0.07):
    """Fast attack, near-full sustain, short release -- keeps it LOUD throughout, no click at the edges."""
    t, dur = i / SR, n / SR
    return max(0.0, min(1.0, t / atk_s) * min(1.0, (dur - t) / rel_s))

def _save(name, samples):
    lim = int(PEAK * 32767)
    buf = array.array("h", (max(-lim, min(lim, int(_sat(s) * lim))) for s in samples))
    path = os.path.join(a.out, name)
    if os.path.exists(path) and not a.force:
        print("skip (exists):", os.path.relpath(path, repo)); return
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(buf.tobytes())
    print("wrote:", os.path.relpath(path, repo), "(%.2fs)" % (len(samples) / SR))

# 1) SCREECH -- a detuned metallic saw diving from a scream to a growl, over a bed of noise.
def screech(dur=1.7):
    n = int(SR * dur); out = [0.0] * n; p1 = p2 = 0.0
    for i in range(n):
        k = i / n
        f = 2600 * (1 - k) ** 1.5 + 190          # 2790 -> ~190 Hz descending
        p1 += f / SR; p2 += (f * 1.007) / SR     # slight detune = metallic beating
        saw = ((2 * (p1 % 1.0) - 1) + (2 * (p2 % 1.0) - 1)) * 0.5
        noise = random.uniform(-1, 1) * 0.35
        out[i] = _env(i, n) * (0.85 * saw + noise)
    return out

# 2) BOOM -- a full-scale noise CRACK, then a low sine boom with a long decaying tail.
def boom(dur=1.6):
    n = int(SR * dur); out = [0.0] * n; p = 0.0
    for i in range(n):
        t = i / SR; k = i / n
        crack = random.uniform(-1, 1) * math.exp(-t / 0.05)      # sharp initial hit (~50 ms)
        f = 120 * (1 - k) + 42                                    # 120 -> 42 Hz sub-boom
        p += f / SR
        low = math.sin(2 * math.pi * p) * math.exp(-t / 0.55)    # deep body, ~0.55 s decay
        out[i] = _env(i, n, atk_s=0.001) * (0.95 * crack + 1.1 * low)
    return out

# 3) SHRIEK -- Psycho-style dissonant high stabs, hard tremolo-gated, gritty. Pure nightmare fuel.
def shriek(dur=1.8):
    n = int(SR * dur); out = [0.0] * n
    for i in range(n):
        t = i / SR
        stab = 0.5 + 0.5 * math.copysign(1.0, math.sin(2 * math.pi * 11 * t))   # ~11 Hz on/off gate
        tone = (math.sin(2 * math.pi * 1720 * t)
                + 0.7 * math.sin(2 * math.pi * 1810 * t)      # a minor-second beside it = dissonance
                + 0.5 * math.sin(2 * math.pi * 2630 * t))
        grit = random.uniform(-1, 1) * 0.25
        out[i] = _env(i, n) * stab * (0.7 * tone / 2.2 + grit)
    return out

_save("stinger_screech.wav", screech())
_save("stinger_boom.wav", boom())
_save("stinger_shriek.wav", shriek())
PYEOF
