# Wake word training folder

A scratch folder for [training your own wake word](../../docs/openwakeword/openwake-word.md) on your own PC.
Running the notebook here, inside the repo, puts the finished model one `cp` away from [`models/`](../../models/).

Everything you create here (the `venv/`, the notebook, about 20 GB of audio, the intermediate clips) is ignored by
git; only this README and the `.gitignore` are tracked. Keep the `.onnx`: copy it to `models/` and commit it, as
the guide's "Put it on the robot" section describes. Then get the space back from the repo root:

```bash
git clean -dfx tools/wakeword-training     # deletes the scratch, keeps the two tracked files
```

Train on a PC or in Colab, never on the Pi, which can't install the tools. Mac users: use Colab.
