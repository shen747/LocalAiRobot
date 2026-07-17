# Use Gemini as the remote brain (API key + setup)

By default the robot's brain runs **100% locally** on the Pi (Ollama) and is fully offline. This guide is
**optional**: it shows how to get a **Google Gemini** API key and point the robot at it instead, so the
**text-intent and camera vision** run on Google's servers. One switch (`ROBOT_AI_BACKEND`) flips both;
flip it back anytime.

## When to use it — and the trade-offs

- ✅ **Faster** than the Pi 5 CPU (vision especially), and no local model download.
- ⚠️ **Needs network** — not offline anymore.
- ⚠️ **Data leaves the device** — your transcripts **and camera frames** are sent to Google.
- ⚠️ **May cost money** past the free tier's rate limits.

Speech (whisper STT / piper TTS), the sensors, motors, face and the wake word stay **local either way** —
only the LLM/vision calls change.

---

## 1. Get a Gemini API key (Google AI Studio)

Every key is tied to a Google Cloud **project** (that's what handles billing and permissions); AI Studio
gives you a simple UI over it.

1. Sign in at **[aistudio.google.com](https://aistudio.google.com)** with a Google account.
2. **Create (or select) a project** — left nav **Projects → Create a new project**, name it anything
   (e.g. `LocalAiRobot-Jarvis`), **Create project**.
3. Left nav **API Keys → Create API key**, and choose that project.
4. **Copy the key** (it starts with `AIza…`). Treat it like a password — don't share or commit it.
5. **Free vs paid:** the **free tier** works out of the box but is rate-limited. For higher limits, open
   **Billing** and enable billing on the project. Watch **Usage / Spend** in the left nav.
6. *(Optional, recommended)* restrict the key to the **Generative Language API** so a leaked key can't be
   used for anything else.

---

## 2. Point the robot at Gemini (`config/robot.env`)

Edit [`config/robot.env`](../config/robot.env) and set:

```bash
ROBOT_AI_BACKEND=gemini
ROBOT_GEMINI_API_KEY=AIza...your-key-here...
# optional overrides (sensible defaults already set):
# ROBOT_GEMINI_MODEL=gemini-3.5-flash
# ROBOT_GEMINI_VISION_MODEL=gemini-robotics-er-1.6-preview   # ships set to Robotics-ER; leave empty to reuse ROBOT_GEMINI_MODEL
```

Then reload the config (the brain reads the **shell**, not the file):

```bash
set -a; source config/robot.env; set +a      # or just open a new SSH session; or ./scripts/run_me.sh --reload
```

### Paste the key over SSH without an editor (keeps it out of GitHub)

`ROBOT_GEMINI_API_KEY=` ships **empty** in the repo, so your key is never on GitHub. On the Pi, inject your
real key with `sed` instead of opening an editor (the `|` delimiter avoids any clash — replace `AIza…`):

```bash
sed -i "s|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=AIza...your-key...|" config/robot.env
```

To avoid leaving the key in your shell history, read it into a variable first (no echo) — history then
records only `${KEY}`, not the key itself:

```bash
read -rsp 'Gemini API key: ' KEY && echo
sed -i "s|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=${KEY}|" config/robot.env
unset KEY
```

Check it took (without printing the key):

```bash
grep -q '^ROBOT_GEMINI_API_KEY=AIza' config/robot.env && echo "key set" || echo "key NOT set"
```

> 🔒 **Keep the key secret.** `config/robot.env` is a **tracked** file, so your edit shows in `git status`.
> Simplest rule: **never `git add config/robot.env`** (and avoid blind `git commit -a`). Belt-and-braces,
> tell git to ignore your local edits to it:
> ```bash
> git update-index --skip-worktree config/robot.env      # undo with: --no-skip-worktree
> ```
> (While that's on, a `git pull` that also changes this file upstream may complain — temporarily
> `--no-skip-worktree`, pull, then re-apply.) If a key ever leaks, delete/rotate it in AI Studio → API Keys.

---

## 3. Test it

**Key sanity check** (one line; expects a `candidates[…]` reply):

```bash
curl -s -H "x-goog-api-key: $ROBOT_GEMINI_API_KEY" -H 'Content-Type: application/json' \
  "$ROBOT_GEMINI_URL/models/$ROBOT_GEMINI_MODEL:generateContent" \
  -d '{"contents":[{"parts":[{"text":"reply with one word: ready"}]}]}'
```

**Run the brain** — the startup banner now reads `AI brain: REMOTE Gemini (gemini-3.5-flash) …`:

```bash
./scripts/run_me.sh --live        # (or --dry to bench it)
```

Give a drive command (the intent JSON comes back from Gemini) and say **"look around"** (the camera frame
is sent to Gemini for the scene description). If the key is missing/invalid the brain logs a clear error
and each command **fail-safes to STOP** — fix the key and re-source.

---

## 4. Go back to local (offline)

```bash
ROBOT_AI_BACKEND=local            # in config/robot.env, then re-source
```

That's it — fully offline again, nothing else to undo.

---

## Notes

- **What switches:** both text-intent **and** vision → Gemini. Everything else stays local.
- **Timeouts:** the remote path reuses `ROBOT_LLM_TIMEOUT_S` (text) and `ROBOT_VISION_TIMEOUT_S` (vision).
- **All the variables** are documented in the [config reference](../config/README.md#ai-backend-local-ollama-vs-remote-gemini).
