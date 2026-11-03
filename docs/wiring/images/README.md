# Wiring images

The pictures [`pwm-esc-servo-wiring.md`](../pwm-esc-servo-wiring.md) uses.

**Part photos you can add** (JPG):

| File | Shot |
|---|---|
| `esc.jpg` | the ESC close up: battery leads, motor leads and the three-wire signal lead |
| `servo.jpg` | the steering servo plugged onto its PCA9685 column |
| `battery.jpg` | the LiPo with the kill switch on its lead |

**Diagrams**, drawn from the PlantUML sources in [`../diagrams/`](../diagrams/):

| File | Source |
|---|---|
| `wiring-overview.png` | `../diagrams/wiring-overview.puml` |
| `pca9685-board.png` | `../diagrams/pca9685-board.puml` |
| `pca9685-channels.png` | `../diagrams/pca9685-channels.puml` |

Render them all into this folder, from the repo root:

```bash
plantuml -o ../images docs/wiring/diagrams/*.puml
```
