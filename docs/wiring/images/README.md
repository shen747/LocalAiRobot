# Wiring images — photos + rendered diagrams

[`pwm-esc-servo-wiring.md`](../pwm-esc-servo-wiring.md) embeds everything from **this** folder.

**Part photos you supply** (JPG):

| Filename | Shot |
|---|---|
| `esc.jpg` | ESC close-up: red/black battery leads, blue/yellow motor leads, the 3-pin signal lead. |
| `servo.jpg` | Front steering servo plugged onto the 2nd output column (ch1). |
| `battery.jpg` | 2S LiPo with the inline kill switch on its lead. |

**Rendered diagrams** (PNG — generated from [`../diagrams/`](../diagrams/), output here):

| Filename | Source |
|---|---|
| `wiring-overview.png` | `../diagrams/wiring-overview.puml` |
| `pca9685-board.png` | `../diagrams/pca9685-board.puml` |
| `pca9685-channels.png` | `../diagrams/pca9685-channels.puml` |

Render them all into this folder:

```bash
plantuml -o ../images ../diagrams/*.puml      # from docs/wiring/, or:
plantuml -o ../images docs/wiring/diagrams/*.puml
```
