# `models/` — wake-word models

This folder holds the openWakeWord **`.onnx`** wake-word model(s) the robot listens for, pointed at by
`ROBOT_WAKEWORD_MODEL` in [`config/robot.env`](../config/robot.env). Two ship:

- **`hey_jarvis.onnx`** — a stock openWakeWord model, **the wired default**: reliable out of the box, so a
  fresh clone wakes to *"hey jarvis"* with no training.
- **`hey_richard.onnx`** — a **custom** model for the robot's own name. Synthetic-voice training makes it
  less reliable than the stock one, so it's bundled to **retrain stronger** and switch to when ready.

> **Licence.** `hey_jarvis.onnx` is openWakeWord's own pre-trained model and keeps its licence —
> [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (attribution, **non-commercial**,
> share-alike) — not this repo's MIT licence. `hey_richard.onnx` was trained for this robot with
> openWakeWord's training tools.

Notes:
- **The path is absolute** in the config (e.g. `~/LocalAiRobot/models/hey_jarvis.onnx`) on purpose: it
  resolves the same under `scripts/run_me.sh` (CWD = repo root), the boot service (CWD = `brain/`), and a
  manual run. If you cloned the repo somewhere other than `~/LocalAiRobot`, update that one line.
- **These are small** (~200 KB–1.3 MB) — fine for git. [`.gitattributes`](../.gitattributes) marks
  `*.onnx binary`, and `.gitignore` has a `!models/*.onnx` exception (every other `.onnx` stays ignored).
- **Make or swap your own** (any phrase you choose, voice-independent): follow
  [docs/openwakeword/openwake-word.md](../docs/openwakeword/openwake-word.md). Drop the new `.onnx` here and
  point `ROBOT_WAKEWORD_MODEL` at it. Commit it and the Pi gets it on `git pull`.
- **More stock models**: list what the installer fetched with
  `find brain/.venv -path "*openwakeword*" -name "*.onnx"`, or grab one from openWakeWord's GitHub releases.
