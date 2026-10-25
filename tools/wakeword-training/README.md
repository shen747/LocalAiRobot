# `tools/wakeword-training/` — local scratch for training a wake word

This is the working folder for **[Training your own wake word](../../docs/openwakeword/openwake-word.md)**
(Path B — local PC / Windows WSL). You run the openWakeWord notebook **here**, inside your repo clone, so
the trained model lands one `cp` away from [`models/`](../../models/) — no separate `~/oww` workspace and
no `scp` to the Pi.

Everything you create in this folder — the `venv/`, the downloaded `.ipynb`, ~20 GB of audio, the
intermediate `my_custom_model/` clips and features — is **gitignored**; only this README and the
`.gitignore` are tracked. The single artifact you keep is the `.onnx`, which you copy to
[`models/`](../../models/) and commit (see the guide's **"Put it on your robot"**).

**Reclaim the ~20 GB when you're done** — from the repo root:
```bash
git clean -dfx tools/wakeword-training     # wipes all the scratch, keeps these two tracked files
```

> Training is a PC/Colab job, **never on the Pi** (its ARM chip can't install the tools). On the Pi this
> folder just sits here harmlessly. Mac users: train in **Colab** (Path A) instead.
