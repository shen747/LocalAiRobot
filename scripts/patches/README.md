# ESC config patches

Each script here writes one ESC's **bench-tested defaults** into `config/robot.env` (neutral, dead-band
floors/ceils, reverse mode), then reminds you to confirm the PCA9685 channel. Run the one that matches
your ESC **after** `install_stack.sh`, as **step 3** of setup:

```bash
./scripts/patches/hb_qr_1060.sh      # HobbyWing QuicRun WP-1060 (F/R jumper: Mode cap OFF)
```

They're **idempotent** (re-runnable) — each key is rewritten in place with a `# [<esc>]` provenance tag.
Only the ESC-electrical keys are touched; wiring choices (`ROBOT_ESC_CHANNEL`, steering angles) stay at the
repo defaults and the patch just prints the channel line for you to confirm.

**Naming:** `<esc-model>.sh`, lowercased, e.g. `hb_qr_1060.sh` = **h**obby**b**wing **q**uic**r**un **1060**.
Adding a new ESC? Copy `hb_qr_1060.sh`, rename it, and change the `set_kv` values to that ESC's tested
numbers. If two builds of the same ESC need different tuning (e.g. a different servo or channel), suffix
the extra bit, e.g. `hb_qr_1060_hv.sh`.

> Camera setup is **not** here — the IMX519 autofocus overlay is handled automatically by
> `scripts/install_stack.sh` (see the camera row in the main [README](../../README.md) parts list).
