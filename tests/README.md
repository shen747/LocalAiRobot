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
| `test_halloween_look.py` | Halloween's doorway check on real JPEGs: an empty porch, a whole-frame exposure shift and a speck of noise are never movement; a visitor walking in is; the reference is two frames back; **only `seen` may spook** — a camera that returns nothing is `blind`, never `seen`; a stuck-HIGH PIR stays bounded by `LOOK_MAX_S`; the scare photos save as `-spook.jpg` / `-spook-reaction.jpg`, the reaction shot timed just past the camera's warm window |
| `test_departure.py` | Cooldown suppresses the same person, clears for a new arrival, boundary either side of the hold, no bogus clear without a falling edge |
| `test_wake_or.py` | CompositeWake: GPIO-only / both / wireless-only / neither; dedupe; `arm_cooldown` fan-out; counter summing |
| `test_no_forward_refs.py` | AST gate: no nested helper is *called above its own `def`* in the same scope — valid syntax that imports clean and then raises `UnboundLocalError` the first time the line runs (it shipped once, and the robot never left idle) |
| `test_weather_mapping.py` | Every WMO code → one of the seven canonical conditions; unknown/junk codes → `Cloudy`; no snow name can escape; the normalized-day contract, including a response too malformed to trust |
| `test_weather_cache.py` | The offline ladder with a fake transport: fetch → disk cache → serve-stale → dropped; the **midnight re-index**; blank LAT/LON and unknown providers stay inert; corrupt caches mean "no cache", never a raise; `on_change` fires every cycle *including with `None`*; blank env values fall back to defaults |
| `test_weather_greeting.py` | Time bands on an injected clock; advice per band with wet outranking temperature; `[Name]`/pronunciation parity with `concierge.greeting`; no forecast → opener only; `summary_note()` is exactly one sentence and never raises |
| `test_weather_screen.py` | The forecast card at three panel sizes: frame count, exact sizing, **pixel-identical chrome outside `icon_boxes()`** (the Turing partial-update guarantee), whole-timeline icon resampling, missing-icon placeholder, condition labels that always fit one line (343 combos × 4 sizes), and the `Face.set_frames` seam |
| `test_face_cache.py` | The enrolment cache: built once and served from memory, **rebuilt the moment `faces_version()` moves** (the staleness risk), scores identical to the old per-row loop, and a `Memory` without the counter degrading to rebuild-every-time |
| `test_zigbee.py` | The four defects that made the Zigbee source unsafe to enable — a retained CLEAR must update state or the sensor goes permanently deaf; `presence_age` must **age** or it drowns out the wired PIR in `CompositeWake`'s `min()`; OFFLINE must clear occupancy; the departure must be back-dated past the sensor's own occupancy timeout |
| `test_gemini_thinking.py` | Gemini thinking config: a **level replaces the budget, never both** (Gemini 400s on that), blank level falls back to the budget, `auto` sends nothing, vision and text keys never cross, and — through a fake transport — the body actually POSTed to Robotics-ER 2 |
| `test_social.py` | Social Memory: calendar-day sighting buckets (not elapsed hours), one-follow-on composition, plan/job/weather precedence, note expiry and per-kind replace, per-person retention, the extractor rejecting anything that isn't a short noun phrase, and the **flag-off pass-through** |

Each exits non-zero on failure, so they gate a commit without pytest as a dependency.
