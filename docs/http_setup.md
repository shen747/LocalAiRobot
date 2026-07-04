# Command the robot from your phone (HTTP)

> 📱 Speak a command into your phone from across the room and the robot runs it — **the same brain the
> wake word uses.** Your phone's keyboard turns your speech into text and POSTs it to a tiny endpoint on
> the Pi; the robot **acts, speaks its reply out loud, and returns that reply to your phone as JSON.**

This is *in addition to* the wake word + whisper.cpp — they stay fully active. It's handy when you're too
far for the mic, or don't want to say "hey jarvis" every time.

## How it works (nothing to install on the Pi)
A token-secured HTTP server runs **inside the brain** — it starts automatically whenever the brain runs
(`./scripts/run_me.sh`, `--detach`, or the boot service). `run_me.sh` is unchanged; there's no separate
service to manage, and no `pip install` (it's Python stdlib).

**On the very first launch** you'll see the ready-to-use URL + an auto-generated token printed once:
```
>> Phone command endpoint READY:  http://LocalAiRobot-Jarvis.local:8080/speak
   token: Xa9_kf3...   (send as the X-Robot-Token header)  -- Android setup: docs/http_setup.md
```
Copy that URL + token into your phone (below). The token is saved to `config/robot.env`
(`ROBOT_HTTP_TOKEN`), so it stays the same across reboots. (`http://<hostname>.local` works because the
installer adds `avahi-daemon` for mDNS — no IP address to hunt down.)

## Test it first with `curl` (from the Pi or a laptop on the same Wi-Fi)
```bash
curl -H "X-Robot-Token: <TOKEN>" -H "Content-Type: application/json" \
     -d '{"text":"say hello"}' http://LocalAiRobot-Jarvis.local:8080/speak
```
The robot **speaks a reply**, and curl prints it back:
```json
{"reply": "Hi there! How can I help?", "action": "SAY"}
```
Try `{"text":"go forward"}` (wheels off / clear space) — it crawls forward, with all the usual failsafes
(obstacle gate, duration cap, kill switch). Wrong/missing token → `401`; empty text → `400`.

### Stop a running explore / behaviour
An agentic **explore** (or any autonomous behaviour) can be interrupted from your phone via a separate
`/stop` route — it does **not** wait for the current turn, so it works even *while* explore is busy:
```bash
curl -H "X-Robot-Token: <TOKEN>" -X POST http://LocalAiRobot-Jarvis.local:8080/stop
```
It trips the abort + neutralises the ESC, replying `{"stopped": true}` if something was running. Make a
second HTTP-Shortcut ("Stop Jarvis") pointing at `/stop` (same token, no body) for a one-tap stop. You can
also just say the wake word — *"hey jarvis"* — to stop it, or Ctrl-C over SSH.

## Android — one-time shortcut (HTTP Shortcuts app)
1. Install **HTTP Shortcuts** (free, open-source — F-Droid or Play Store).
2. **Create Shortcut** → give it a name (e.g. "Tell Jarvis").
3. **Basic Request:**
   - **Method:** `POST`
   - **URL:** `http://LocalAiRobot-Jarvis.local:8080/speak`
4. **Request Body:** type **Text** (or JSON), content:
   ```json
   {"text": "{{input}}"}
   ```
   …and add a **variable** of type **Text Input** named `input` (so tapping the shortcut pops up a text
   box — where you tap the **keyboard's mic** and speak).
5. **Request Headers:** add `X-Robot-Token` = `<YOUR_TOKEN>`.
6. *(Optional)* Under **Response handling**, show the response as a **Toast** so you see Jarvis's reply.
7. **Add to home screen.** Now: tap → keyboard mic → speak → send. The robot acts + speaks, and your phone
   shows the reply.

*(iOS works too — Apple **Shortcuts**: "Dictate Text" → "Get Contents of URL" (POST, JSON body
`{"text": <dictated>}`, header `X-Robot-Token`). Trigger via Siri or a home-screen widget.)*

## 🔒 Security & privacy — what a stolen token can and can't do
- **A token only works from your LAN.** The endpoint is **not exposed to the internet** unless you
  deliberately port-forward it — so **don't**. A remote attacker can't reach it; someone would have to be on
  your Wi-Fi first.
- **A stolen token lets someone *command* the robot** (drive / speak / — if you allow it — a one-shot
  photo-describe). Annoying, but bounded by the same failsafes as any command. It travels as **plain HTTP**,
  so anyone *already on the same Wi-Fi* could sniff it — keep the Pi on **WPA2/WPA3** and rotate the token
  (`ROBOT_HTTP_TOKEN=` empty → next launch regenerates it) if worried.
- **The API canNOT stream your camera or microphone.** It's text-in / text-out. There is no video or audio
  feed. The worst a token-holder could get is a **one-shot text description** of the current camera view —
  and by default **even that is blocked over HTTP** (`ROBOT_HTTP_ALLOW_CAMERA=0`): camera actions
  (look around / find / remember) are refused and answered *"I can only drive and chat over the phone."*
  Set `ROBOT_HTTP_ALLOW_CAMERA=1` only if you want them.
- **No token can turn the robot into a spy tool.** A command is data → LLM prompt → whitelisted, clamped
  action → Piper via stdin (no shell). There's no path from the endpoint to running code. Real surveillance
  would require **SSH/root** — a *completely separate* compromise.
- **So the real thing to lock down is SSH**, not this endpoint: use **Ed25519 keys + disable password auth**
  (see [docs/change-pi-wifi.md](change-pi-wifi.md)). For strong remote access, use a **VPN (Tailscale)**
  rather than exposing anything. (HTTPS on a `.local` box is fiddly and planned for later.)

## Turn it off / tune it
All in [`config/robot.env`](../config/robot.env) (reference: [`config/README.md`](../config/README.md)):

| Key | Default | Meaning |
|---|---|---|
| `ROBOT_HTTP_ENABLE` | `1` | `0` = don't start the endpoint at all. |
| `ROBOT_HTTP_PORT` | `8080` | Port in the URL. |
| `ROBOT_HTTP_TOKEN` | *(auto)* | Shared secret; empty → regenerated on next launch. |
| `ROBOT_HTTP_ALLOW_CAMERA` | `0` | `1` = allow look-around / find / remember over HTTP. |

## Troubleshooting
- **Phone can't resolve `…​.local`** → some Android versions are flaky with mDNS. Use the Pi's **IP** in the
  URL instead (`http://192.168.x.y:8080/speak`), or check the Pi with `hostname` / `avahi-resolve -n
  $(hostname).local`.
- **`401 unauthorized`** → the `X-Robot-Token` header is missing or doesn't match `ROBOT_HTTP_TOKEN`.
- **`connection refused`** → the brain isn't running, `ROBOT_HTTP_ENABLE=0`, or a firewall (Pi OS has none by
  default) — start `./scripts/run_me.sh` and re-check the printed URL.
- **Reply says "I can only drive and chat over the phone"** → that command needed the camera; set
  `ROBOT_HTTP_ALLOW_CAMERA=1` to permit it.
