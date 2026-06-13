# `models/` — wake-word models

This folder holds the openWakeWord **`.onnx`** wake-word model(s) the robot listens for. The default,
**`hey_richard.onnx`**, is wired in [`config/robot.env`](../config/robot.env) as `ROBOT_WAKEWORD_MODEL`,
so a fresh clone wakes to **"hey Richard"** out of the box — no training and no copying needed.

- **The path is absolute** in the config (`~/LocalAiRobot/models/hey_richard.onnx`) on purpose: it then
  resolves the same under `scripts/run_me.sh` (CWD = repo root), the boot service (CWD = `brain/`), and a
  manual run. If you cloned the repo somewhere other than `~/LocalAiRobot`, update that one line.
- **A model is ~200 KB** — small enough to live in git. [`.gitattributes`](../.gitattributes) marks
  `*.onnx binary`, so it's never line-ending-mangled.
- **Make or swap your own** ("hey Richard" → any phrase you choose, voice-independent): follow
  [docs/opnewakeword/openwake-word.md](../docs/opnewakeword/openwake-word.md). Drop the new `.onnx` here,
  then either overwrite `hey_richard.onnx` to keep it the default, or point `ROBOT_WAKEWORD_MODEL` at the
  new filename. Commit it and the Pi gets it on `git pull`.
- **Stock models work too** — copy any openWakeWord `.onnx` (e.g. `hey_jarvis`) here and re-point the
  config. List what the installer fetched: `find brain/.venv -path "*openwakeword*" -name "*.onnx"`.
