# tests — wake-path harnesses

Deterministic, hardware-free gates for the Concierge wake path. Injected clock/sleep, stub sensors and a
stub face, so a full pulse-train scenario runs in milliseconds with no GPIO, camera or MQTT.

    cd brain && BRAIN_DIR="$PWD" python ../tests/test_presence.py     # one harness
    ./scripts/run_wake_tests.sh                                       # all five

| File | Pins down |
|---|---|
| `test_presence.py` | Presence across a SEN0171 pulse train: sticky over the LOW gaps, expiry one hold after the last pulse, `PRESENCE_HOLD_S=0` off-switch, unreadable sensor -> `inf` |
| `test_probe.py` | Stage A floor / sensor-extension / ceiling, `capped` flag, backward compat, broken reader bails, dead-camera backoff |
| `test_face.py` | Face follows the sensor while retries continue; no flicker across pulse gaps; a throwing face never breaks a look |
| `test_departure.py` | Cooldown suppresses the same person, clears for a new arrival, boundary either side of the hold, no bogus clear without a falling edge |
| `test_wake_or.py` | CompositeWake: GPIO-only / both / wireless-only / neither; dedupe; `arm_cooldown` fan-out; counter summing |

Each exits non-zero on failure, so they gate a commit without pytest as a dependency.
