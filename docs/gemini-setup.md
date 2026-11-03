# Gemini as the brain

By default the robot thinks on the Pi, offline. With a Google Gemini API key it can use Gemini instead for
understanding commands and describing what the camera sees. Speech, the sensors, the motors, the face and the
wake word stay on the Pi either way.

- **Faster**, especially for vision, which takes tens of seconds on the Pi.
- **Online:** it needs a network connection.
- **Your words and camera frames go to Google.**
- **It can cost money** beyond the free tier's limits.

## 1. Get a key

1. Sign in at [aistudio.google.com](https://aistudio.google.com).
2. Create a project (Projects, then Create a new project).
3. Open API Keys, then Create API key, and pick the project.
4. Copy the key (it starts with `AIza`). Treat it like a password.

The free tier works straight away with rate limits; turn on billing for more. Restricting the key to the
Generative Language API limits the damage if it leaks.

## 2. Point the robot at it

On the Pi, put the key in your settings without leaving it in your shell history:

```bash
cd ~/LocalAiRobot
read -rsp 'Gemini API key: ' KEY && echo
sed -i -e "s|^ROBOT_GEMINI_API_KEY=.*|ROBOT_GEMINI_API_KEY=${KEY}|" \
       -e 's|^ROBOT_AI_BACKEND=.*|ROBOT_AI_BACKEND=mixed|' config/robot.env
unset KEY
./scripts/run_me.sh --reload
```

`config/robot.env` is ignored by git, so the key never reaches GitHub. Pick a backend:

| `ROBOT_AI_BACKEND` | Behaviour |
|---|---|
| `local` | everything on the Pi (the default) |
| `gemini` | Gemini for commands and vision |
| `mixed` | Gemini, falling back to the Pi whenever Gemini can't be reached |

Without a key, `gemini` and `mixed` stay local. The models are `ROBOT_GEMINI_MODEL` (text) and
`ROBOT_GEMINI_VISION_MODEL` (vision); see the [settings reference](../config/README.md#ai-backend).

## 3. Test it

Check the key on its own (expect a reply containing `candidates`):

```bash
set -a; source config/robot.env; set +a      # load the new settings into this shell
curl -s -H "x-goog-api-key: $ROBOT_GEMINI_API_KEY" -H 'Content-Type: application/json' \
  "$ROBOT_GEMINI_URL/models/$ROBOT_GEMINI_MODEL:generateContent" \
  -d '{"contents":[{"parts":[{"text":"reply with one word: ready"}]}]}'
```

Then start the robot: the log shows `AI brain: REMOTE Gemini (...)`. Give it a command and say "look around". A
missing or wrong key is logged clearly, and each command safely becomes a stop until you fix it. If a key ever
leaks, delete it in AI Studio and make a new one.

## Going back

Set `ROBOT_AI_BACKEND=local` and reload. Nothing else to undo.
