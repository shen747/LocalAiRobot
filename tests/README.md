# tests — wake-path and weather harnesses

Deterministic, hardware-free gates. Injected clock/sleep, stub sensors, a stub face and a fake HTTP
transport, so a full pulse-train scenario or a week of forecast polling runs in milliseconds with no GPIO,
camera, MQTT **or network**.

    cd brain && BRAIN_DIR="$PWD" python ../tests/test_presence.py     # one harness
    ./scripts/run_wake_tests.sh                                       # all of them

`run_wake_tests.sh` auto-discovers `tests/test_*.py`, so a new file needs no registration — but it
suppresses output and judges only the exit code, and it sets **only** `BRAIN_DIR`. Every harness therefore
has to pass with no other env key set, and must never touch the real network.

| File | Pins down |
|---|---|
| `test_presence.py` | Presence across a SEN0171 pulse train: sticky over the LOW gaps, expiry one hold after the last pulse, `PRESENCE_HOLD_S=0` off-switch, unreadable sensor -> `inf` |
| `test_probe.py` | Stage A floor / sensor-extension / ceiling, `capped` flag, backward compat, broken reader bails, dead-camera backoff |
| `test_face.py` | Face follows the sensor while retries continue; no flicker across pulse gaps; a throwing face never breaks a look |
| `test_departure.py` | Cooldown suppresses the same person, clears for a new arrival, boundary either side of the hold, no bogus clear without a falling edge |
| `test_wake_or.py` | CompositeWake: GPIO-only / both / wireless-only / neither; dedupe; `arm_cooldown` fan-out; counter summing |
| `test_no_forward_refs.py` | AST gate: no nested helper is *called above its own `def`* in the same scope — valid syntax that imports clean and then raises `UnboundLocalError` the first time the line runs (it shipped once, and the robot never left idle) |
| `test_weather_mapping.py` | Every WMO code → one of the seven canonical conditions; unknown/junk codes → `Cloudy`; no snow name can escape; the normalized-day contract, including a response too malformed to trust |
| `test_weather_cache.py` | The offline ladder with a fake transport: fetch → disk cache → serve-stale → dropped; the **midnight re-index**; blank LAT/LON and unknown providers stay inert; corrupt caches mean "no cache", never a raise; `on_change` fires every cycle *including with `None`*; blank env values fall back to defaults |
| `test_weather_greeting.py` | Time bands on an injected clock; advice per band with wet outranking temperature; `[Name]`/pronunciation parity with `concierge.greeting`; no forecast → opener only; `summary_note()` is exactly one sentence and never raises |
| `test_weather_screen.py` | The forecast card at three panel sizes: frame count, exact sizing, **pixel-identical chrome outside `icon_boxes()`** (the Turing partial-update guarantee), whole-timeline icon resampling, missing-icon placeholder, and the `Face.set_frames` seam |

Each exits non-zero on failure, so they gate a commit without pytest as a dependency.
