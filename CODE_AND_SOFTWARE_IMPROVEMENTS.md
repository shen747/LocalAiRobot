# LocalAiRobot — Code & Software Improvements (Planning)

> **Phase 1 deliverable: planning only. No code or diagram files were changed in producing this document.**
> It is a gap analysis of the [brenpoly *be-more-agent* / BMO](ref/be-more-agent-main/) project (the subject of the three YouTube videos) against **our** LocalAiRobot, plus a detailed design for a **"Richard" wake word**, and an inventory of which **`.puml` diagrams** would need updating (and exactly how) when we implement.

**Date:** 2026-06-06
**Branch context:** `add_camera_lcd_and_more_ultrsonic_sensors`
**Scope:** Improvements relevant to *this* project (an offline 1/10 RC crawler on a Pi 5 16GB). **Explicitly excluded by request:** custom TTS voice training/cloning, the Odradek/Death Stranding project, and Steam streaming via Moonlight/Sunshine. See [Out of scope](#out-of-scope).

> Tooling note: the jCodemunch MCP code-navigation server was **not connected** in this session, so the codebase was read with the standard file tools. Findings below cite real `file:line` locations from the current tree.

---

## 1. TL;DR — prioritised improvements

Legend — **Effort:** S(mall ≈ ½–1 day) / M(edium) / L(arge). **Offline-safe:** does it preserve "pull the Wi-Fi and it still works"?

| # | Improvement | From video | Value | Effort | Offline-safe | Touches HW? |
|---|---|---|---|---|---|---|
| **1** | **Wake word "Richard"** (openWakeWord) — replaces, but keeps, ENTER push-to-talk; unlocks headless `robot.service` | V1 | ⭐⭐⭐ (your #1 ask) | **M** | ✅ yes | reuses existing USB mic |
| **2** | **Model orchestration**: boot **warmup** + `keep_alive` + a single `model_lock` (thin `inference.py`) | V1/V2 | ⭐⭐⭐ (fixes a real latent bug; foundation for #5/#7) | **S–M** | ✅ yes | no |
| **3** | **Audio cues**: ack / greeting / thinking earcons (non-verbal feedback during slow inference) | V1 | ⭐⭐⭐ (huge UX win on a slow robot) | **S** | ✅ yes | no |
| **4** | **Animated faces** (PNG frame sequences per state) | V1 | ⭐⭐ (visual polish, low risk) | **S–M** code + ongoing art | ✅ yes | no |
| **5** | **Optional dedicated VLM** (Moondream2) to decouple vision latency | V1 | ⭐⭐ (cuts the tens-of-seconds vision stall) | **S** code / **M** ops | ✅ yes | no (RAM cost) |
| **6** | **Optional web search** (DuckDuckGo) — companion Q&A only | V1 | ⭐ (low; conflicts with offline) | **S** | ❌ **breaks offline** | no |
| **7** | **AI accelerator / NPU** (Hailo AI HAT+) to offload the VLM/LLM | V2 | ⭐⭐ potential, but speculative | **L** | ✅ yes | **yes (new HAT)** |

**Recommended build order (by dependency, not by headline value):** #2 → #3 → #1 → #4 → (#5) → (#6) → (#7).
Items #6 and #7 are explicitly optional/deferred (see their sections for *why*).

---

## 2. Method

1. **Deep scan of our codebase** — every module under [brain/](brain/) (`main.py`, `driver.py`, `camera.py`, `vision.py`, `memory.py`, `behavior.py`, `face.py`), [config/robot.env](config/robot.env), [scripts/install_stack.sh](scripts/install_stack.sh), [scripts/robot.service](scripts/robot.service), [docs/assembly.md](docs/assembly.md), and the four [docs/images/*.puml](docs/images/) sources.
2. **Reference extraction** — the brenpoly implementation in [ref/be-more-agent-main/](ref/be-more-agent-main/): all 1081 lines of `agent.py`, `config.json`, `setup.sh`, `requirements.txt`, and the `faces/` + `sounds/` asset layout.
3. **Video-feature mapping** — the three-video synthesis (build → voice/brain upgrade → movement + new project) mapped to current capability and labelled relevant / out-of-scope.
4. **Per-area design + adversarial review** — each candidate improvement was designed against the real code (with `file:line` citations) and then run through a completeness/accuracy/scope critic. Corrections from that critic are folded in here.

---

## 3. Current architecture snapshot (what we have today)

A fully-serial, push-to-talk loop, everything local on the Pi 5:

```
[ENTER over SSH] → arecord (fixed 4 s) → whisper.cpp (ggml-base.en)
   → gemma3:4b via Ollama (text-intent JSON; SAME model also reads camera frames)
   → validate/clamp intent → dispatch {DRIVE | STOP | EXPLORE | FIND}
   → Piper TTS reply ;  PCA9685 → ESC + steering servo ;  face panel reflects state
```

Key facts that shape every design below:

- **Push-to-talk is literally `input("\n[ENTER]=talk q=quit > ")`** at [main.py:270](brain/main.py#L270). It blocks the whole loop on stdin → **`scripts/robot.service` (no TTY) can never trigger a turn**; the brain only works hand-run over SSH.
- **One model does two jobs.** `query_ollama` ([main.py:134-159](brain/main.py#L134-L159)) and `vision._ollama_image` ([vision.py:54-73](brain/vision.py#L54-L73)) both POST to the *same* `cfg["ollama_url"]` with the *same* `cfg["model"]` (`gemma3:4b`). There is **no application-level lock** around the model — serialisation is an accident of the single-threaded loop, not enforced (contrast `audio_lock` at [main.py:87](brain/main.py#L87), which *does* serialise mic vs. speaker).
- **Vision is very slow on the Pi 5 CPU:** the timeout is **600 s** (`ROBOT_VISION_TIMEOUT_S`, [vision.py:47-51](brain/vision.py#L47-L51)); the header says "tens of seconds to minutes per image" ([vision.py:5-7](brain/vision.py#L5-L7)). This forces vision to be a "park-and-look," and bounds explore/find to `ROBOT_EXPLORE_MAX_ITERS=6`.
- **Cold-load risk:** nothing warms the model at boot, so the first command pays the full load cost — the repo already warns about it in [config/robot.env:11](config/robot.env#L11) ("raise if the first-command cold model load times out"). On timeout the loop **fail-safes to STOP** ([main.py:292-295](brain/main.py#L292-L295)).
- **Audio is half-duplex** under `audio_lock`: `arecord` (record) and `aplay` (Piper playback) never overlap. **The only audio output that exists today is Piper TTS** ([main.py:162-184](brain/main.py#L162-L184)) — there are no beeps/earcons anywhere.
- **Face** ([brain/face.py](brain/face.py)) is **100% procedural** (PIL `ImageDraw`, no sprite assets, [face.py:5](brain/face.py#L5)) with **no animation** — `set_expression` renders once per change and early-outs on same-state ([face.py:228-240](brain/face.py#L228-L240)). 8 states: `idle, listening, thinking, talking, exploring, happy, confused, alarmed` ([face.py:25](brain/face.py#L25)). Backends auto-ladder `turing → framebuffer → simulated`, all best-effort/never-raise.
- **Safety lives in the driver, not the brain:** the 20 Hz `_control_loop` ([driver.py:303-350](brain/driver.py#L303-L350)) returns the ESC to neutral on STOP/error/obstacle/timeout, independent of Ollama. There is **no hardware watchdog**.
- **Dry-run discipline is excellent and reusable:** `ROBOT_DRY_RUN` swaps hardware for fakes that *log* intended actions ([driver.py:52-53](brain/driver.py#L52-L53)), with `ROBOT_FAKE_VISION` / `ROBOT_FAKE_DISTANCE_CM` scripting. Every new module below should follow this same lazy-import + `available` flag + fake-fallback + structured-logger pattern.

---

## 4. Feature map: 3 videos → our capability → gap

| Video feature | brenpoly / BMO | LocalAiRobot today | Gap → action |
|---|---|---|---|
| **Wake word** (Open Wake Word + whisper) | ✅ `wakeword.onnx` (stock "Hey Jarvis"), hybrid WAKE/PTT/CLI | ❌ none (README "Is not → wake-word") | **#1 — build "Richard"** |
| **Separate LLM + VLM** (Gemma 3 + Moondream 2) | ✅ `gemma3:1b` text + `moondream` vision | ⚠️ one `gemma3:4b` for both | **#5 — optional Moondream2** |
| **Model warmup / keep-alive** | ✅ `warm_up_logic`, `keep_alive=-1`, `WARMUP` state | ❌ cold-load on first command | **#2 — warmup + keep-alive** |
| **Async device management** (the `/dev/hailo0` exclusivity warning) | ⚠️ serialised via single worker thread | ⚠️ serialised only by accident (no lock) | **#2 — `model_lock`** |
| **Thinking / ack / greeting sounds** | ✅ `sounds/{greeting,ack,thinking,error}` | ❌ none (only Piper TTS) | **#3 — audio cues** |
| **Animated reactive faces** (PNG sequences) | ✅ `faces/<state>/NN.png` | ⚠️ static, procedural | **#4 — animated faces** |
| **DuckDuckGo web search** | ✅ tool-action → DDG → summarise | ❌ none (offline by design) | **#6 — optional, default OFF** |
| **AI accelerator / NPU** (Hailo AI HAT+, M5Stack) | ✅ compared/benchmarked | ❌ CPU only | **#7 — speculative HW** |
| Movement / servos | ✅ added in V3 | ✅ **already have** (Ackermann drive+steer) | — none |
| Custom Piper voice (V2) | ✅ trained "Amy" | n/a | **OUT OF SCOPE** |
| Steam via Moonlight/Sunshine (V3) | ✅ | n/a | **OUT OF SCOPE** |
| Odradek scanner (V3) | new project | n/a | **OUT OF SCOPE** |

---

## Out of scope

Per your instructions, the following are deliberately **not** planned here:

- **Custom TTS voice training / voice cloning** (V2's fine-tuned "Amy" Piper voice). *Note:* training a **wake-word** model for "Richard" is a different thing — it is a tiny keyword classifier, not a voice — and **is** in scope (see #1).
- **The Odradek / Death Stranding build** (V3) — a separate robot.
- **Steam game streaming via Moonlight/Sunshine** (V3) — irrelevant to an RC crawler and would add a non-local dependency.

---

# 5. Improvement designs

Each section: *current state → what the reference does → proposed design → touchpoints → config → effort/risk/deps → test plan.* All grounded in real `file:line` citations.

---

## #1 — Wake word "Richard" ⭐ (flagship)

> **Richard** = **R**obotic **I**nteractive **C**omputing **H**ub **A**utonomous **R**esponse **D**evice. Say "Richard" and the robot wakes, acknowledges, and listens — **replacing, but not removing,** the SSH `ENTER` push-to-talk at [main.py:270](brain/main.py#L270).

**Why it matters here (beyond the cool factor):** it is the single change that makes the robot viable as a **headless systemd service**. `scripts/robot.service` has no TTY/stdin, so today's `input("[ENTER]…")` *physically cannot fire* under the service — the brain only runs hand-launched over SSH. A wake word removes the stdin dependency.

### Adapt the reference's pattern — don't copy it blindly

The reference ([agent.py:42-44,244-260,565-692](ref/be-more-agent-main/agent.py#L565-L692)) loads openWakeWord and runs an always-listening `sounddevice` stream, but it's a desktop Tkinter app. We borrow the **openWakeWord pattern** and adapt to our hard constraints:

- **Hybrid trigger, identical to the reference.** `detect_wake_word_or_ptt()` returns `WAKE` / `PTT` / `CLI`; if the model fails to load it degrades to PTT-only ([agent.py:565-616](ref/be-more-agent-main/agent.py#L565-L616)). We do the same: a new `wait_for_trigger(cfg)` returns `"WAKE"` or `"PTT"`, ENTER/`q`/Ctrl-C keep today's semantics ([main.py:271-274,339](brain/main.py#L271-L274)).
- **Opt-in, default OFF.** New `ROBOT_WAKEWORD_ENABLE` (default `0`). When off, behaviour is byte-for-byte today's ENTER PTT — protects the existing bench workflow and never assumes a mic is streaming over SSH.
- **The wake word only starts capture — it is *not* the autonomy gate.** EXPLORE/FIND are still demoted to DRIVE unless the *raw transcript* matches `is_explore_command(text)` ([main.py:302-307](brain/main.py#L302-L307)). Saying "Richard" gets you a listening robot; you still must say "explore" / "find X" for autonomy. Two orthogonal gates, both preserved.

### The woken turn (only one seam changes)

```
TODAY:    loop → idle → input("[ENTER]") ───────────────→ listen_and_transcribe → think → act → say
WAKEWORD: loop → idle → wait_for_trigger(cfg)
                         │ ENABLE=0 → input("[ENTER]")          (unchanged fallback)
                         │ ENABLE=1 → openWakeWord stream:
                         │     ├ "Richard" score>thr → ack earcon + face=listening → "WAKE"
                         │     ├ stdin line          → "PTT"   (ENTER still works)
                         │     └ q / EOF / Ctrl-C    → quit
                         └───────────────────────────────────→ listen_and_transcribe → think → act → say
```

Steps after the trigger are **unchanged**: the existing `listen_and_transcribe(cfg)` ([main.py:90-103](brain/main.py#L90-L103)) records the 4 s command and runs whisper exactly as today. Wake detection is a *separate, continuous, shorter* capture; the command capture is untouched.

### Audio capture — use a persistent input stream (important correction)

openWakeWord needs a **continuous** 16 kHz / 1280-sample stream. The reference uses a persistent `sounddevice` `InputStream` for exactly this reason ([agent.py:626-691](ref/be-more-agent-main/agent.py#L626-L691)). **Spawning a fresh `arecord` per ~80 ms chunk would drop audio between captures and miss wakes** — so the realistic, reliable approach mirrors the reference: a persistent `sounddevice` stream, with two Pi-specific CPU guards the reference already proved:

1. **Silence skip** — only run `predict()` when chunk peak amplitude clears a floor (reference `current_max > 200`, [agent.py:681](ref/be-more-agent-main/agent.py#L681)). In a quiet room the model barely runs.
2. **Nearest-neighbour downsample by index-slicing, never `scipy.resample`** ([agent.py:667-672](ref/be-more-agent-main/agent.py#L667-L672)) — avoids the ALSA buffer overflow (`!!!!`) that scipy resampling causes on the Pi.

This means we **do** take a `sounddevice` + PortAudio dependency (see deps). `arecord`-looping remains a documented fallback but is flagged as may-miss-wakes.

### Half-duplex & self-trigger (don't wake on our own voice)

The headline risk is the robot hearing "Richard" in its own Piper output. The half-duplex `audio_lock` ([main.py:87](brain/main.py#L87)) is the clean defense: **the wake stream must be paused/stopped while TTS or any earcon holds the lock** (the reference calls `sd.stop()` before recording for the same hardware-contention reason, [agent.py:725,749](ref/be-more-agent-main/agent.py#L749)), and a `ROBOT_WAKEWORD_COOLDOWN_S` refractory window applies *after* playback releases the lock before re-arming. We **do not** attempt open-mic barge-in (interrupting TTS by voice) — that needs full-duplex + echo cancellation, which conflicts with our half-duplex design. *Say so explicitly: the robot finishes talking, then listens.*

### ⚠️ Safety: the wake loop runs alongside the 20 Hz failsafe (corrected claim)

Wake detection never overlaps **Ollama** (the foreground turn is serial — the loop sits at `wait_for_trigger` precisely when the model is idle). **But** the always-listening loop's continuous capture + `predict()` runs concurrently with the driver's **20 Hz `_control_loop`** ([driver.py:303-350](brain/driver.py#L303-L350)) — the safety-critical thread that returns the ESC to neutral — and the TTS daemon. So it is *not* "zero steady-state contention": it is a persistent extra CPU consumer alongside the failsafe. **Mandatory test:** confirm the 20 Hz cadence does not jitter under always-listening load (a starved failsafe could let a FORWARD command overrun its deadline). The two CPU guards above keep the wake cost to a small fraction of one core, which the Active Cooler easily absorbs — but it must be *measured*, not assumed.

### Obtaining a "Richard" model (wake-word training ≠ voice cloning)

This produces a small classifier (`richard.onnx`/`.tflite`, hundreds of KB, like the reference's 205 KB `wakeword.onnx`) answering one binary question: "did someone say 'Richard'?" It has nothing to do with how the robot *sounds* (Piper). Three options, increasing effort:

1. **Stock model first (ship day one).** The reference ships the stock **"Hey Jarvis"** model (`hey_jarvis_v0.1.onnx`, downloaded in [setup.sh](ref/be-more-agent-main/setup.sh)). Wire the whole pipeline with a stock word, then swap in "Richard" — the detector loads a *list* of model paths and iterates `prediction_buffer.keys()`, so adding "Richard" later is a config change, not code. **Recommended: prove the plumbing with a stock word.**
2. **Phonetically-close pretrained** — only if a near-match community model already exists.
3. **Train a custom "Richard" model** via the **openWakeWord / Home Assistant community pipeline** (a Colab notebook). It trains from **synthetic positive clips generated by a *diverse set of stock Piper voices* plus the pipeline's voice-augmentation** (for speaker diversity), mixed with negative/background audio → outputs a `.tflite`/`.onnx`. **Do not train from a single voice / our `en_US-lessac-medium` voice** — that yields a classifier that only wakes on a robotic-sounding "Richard." Effort: ~an afternoon of generation + training, then threshold/clip-diversity tuning. "Richard" is short and common-ish, so expect to tune against false-accepts.

**Built-in fallback:** missing/unloadable model → log a warning and degrade to ENTER PTT (mirrors the reference's `oww_model = None → PTT`, and our own `camera.py`/`driver.py` `available=False`/fake degradation).

> **Implementation note:** openWakeWord's `Model()` constructor signature changed across versions — the reference try/excepts `Model(wakeword_model_paths=[…])` then `Model(wakeword_models=[…])` ([agent.py:249-254](ref/be-more-agent-main/agent.py#L249-L254)). Confirm the current API at implementation time.

### Code touchpoints

- **New `brain/wakeword.py`** — mirror `camera.py`/`driver.py` lifecycle: lazy-import `openwakeword` inside a live branch; `class WakeWord(cfg)` that selects a **`_FakeWakeWord`** when `dry_run_enabled()` ([driver.py:52-53](brain/driver.py#L52-L53)) or the model is absent; expose `available`, a `wait()`/`poll()`, and `close()`. Real detector = persistent `sounddevice` stream + silence-skip + index-slice downsample + `predict()` on `prediction_buffer["richard"]`, firing when score > threshold for `trigger_level` consecutive hits. `_FakeWakeWord` "fires" on an injected trigger (`ROBOT_FAKE_WAKE` / stdin sentinel) so the whole woken turn is bench-testable with **no mic** under `ROBOT_DRY_RUN=1`. Log on a `robot.wake` logger like `robot.hw`.
- **`brain/main.py`** — new `wait_for_trigger(cfg, wake, face)` sibling to `listen_and_transcribe`; when `ENABLE=0` it is literally today's `input(...)` block. Replace the bare `input(...)` at [main.py:269-270](brain/main.py#L269-L270). On `"WAKE"`, set `listening` + play the **wake-ack earcon** before `listen_and_transcribe`. Construct `wake = WakeWord(cfg)` next to `fc = Face()` (~[main.py:206](brain/main.py#L206)); `wake.close()` in teardown. Add the `ROBOT_WAKEWORD_*` keys to the config loader (~[main.py:46-54](brain/main.py#L46-L54)).
- **Face** — reuse `fc.set_expression(...)` (no new API). Optionally add one `"waiting"` mood to `_MOODS` ([face.py:29-38](brain/face.py#L29-L38)) for the pre-wake state.
- **Sound** — the wake-ack reuses the earcon player from **#3** (`play_oneshot("ack")`).

### New config knobs (`config/robot.env`)

| Key | Default | Meaning |
|---|---|---|
| `ROBOT_WAKEWORD_ENABLE` | `0` | Master toggle. `0` = today's ENTER PTT (no new deps exercised). |
| `ROBOT_WAKEWORD_MODEL` | `~/openwakeword/richard.onnx` | Path to the model (promote the reference's hard-coded constant to config). Missing → degrade to PTT. |
| `ROBOT_WAKEWORD_THRESHOLD` | `0.5` | Min activation probability (reference default). |
| `ROBOT_WAKEWORD_TRIGGER_LEVEL` | `1` (raise to `2` if false-firing) | Consecutive activations before firing — primary false-accept damper. |
| `ROBOT_WAKEWORD_COOLDOWN_S` | `2.0` | Refractory window after a wake / after TTS releases the lock. |
| (existing) `ROBOT_MIC` | `plughw:1,0` | Reuses the existing USB mic ([config/robot.env:16](config/robot.env#L16)). **No new audio device.** |
| `ROBOT_FAKE_WAKE` | `0` | Dry-run trigger (parallels `ROBOT_FAKE_VISION`). |

### Effort / risk / deps / test

- **Effort: M.** New module (mirrors an existing pattern), one new function + a few edited lines in `main.py`, ~6 config keys, an installer model-download step, docs. The long-tail effort is *obtaining/tuning the "Richard" model* — decoupled, can start with a stock word.
- **Deps (net-new):** `openwakeword`, `onnxruntime` (and/or `tflite-runtime`) → `brain/requirements.txt`; `sounddevice` + apt `portaudio19-dev`, `libasound2-dev` (the reference installs these and force-reinstalls `sounddevice` against PortAudio). The `richard.onnx` binary → an idempotent download step in `install_stack.sh` (mirror the whisper/Piper `curl` pattern). **No VAD needed** — openWakeWord's silence-skip is sufficient.
- **Risks:** false-accepts on a short name (→ raise threshold/trigger-level; a spurious wake only costs a 4 s `arecord`+whisper that `is_noise()` discards — it *cannot* move the robot); self-trigger on TTS (→ `audio_lock` + cooldown); ALSA overflow (→ index-slice downsample + silence-skip); always-on over SSH surprise (→ default OFF, ENTER kept); **failsafe jitter (→ measure 20 Hz cadence)**.
- **Dry-run test (no mic):** `ROBOT_DRY_RUN=1 ROBOT_WAKEWORD_ENABLE=1` → `_FakeWakeWord` selected; inject a fake wake → assert face `listening`, ack earcon attempted, then the existing fake-vision downstream path runs to a parsed intent. `ENABLE=0` → byte-identical to today (regression guard). Bad model path → WARNING + degrade to PTT.
- **On-device test (Pi 5, wheels-off / ESC unpowered):** stock word → wake → ack → 4 s record → whisper → intent (watch openWakeWord score prints to set threshold); swap `richard.onnx`, tune; **self-trigger test** (make Piper say a sentence containing "Richard", confirm no re-wake); **headless test** under `robot.service` (no stdin) — wakes by voice with no ENTER; **failsafe-cadence test** — log `_control_loop` tick interval under always-listening load and confirm ~20 Hz, plus `vcgencmd measure_temp`.
- **Docs:** `docs/assembly.md` Phase 1 (audio bring-up + "wake replaces ENTER, ENTER stays"), the `robot.service` stdin note, `requirements.txt`, `install_stack.sh`, and the README "Is not → wake word" line (this feature removes that limitation).

---

## #2 — Model orchestration: warmup + `keep_alive` + `model_lock`

> Cheap, no hardware, fixes a **real latent bug**, and is the prerequisite for #5 and #7. Recommend doing this **first**.

### Three problems, all fixable in code

1. **Cold-load on first command.** Nothing warms the model at boot; the first `query_ollama` pays the full load — the repo warns about it at [config/robot.env:11](config/robot.env#L11), and on timeout the loop fail-safes to STOP ([main.py:292-295](brain/main.py#L292-L295)).
2. **No arbitration on the shared model.** Text-intent and vision both hit the same Ollama/`gemma3:4b` with **no lock**. Today's serialisation is an accident of the single-threaded loop — add any second client (a debug `curl`, a future async wake listener, a second consumer) during a tens-of-seconds vision call and the text request queues and blows its 120 s timeout. **This is our software analogue of the creator's `/dev/hailo0` device-exclusivity crash:** one resource, no lock, long exclusive holds.
3. **Eviction between commands.** No `keep_alive` set → Ollama's default ~5-min idle TTL unloads the model → next command cold-loads again.

The reference avoids all three: separate lightweight VLM ([agent.py:94-95](ref/be-more-agent-main/agent.py#L94-L95)), `keep_alive=-1` + a **WARMUP** boot prime ([agent.py:556-563](ref/be-more-agent-main/agent.py#L556-L563)), and one serialised worker thread.

### Proposed (three recommendations)

- **Rec 1 — Warmup at boot (always).** New `warmup_model(cfg, fc)` after `Face()` (~[main.py:206](brain/main.py#L206)) and before the loop: set a `warmup` face, issue a tiny priming generate (`{"prompt":"ok","options":{"num_predict":1}}`) with a generous timeout, best-effort (a failed warmup must not block the loop). Gate on `ROBOT_WARMUP` (default `1`). To surface the cue, add `"warmup"` to `STATES` + a `_MOODS` row ([face.py:25,29-38](brain/face.py#L25)); else it falls back to `idle`.
  - *Dependency to call out:* the #3 boot **greeting** only becomes a meaningful "stack is up" signal once the model is warmed/pinned here — otherwise the first real turn still cold-loads after the greeting plays.
- **Rec 2 — One `model_lock` + keep-alive (always).** Extract a thin **`brain/inference.py`** with a shared `model_lock` (module-level `threading.Lock`, mirroring `audio_lock`) and a single `ollama_generate(cfg, payload)` that takes the lock, POSTs, unwraps the `{"response": …}` envelope (both callers already do this identically, [main.py:150-153](brain/main.py#L150-L153) / [vision.py:66-73](brain/vision.py#L66-L73)), and injects `keep_alive`. Route both `query_ollama` and `vision._ollama_image` through it. **Honest limit:** an in-*process* lock only protects this process; a second OS process/`curl` is not covered — for that the real protection is `keep_alive` + not spawning extra clients. The lock is near-zero-cost today (loop already serialises) and pure insurance for future concurrency (e.g. the wake listener). **Safety:** holding `model_lock` across a 600 s vision call cannot starve the emergency stop — the failsafe lives in the driver's own thread ([driver.py:303-350](brain/driver.py#L303-L350)) and never calls Ollama.
- **Rec 3 — Optional dedicated VLM** → see **#5** (kept separate because it costs RAM).

### Config / touchpoints / effort

| Concern | Location | Change |
|---|---|---|
| Thin inference layer | new `brain/inference.py` | `ollama_generate` owns `model_lock` + envelope unwrap + `keep_alive` |
| Text uses it | [main.py:134-159](brain/main.py#L134-L159) | route POST through `inference.ollama_generate` |
| Vision uses it | [vision.py:54-73](brain/vision.py#L54-L73) | same; use `cfg["vision_model"] or cfg["model"]` |
| Warmup | ~[main.py:206](brain/main.py#L206) → before [main.py:268](brain/main.py#L268) | `warmup_model(cfg, fc)`, gated on `ROBOT_WARMUP` |
| Warmup face | [face.py:25,29-38](brain/face.py#L25) | add `"warmup"` state + mood row |

New knobs: `ROBOT_WARMUP` (1), `ROBOT_OLLAMA_KEEPALIVE` (e.g. `30m`, or `-1` to pin), `ROBOT_VISION_MODEL` (empty → unchanged), optional `ROBOT_VISION_URL`. **Effort: S–M.** **Deps: none** (pure `urllib`, already used). **Test:** dry-run already fakes vision — assert the priming POST fires once before the first prompt; unit-test `ollama_generate` serialisation (two threads, non-overlap) and that `keep_alive` is in the payload.

---

## #3 — Audio cues (ack / greeting / thinking earcons)

> The highest UX-per-effort item on a *slow* robot. Adapts the reference's `sounds/{greeting,ack,thinking,error}` system to our `aplay`/`audio_lock` discipline.

### Why it matters here

The turn blocks for a long time (text up to 120 s, **vision up to 600 s**) and today the operator gets **no feedback** that the robot heard them and is working — the only audio out is Piper TTS ([main.py:162-184](brain/main.py#L162-L184)), and the face is a single static frame. Non-verbal cues fix exactly this:

- **Instant ack** the moment a command (or wake word) is accepted — "heard you" *before* the multi-second model call.
- **Thinking loop** *during* the blocking call — continuous proof-of-life across a 10-minute vision stall.
- **Greeting** on boot/arm — confirms the stack came up (vital when **headless under `robot.service`**, no console). *(Only meaningful once #2 pins the model — see cross-link.)*

Reference mechanics to port: `get_random_sound` ([agent.py:1031-1035](ref/be-more-agent-main/agent.py#L1031-L1035)), the `_run_thinking_sound_loop` daemon with a 0.5 s lead-in ([agent.py:1022-1029](ref/be-more-agent-main/agent.py#L1022-L1029)), and the **clear-flag-before-speak** handoff ([agent.py:849-854](ref/be-more-agent-main/agent.py#L849-L854)).

### Design

- **New `brain/sounds.py`** parallel to `speak()`: `play_oneshot(category)` picks a random `.wav` from `<sounds_dir>/<category>/` and spawns a **daemon thread** that grabs `audio_lock` and `aplay`s it (same invocation as `speak()` at [main.py:176-177](brain/main.py#L176-L177)); returns immediately. `start_thinking()`/`stop_thinking()` drive a single daemon loop gated by a `threading.Event`. Empty/missing dir → silent no-op (never raises, matching `say`/`set_expression`). Dry-run → log `SOUND -> ack (got_it.wav)` on a `robot.sound` logger, no `aplay`.
- **Asset layout** (committed WAVs, *not* Piper-generated, *not* the reference's `sounddevice`/`scipy` path):
  ```
  sounds/{greeting_sounds, ack_sounds, thinking_sounds, error_sounds}/*.wav   # 16-bit PCM, USB-card-native rate
  ```
- **TTS collision avoidance (the crux):** reuse the **single `audio_lock`** so cues, TTS, and recording stay globally serialised; call `stop_thinking()` *before* the first `say()` of the turn. **Lock-ordering rule:** the thinking loop must **acquire `audio_lock` per clip, not hold it across the whole LLM call** (holding it would deadlock TTS); it checks its stop flag in ~100 ms steps so it tears down within one clip boundary.

### When each fires (corrected for wake word + safety)

| Cue | Fires | Site |
|---|---|---|
| **greeting** | once, after driver-arm + stack init, before the loop | after the banner at [main.py:262](brain/main.py#L262) |
| **wake-ack** | instant, on "Richard" detection (if #1 enabled) | in `wait_for_trigger` (#1) |
| **command-ack** | after a transcript passes the noise filter, before the LLM | between [main.py:285](brain/main.py#L285) and [main.py:289](brain/main.py#L289) |
| **thinking** | loop start before the blocking model call, stop when reply ready | start at [main.py:289](brain/main.py#L289); stop right after `query_ollama` returns ([main.py:291](brain/main.py#L291)); also bracket `vision.look()`/`describe()` in [behavior.py](brain/behavior.py) |
| **error** (optional) | obstacle / LLM exception | `on_obstacle` ([main.py:218](brain/main.py#L218)), `llm:` except ([main.py:294](brain/main.py#L294)) |

> **Two acks, one player (reconciliation).** With #1, there's a **wake-ack** ("heard my name", instant, before recording) *and* a **command-ack** ("accepted a command", after the noise filter). They're the same `play_oneshot("ack")` player at two sites — placing the command-ack *after* `is_noise()`/`len<2` ([main.py:284-285](brain/main.py#L284-L285)) means whisper hallucinations ("you", "thank you") never blip. *(The reference plays ack right after recording, [agent.py:771](ref/be-more-agent-main/agent.py#L771); we prefer after-accepted.)*

> **⚠️ Safety ordering for the error cue:** the error earcon must be **fire-and-forget and must never sit on the synchronous path to `driver.stop()`/`_neutral()`**. `on_obstacle` already backgrounds its `speak` ([main.py:224-226](brain/main.py#L224-L226)); `play_oneshot` is likewise daemon-spawned and returns immediately — keep it that way so an obstacle stop is never delayed by a sound.

### Config / effort

Knobs: `ROBOT_SOUNDS_ENABLE` (1; 0 → log-only even live), `ROBOT_SOUNDS_DIR`, **`ROBOT_SOUNDS_DEVICE`** (the USB *speaker* card via `aplay -D …` — important because the mic is `plughw:1,0` and the speaker is a *separate* card), per-category `ROBOT_SOUNDS_{GREETING,ACK,THINKING,ERROR}`, `ROBOT_THINKING_LEAD_MS` (500, so fast turns stay silent), `ROBOT_THINKING_GAP_MS`. **Effort: S.** **Deps: none** (`aplay` already installed/used). Add the `sounds/` dir creation to `install_stack.sh` (mirror the reference's `setup.sh` dir-creation). **Test:** dry-run asserts `robot.sound` logs at each site and *no* ack on noise transcripts; live, confirm ack within ~100 ms of `Heard:`, thinking loops audibly during a real vision look and stops cleanly before the spoken reply.

---

## #4 — Animated faces (PNG sequences)

> Most visually impactful, lowest-risk feature. **Stays entirely inside [brain/face.py](brain/face.py)** — the `set_expression(state)` contract (all that `main.py`/`behavior.py` call) is preserved exactly.

### Current vs reference

- **Today:** 100% procedural `_render` per state ([face.py:41-97](brain/face.py#L41-L97)); no assets ([face.py:5](brain/face.py#L5)); **no animation** — `set_expression` early-outs on same-state ([face.py:233-234](brain/face.py#L233-L234)). 8 states, 3 backends (`turing → framebuffer → simulated`), never-raise.
- **Reference:** preloads `faces/<state>/NN.png` ([agent.py:364-381](ref/be-more-agent-main/agent.py#L364-L381)) — frame counts idle=1, listening=2, thinking=4, speaking=3, plus capturing/warmup/error=1. A single self-rescheduling loop advances a frame index keyed on the shared state ([agent.py:383-400](ref/be-more-agent-main/agent.py#L383-L400)); a state change just resets the index. Two-tier fps: **50 ms for SPEAKING** (random frame 1..n to fake a moving mouth, [agent.py:389-393](ref/be-more-agent-main/agent.py#L389-L393)), 500 ms otherwise. Missing folder → idle → blue blank, never crashes.

**Structural difference that matters:** the reference animates on the Tk main-thread `after()` loop because it's a GUI app. **We have no GUI loop** — `main.py` is a blocking REPL writing to serial/framebuffer/log. So we need a **dedicated background daemon thread** (the only new moving part).

### Design (backward-compatible, incremental)

1. **Asset layout:** `brain/faces/<state>/NN.png`, root configurable via `ROBOT_FACE_FRAMES_DIR` (default `brain/faces`, module-relative so it resolves under the service `WorkingDirectory`). Glob `*.png`, sort numerically (tolerate `thinking 04.png` spaces *and* `04.png`), open via Pillow, **resize once at load time to the backend's *actual* geometry** — note our panel default is **320×480 portrait** ([face.py:196-197](brain/face.py#L196-L197)), *not* the reference's 800×480 landscape; reused reference art is re-letterboxed. Empty/missing dir → that state transparently falls back to today's procedural `_render`. **With no `faces/` dir shipped, behaviour is identical to today.**
2. **Frame model:** per active state cache `frames`, `frame_index`, `(fps, loop)`. 1-frame state = static.
3. **fps/loop table:** `talking` → fast (~15–20 fps) looping (+ optional random-frame mouth trick); `thinking`/`listening`/`exploring` → slow loop (~2–4 fps); `idle`/`happy`/`confused`/`alarmed` → static or short loop. Global `ROBOT_FACE_FPS` default, per-state overrides.
4. **Animator thread:** one daemon (lazy-started on first multi-frame state, torn down in `close()`), one lock guarding `_state`/`frames`/`frame_index`, one Event so a state change interrupts the frame-wait within ms. **Rework the dedupe early-out** at [face.py:233-234](brain/face.py#L233-L234): on an *actual* change, take the lock, set state, reset index, signal the Event (same state still no-ops → no flicker). 1-frame/procedural state → show once, then block on the Event (no busy spin).
5. **Per-backend throughput:** *framebuffer* — resize/convert once at load + **reuse a single persistent `np.memmap`** (today it re-allocs every `show`, [face.py:170-178](brain/face.py#L170-L178)); *turing USB-C* — serial bandwidth caps fps, so **clamp the turing backend to a serial-safe fps** (e.g. ~5–8 for `talking`); *simulated* — log **on state change only**, not per frame (keep `ROBOT_FACE_DUMP` as the dry-run inspection path).
6. **State mapping** (keep our 8-state vocab as the public contract): reference `speaking→talking`, `error→alarmed`; reference `idle/listening/thinking` map directly. Our `exploring/happy/confused` have **no reference art** → stay procedural. Worth **adding two new states** (table-only in `face.py`, emission wiring is a later one-liner): **`warmup`** (covers the #2 boot model-load) and **`capturing`** (covers the camera grab at [behavior.py:63](brain/behavior.py#L63), currently shown as `exploring`).

### Asset sourcing / effort

The repo ships **no** drawn faces today, so this has a genuine **art dependency independent of the code**. Land the engine with **zero assets** (identical to today), then add **one frame per state** (instant upgrade to drawn stills — even auto-exporting the procedural `_render` output as `01.png`), then add motion where it matters (`talking`, `thinking`). Reference art is a placeholder subject to its license + our portrait geometry. **Effort: S–M code (~½ day), all in one file; art is separate/ongoing.** **Deps: none** (`Pillow`/`numpy` already present). Touches `face.py` only — no `main.py`/`behavior.py`/`driver.py` change. **Test:** simulated + `ROBOT_FACE_DUMP` dry-run (per-frame dump, log-on-change), rapid state-change interruption, fps/loop verification, framebuffer memmap reuse, turing fps clamp, and a **regression test** that with no `faces/` dir output is byte-for-byte today's procedural behaviour.

---

## #5 — Optional dedicated VLM (Moondream2)

> Present as an **option, not a default**, because it costs RAM. It directly resolves the shared-model contention and the slow-vision stall that #3 leans on.

**What:** give vision its own model (and optionally endpoint) so `look()`/`describe()` stop sharing `gemma3:4b`. The integration boundary already exists — the structured-output contract (`LOOK_SCHEMA`/`DESCRIBE_SCHEMA`, [vision.py:24-44](brain/vision.py#L24-L44)); anything returning that JSON works. Two variants:
- **(a) Different model, same server:** `ROBOT_VISION_MODEL` (default empty → falls back to `cfg["model"]`, unchanged unless set). `ollama pull moondream`, set `ROBOT_VISION_MODEL=moondream`.
- **(b) Different model *and* endpoint:** also `ROBOT_VISION_URL`.

This mirrors the reference's two-model split (`gemma3:1b` + `moondream`, [agent.py:94-95](ref/be-more-agent-main/agent.py#L94-L95)). With two distinct models, a vision call and a text call no longer fight over one resident model — the #2 `model_lock` becomes belt-and-suspenders.

**Latency win:** Moondream2 is far smaller than multimodal `gemma3:4b`, so a "look" drops from tens-of-seconds substantially, letting explore/find tolerate more iterations.

**RAM math (state explicitly):** two models resident on the 16 GB Pi — `gemma3:4b` (~3–4 GB Q4) + `moondream` (~1.6–2 GB) ≈ 5–6 GB of weights, on top of OS + picamera2/libcamera + whisper + Piper. **16 GB accommodates this; it tightens headroom.** Posture: pin the **text** model (`keep_alive=-1`, hit every turn) and keep the **VLM** keep-alive **shorter/lazy** (the reference loads the VLM lazily and only pins text). The camera path is unchanged — same JPEG bytes ([vision.py:54-62](brain/vision.py#L54-L62)) routed to a different model; **no new device contention**. **Effort: S code / M ops (extra `ollama pull`, RAM validation on hardware).** **Test:** assert vision POST carries `"model":"moondream"` while text still carries `gemma3:4b`; default-empty → both carry `gemma3:4b`; on hardware `ollama ps` + watch free RAM under an explore loop.

---

## #6 — Optional web search (DuckDuckGo) — low priority, conflicts with offline

> **Read this first:** this is the **lowest-priority item** and the *only* feature that requires the radio to be **on**. If priorities are limited, **this is the first thing to cut.**

### The tension (be honest)

LocalAiRobot's defining promise is "pull the network and it still works" — the installer header says so ([install_stack.sh L1-4](scripts/install_stack.sh)), the operating instruction is literally "after the one-time downloads, disable Wi-Fi", and **every** subsystem (whisper, Ollama, Piper, SQLite) runs with zero outbound network. DuckDuckGo search breaks that premise: it (a) dilutes the offline guarantee, (b) serves *none* of the robot's actual job (driving + local vision) — only companion chatter, the least load-bearing capability, and (c) adds the project's only network/latency/privacy surface. The reference ships it ([agent.py:47,471-506](ref/be-more-agent-main/agent.py#L471-L506)) because it's a stationary desktop companion with no offline mandate. **We should adapt the *pattern*, but be honest the *feature* is a poor fit.**

### If built: hard rules (non-negotiable)

1. **Opt-in, default OFF** — `ROBOT_WEBSEARCH_ENABLE=0`. No code path reaches the network unless deliberately enabled.
2. **Companion Q&A only — never in any motion/safety path.** Structurally forbidden from DRIVE/STOP/FIND/EXPLORE, the 20 Hz control thread, the obstacle failsafe, and `behavior.py`.
3. **Never blocks teleop/safety** — runs only at neutral, in the conversational branch, with a **tight timeout** (`ROBOT_WEBSEARCH_TIMEOUT_S` ≈ 5 s, far below the 120 s LLM timeout). Any failure → instant spoken "I'm offline", ESC already neutral.
4. **Degrade gracefully** — offline is the *expected* state, not an error (mirrors how `vision.py` downgrades to "not visible", [vision.py:114-115](brain/vision.py#L114-L115)).

### Design (adapt the reference's two-pass tool protocol)

- **New `brain/websearch.py`** — lazy-import DDG inside a live branch (so it imports fine offline), single `search(cfg, query) -> str | None` that **never raises** and returns `None` if disabled/offline/empty/timeout.
- **Trigger = explicit tool-action (preferred, Option A):** extend `SYSTEM_PROMPT` ([main.py:106-124](brain/main.py#L106-L124)) so `gemma3:4b` can emit `{"action":"ANSWER","query":"…","reply":"Let me check…"}`; add an `ANSWER` branch in the dispatcher ([main.py:309+](brain/main.py#L309)). This is the analogue of the reference's `{"action":"search_web",…}` ([agent.py:445-511](ref/be-more-agent-main/agent.py#L445-L511)). **Crucially, extend the gate at [main.py:302-307](brain/main.py#L302-L307) to demote `ANSWER` unless it's unambiguously non-motion (no drive/steer fields)** — a drive command must *never* be routed to search.
- **Two-pass summarise (copy the reference):** don't speak raw snippets — run the search, then a *second local* `gemma3:4b` call to summarise into one short sentence ([agent.py:915-933](ref/be-more-agent-main/agent.py#L915-L933)). Only the search HTTP leaves the device; synthesis stays local.
- **Shared-model contention:** the summarise call hits the same `gemma3:4b` — so the search+summarise must run **synchronously on the main loop thread, never backgrounded** (backgrounding would reintroduce contention; #2's `model_lock` is the backstop).

**Library:** prefer **`ddgs`** (the renamed `duckduckgo_search`) and pin a version — it's a **net-new dependency** and the *only* outbound-network dep; comment it as "breaks fully-offline" next to the requirement. **Knobs:** `ROBOT_WEBSEARCH_ENABLE` (0), `_TIMEOUT_S` (5), `_MAX_RESULTS` (1), `_REGION` (`us-en`), optional `_FALLBACK` line; `ROBOT_DRY_RUN` returns a canned fake. **Effort: S.** **Risk:** mostly *conceptual* (offline identity, privacy) not technical. **Test (most important first):** default-off proof (no import, no socket, Wi-Fi off); offline-degrade within the timeout; **safety-isolation** ("go forward" never routes to ANSWER; STOP/obstacle unaffected mid-search; no search reachable from `behavior.py`/control loop); no-contention (runs on the loop thread only).

---

## #7 — AI accelerator / NPU (Hailo AI HAT+) — hardware + async inference

> **Heaviest, most-speculative item. Effort L. A Phase-N+ exploration, not a committed build.** Read the caveats before the opportunity.

### Opportunity

Today the brain and eyes are the *same* CPU-bound model; vision is "tens of seconds to minutes" ([vision.py:5-7](brain/vision.py#L5-L7)), which forces vision to be a park-and-look and caps autonomy at 6 iterations. An NPU offloading the VLM could bring a "look" toward ~1–2 s → vision usable near-real-time, tighter explore loops, CPU headroom returned to STT/TTS/control. This is the local realisation of V2's AI-HAT+2 (Hailo-10H) vs M5Stack LLM-8850 comparison.

### Honest caveats (read first)

- **GenAI on Hailo is new and model-gated.** HailoRT exposes an LLM/VLM GenAI API, but models must be **pre-compiled `.hef` files** for the specific target — you **cannot** point it at our Ollama `gemma3:4b`. The realistic near-term VLM is a *small* one (Moondream2-class — the same model #5 recommends). Plan around **small VLM on the NPU, LLM possibly still on CPU**, not "move everything."
- **Loss of schema-constrained output.** Our vision safety vocabulary depends on Ollama's `format: <JSON-schema>` ([vision.py:24-44](brain/vision.py#L24-L44)); HailoRT's generator streams free tokens, so an NPU backend needs its own JSON coercion/repair layer to keep `_normalize_look` ([vision.py:95-110](brain/vision.py#L95-L110)) fed. Real integration work, not a config swap.
- **Physical/power fit on a packed crawler.** I2C (GPIO2/3), six sensor GPIOs ([driver.py:125-130](brain/driver.py#L125-L130)), CSI camera, the USB-C panel, and a planned UPS HAT already contend for space/header. Adding a PCIe/M.2 Hailo HAT means **HAT-stacking conflicts, a tall stack on a 1/10 chassis, and extra current draw** competing with the active cooler + brain domain. The NPU does **not** fight the I2C bus / sensor GPIOs / CSI lane (it's on its own PCIe edge) — the real contention is **physical/power**.

### The device-exclusivity lesson, in our code

The creator's warning ("warming the VLM grabs `/dev/hailo0`, crashes the LLM if not async") **is the same hazard we already have in software** (problem #2.2). HailoRT centres on a single shared `VDevice`; running an LLM + VLM on one Hailo-10H means both share it (`group_id="SHARED"`), and warming/loading the VLM mid-LLM-stream tears down the in-flight LLM — exactly the crash. **Translation:** the fix is the **`model_lock`** from #2, wrapping both inference paths, plus an explicit **warmup** with one model pinned and (un)loading the other as a *locked, serialised, never-concurrent* operation. **Building `model_lock` now (against today's shared Ollama) is the cheapest insurance and a prerequisite for any NPU work.**

### Hardware diagram impact + sequencing

Adding an NPU HAT touches **`wiring.puml` + `power-domains.puml` + `assembly.puml`** (new brain-domain device on PCIe, extra current budget, physical stack step) — see [§6 Diagram impact](#6-diagram-impact). **Touchpoint:** a `ROBOT_VISION_BACKEND ∈ {ollama, hailo}` seam behind `look()`/`describe()`; the `hailo` path is a new lazy-import module (like `camera.py`) that falls back to CPU-Ollama when absent. **Install:** HailoRT + PCIe driver is an **apt** step + a **reboot** (dovetails with the existing reboot note), `.hef` download mirrors the whisper/Piper pattern. **Sequencing — do in order, each stands alone:** (1) ship `model_lock` + warmup (#2) → (2) add the `vision.py` backend abstraction + dedicated Moondream2 (#5, all-CPU, recovers much of the UX win with **no new hardware**) → (3) *only then* evaluate the Hailo HAT, gated on a confirmed usable `.hef` and a closed power/physical budget. **If those constraints don't close, stop at step 2.**

---

# 6. Diagram impact (which `.puml` to update & re-render)

> **In this planning phase the `.puml` files were NOT edited.** Below is exactly what to change and which PNGs to re-render, ready to apply when you greenlight implementation. All new nodes use the **existing stereotype palette** already defined in each file's `skinparam rectangle` block (`<<io>>`/`<<model>>`/`<<data>>`/`<<motor>>`/`<<proc>>`/`<<store>>`, plus `<<braindomain>>`/`<<motordomain>>` containers), so they inherit colours with no palette edits.

| File | Update? | Trigger | What changes |
|---|---|---|---|
| [basic_control_flow.puml](docs/images/basic_control_flow.puml) | **YES — software phase** | #1, #3, #2, #5, #6 | wake-word `<<proc>>` stage in front of whisper; ack/thinking earcon `<<io>>` node off the reply path; brain note for warmup/async; optional Moondream2 `<<model>>` node; optional DuckDuckGo `<<proc>>` branch. Animated faces = label note only. |
| [wiring.puml](docs/images/wiring.puml) | **OPTIONAL — only if NPU (#7)** (+ cosmetic mic) | #7; #1 mic | Hailo HAT `<<io>>` on PCIe **only if bought**. Wake word reuses the existing USB mic → at most a cosmetic `USB mic` node. |
| [power-domains.puml](docs/images/power-domains.puml) | **OPTIONAL — only if NPU (#7)** | #7 | Hailo HAT `<<io>>` load in the brain domain + current-budget note. No software feature touches this. |
| [assembly.puml](docs/images/assembly.puml) | **OPTIONAL — only if NPU (#7)** | #7 | Hailo HAT `<<io>>` inside `brainbox`. No software feature touches this. |

**Phase split at a glance:** the entire **software phase (#1–#6) re-renders only `basic_control_flow.png`.** The three hardware diagrams change **only if the Hailo HAT (#7) is actually purchased**. The wake word adds **no** hardware node (reuses `ROBOT_MIC`).

### `basic_control_flow.puml` — the planned edits

Existing IDs: `voice, mic, whisper, gate, llm, intent, mem, cam, vision, behav, ctrl, sonar, pca, tts, face`.

- **#1 Wake word (required):** new node + rewire the `mic --> whisper` edge (line 68):
  ```plantuml
  rectangle "wake word (openWakeWord)\nalways-listening: 'Richard'\nthreshold / trigger-level / cooldown" as wake <<proc>>
  rectangle "ENTER push-to-talk\n(SSH fallback, default)" as ptt <<io>>
  mic  --> wake    : 16 kHz stream
  wake --> whisper : on "Richard" -> record 4 s
  ptt  --> whisper : manual trigger (fallback)
  ```
- **#3 Audio cues (required):** earcon node parallel to `tts`, fed by the wake + think + reply paths (near the feedback block, lines 94-98):
  ```plantuml
  rectangle "earcons (aplay WAV)\nack / greeting / thinking\n(half-duplex under audio_lock)" as sfx <<io>>
  wake   --> sfx : ack on wake
  llm    --> sfx : thinking cue
  intent --> sfx : greeting / ack
  ```
- **#2 Warmup/async (note on `llm`):**
  ```plantuml
  note bottom of llm
    warmup on boot (preload weights);
    one model_lock serialises text + vision (async-safe)
  end note
  ```
- **#5 Optional Moondream2 VLM** (note "optional — replaces gemma vision when enabled"):
  ```plantuml
  rectangle "Moondream2 VLM\n(optional dedicated vision model)" as vlm <<model>>
  cam   --> vlm : JPEG frame
  vlm   --> mem : record observation
  behav --> vlm : look? (alt to gemma vision)
  ```
- **#6 Optional web search** (note "optional"):
  ```plantuml
  rectangle "web search (DuckDuckGo)\noptional tool branch" as websearch <<proc>>
  llm       --> websearch : query (when tool-routed)
  websearch --> llm       : results -> reply
  ```
- **#4 Animated faces:** label tweak only on the `face` node (line 64), e.g. add `waiting` to the listed states. **#7 NPU:** not a flow node — at most a one-line note on `llm` ("optionally offloaded to Hailo NPU").

### The three hardware diagrams (only if #7 is bought)

- **wiring.puml** — `rectangle "Hailo AI HAT+\n(NPU, M.2/PCIe via Pi 5)" as npu <<io>>` ; `npu --> pi : PCIe (FFC) + 5V`; note it does **not** join I2C / sensor GPIOs / CSI.
- **power-domains.puml** — add `npu <<io>>` inside the `brain` domain box; `pi --> npu : 5V + PCIe`; `npu ..> gnd : GND`; current-budget note (~+5–8 W under inference; verify UPS/27 W PSU headroom).
- **assembly.puml** — add `npu <<io>>` inside `brainbox`; `pi --> npu : PCIe + 5V`; extend the bottom note (NPU is Pi-side / brain-domain like camera/face/sensors).

### Re-render commands (only the files you actually change)

```bash
plantuml -tpng docs/images/basic_control_flow.puml      # software phase: this one only
plantuml -tpng docs/images/wiring.puml                  # only if NPU/mic added
plantuml -tpng docs/images/power-domains.puml           # only if NPU added
plantuml -tpng docs/images/assembly.puml                # only if NPU added
```

Each writes its sibling `.png` in place; then replace the PNGs embedded in `README.md` / `docs/assembly.md`. **For the software-first phase, you re-render only `basic_control_flow.png`.**

---

# 7. New dependencies (summary)

| Feature | pip (`brain/requirements.txt`) | apt (`install_stack.sh` step 1) | Other |
|---|---|---|---|
| #1 Wake word | `openwakeword`, `onnxruntime` (and/or `tflite-runtime`), `sounddevice` | `portaudio19-dev`, `libasound2-dev` | download `richard.onnx` (or stock model) |
| #2 Orchestration | — | — | — |
| #3 Audio cues | — | — (`aplay` already present) | commit WAV assets |
| #4 Animated faces | — (`Pillow`/`numpy` present) | — | art assets (separate) |
| #5 Moondream2 | — | — | `ollama pull moondream` |
| #6 Web search | `ddgs` (**only outbound-network dep**) | — | requires Wi-Fi when enabled |
| #7 NPU/Hailo | `hailo-platform` (via system-site-packages) | `hailo-all` (HailoRT + PCIe driver) + **reboot** | Hailo AI HAT+ hardware, `.hef` model |

# 8. New config knobs (summary, all `ROBOT_*` in `config/robot.env`)

- **#1:** `ROBOT_WAKEWORD_ENABLE=0`, `_MODEL`, `_THRESHOLD=0.5`, `_TRIGGER_LEVEL=1`, `_COOLDOWN_S=2.0`, `ROBOT_FAKE_WAKE=0` (reuses `ROBOT_MIC`).
- **#2:** `ROBOT_WARMUP=1`, `ROBOT_OLLAMA_KEEPALIVE=30m`.
- **#3:** `ROBOT_SOUNDS_ENABLE=1`, `_DIR`, `_DEVICE`, `_{GREETING,ACK,THINKING,ERROR}`, `ROBOT_THINKING_LEAD_MS=500`, `_GAP_MS`.
- **#4:** `ROBOT_FACE_FRAMES_DIR`, `ROBOT_FACE_FPS` (reuses existing `ROBOT_FACE_*`).
- **#5:** `ROBOT_VISION_MODEL` (empty → unchanged), optional `ROBOT_VISION_URL`.
- **#6:** `ROBOT_WEBSEARCH_ENABLE=0`, `_TIMEOUT_S=5`, `_MAX_RESULTS=1`, `_REGION=us-en`, `_FALLBACK`.
- **#7:** `ROBOT_VISION_BACKEND=ollama`, `.hef` model path.

All defaults preserve today's behaviour (every feature off/unchanged unless opted in).

---

# 9. Hardware improvements (summary)

Most gaps are **software** — the only hardware item is the **NPU (#7)**, which is speculative (see its caveats). Notably:

- **Wake word needs no new hardware** — it reuses the existing USB mic (`ROBOT_MIC=plughw:1,0`).
- A small clarification worth making regardless: the **USB mic + USB speaker are separate cards** and currently aren't drawn in `wiring.puml`/`power-domains.puml` (only in the README parts list). When touching those diagrams you may add a cosmetic `USB mic + speaker` node for completeness, and `ROBOT_SOUNDS_DEVICE` (#3) makes the speaker card explicit in config.
- An **IR illuminator** (already noted in the README parts list as optional for the NoIR camera in the dark) remains the obvious *non-NPU* hardware add if low-light vision matters — independent of these video-derived features.

---

# 10. Phased roadmap

- **Phase A — Foundations (software, no hardware, high value):** **#2** (warmup + `keep_alive` + `model_lock`/`inference.py`) → **#3** (audio cues). Cheap, fixes the cold-load-to-STOP and latent contention bugs, and gives the operator feedback. *Re-render: none.*
- **Phase B — Flagship + polish:** **#1** (Richard wake word; start with a stock word, then train "Richard") → **#4** (animated faces engine, art incremental). *Re-render: `basic_control_flow.png`.*
- **Phase C — Optional, opt-in:** **#5** (Moondream2, if vision latency hurts and RAM allows) → **#6** (web search, only if you want companion Q&A and accept losing pure-offline; default OFF). *Re-render: `basic_control_flow.png` (optional VLM/search nodes).*
- **Phase D — Speculative hardware:** **#7** (Hailo HAT) — only after Phase A's `model_lock`/warmup and Phase C's backend abstraction, and only if a usable `.hef` + power/physical budget close. *Re-render: `wiring/power-domains/assembly.png`.*

---

# 11. Open questions / decisions for you

1. **Wake-word capture stack:** OK to take the `sounddevice` + `portaudio19-dev` dependency (the reference's proven approach for reliable always-listening), rather than the lossy `arecord`-loop fallback?
2. **"Richard" model:** ship a **stock wake word first** and train the custom "Richard" model as a follow-up, or hold #1 until the custom model is trained?
3. **Dedicated VLM (#5):** do you want the vision-latency win enough to hold two models (~5–6 GB) resident on the 16 GB Pi, or stay single-model for simplicity?
4. **Web search (#6):** include it (opt-in, default OFF, with the offline caveat) or **declare it out of scope** for an offline crawler?
5. **NPU (#7):** keep it on the roadmap as a Phase-D exploration, or drop it until the Hailo GenAI/`.hef` ecosystem matures?
6. **Diagrams:** confirm the intent — *plan now, apply the `.puml` edits + re-render during implementation* (this phase made no diagram changes). When implemented, the software phase re-renders **only `basic_control_flow.png`**.

---

## Appendix — reference architecture cheat-sheet

Key `ref/be-more-agent-main/agent.py` anchors used above (BMO is a Tkinter desktop companion; we borrow patterns, not the GUI):

| Pattern | Reference location | Our adaptation |
|---|---|---|
| openWakeWord load (versioned API) | `agent.py:42-44,247-260` | `brain/wakeword.py`, config-driven model path |
| Hybrid WAKE/PTT/CLI trigger | `agent.py:565-616` | `wait_for_trigger()` → `WAKE`/`PTT`, ENTER kept |
| Persistent stream + silence-skip + index downsample | `agent.py:626-691` (`current_max>200`, `:667-672`) | same, paused under `audio_lock` during TTS |
| Sound categories + random pick | `agent.py:197-200,1031-1035` | `brain/sounds.py` over `aplay`+`audio_lock` |
| Thinking loop + clear-before-speak | `agent.py:1022-1029,849-854` | `start/stop_thinking()`, per-clip lock |
| Animated frames + 2-tier fps + mouth random | `agent.py:364-400` | daemon animator thread in `face.py`, 320×480 |
| Warmup + `keep_alive=-1` + WARMUP state | `agent.py:556-563,76` | `warmup_model()` + `ROBOT_OLLAMA_KEEPALIVE` |
| Separate text + vision models | `agent.py:94-95` (`gemma3:1b`+`moondream`) | optional `ROBOT_VISION_MODEL=moondream` |
| Tool-action → DDG → summarise | `agent.py:445-511,915-933` | opt-in `ANSWER` action, two-pass, default OFF |
