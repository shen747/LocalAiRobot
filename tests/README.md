# Tests

Fast checks that need no hardware and no network: they use fake clocks, sensors, cameras and web requests, so
a week of weather polling or a full visit runs in milliseconds.

```bash
./scripts/run_wake_tests.sh                                       # all of them
cd brain && BRAIN_DIR="$PWD" python ../tests/test_presence.py     # one, with its full output
```

The runner finds every `tests/test_*.py` by itself, hides their output and judges them by exit code. It sets
only `BRAIN_DIR`, so each test must pass with no other settings and must never touch the network. Each is a
plain script that exits non-zero on failure; there's no pytest.

| File | Checks |
|---|---|
| `test_presence.py` | PIR presence across its pulse train, and when presence expires |
| `test_probe.py` | the quick "anyone there?" look: its limits, a broken camera, a stuck sensor |
| `test_face.py` | the face follows the sensor without flicker, and a broken face never breaks a look |
| `test_halloween_look.py` | Halloween's person check: decoding, height cut, score setting, the real model runs, only "seen" scares, photos |
| `test_departure.py` | the greeting cooldown clears when one visitor leaves and a new one arrives |
| `test_wake_or.py` | wired and wireless motion sensors combined, with duplicates merged |
| `test_no_forward_refs.py` | no nested helper in `main()` is called above its definition (that crashes at runtime) |
| `test_weather_mapping.py` | every weather code maps to one of the seven conditions |
| `test_weather_cache.py` | the offline ladder: fetch, saved forecast, stale, dropped, midnight rollover |
| `test_weather_greeting.py` | time-of-day greetings and weather advice |
| `test_weather_screen.py` | the forecast card at several screen sizes, labels that always fit |
| `test_face_cache.py` | the enrolment cache rebuilds as soon as enrolments change |
| `test_zigbee.py` | the wireless sensor's edge cases: retained messages, going offline, timing |
| `test_gemini_thinking.py` | Gemini thinking settings, and the exact request body sent |
| `test_social.py` | social memory: sighting buckets, notes, expiry, and that turning it off changes nothing |
| `test_identify.py` | "who can you see" names everyone across a burst of frames, not just the first match |
| `test_identify_faces.py` | every face in a frame is recognised, not just the largest |
| `test_identify_gate.py` | "who" questions reach face recognition, not the vision model, and the question checks run in the right order |
| `test_doc_links.py` | every link and image in the Markdown points at a file in the repo, and every `#anchor` at a real heading |
