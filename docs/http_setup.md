# Phone remote

Speak into your phone from across the room and the robot runs the command, exactly as if you'd said "hey jarvis":
it acts, says its answer out loud and sends the answer back to your phone. Your phone's keyboard microphone turns
speech into text, and a small web server inside the robot takes it from there. The wake word keeps working.

It starts with the robot. On first start the robot prints the address and makes a password (a token), which it
saves as `ROBOT_HTTP_TOKEN` in `config/robot.env`:

```
>> Phone command endpoint READY:  http://LocalAiRobot-Jarvis.local:8080/speak
   token: Xa9_kf3...   (send as the X-Robot-Token header)
```

## Try it with curl

From the Pi or a computer on the same Wi-Fi:

```bash
curl -H "X-Robot-Token: <TOKEN>" -H "Content-Type: application/json" \
     -d '{"text":"say hello"}' http://LocalAiRobot-Jarvis.local:8080/speak
```

The robot speaks, and curl prints `{"reply": "Hi there! How can I help?", "action": "SAY"}`. A wrong token gets
`401`, empty text `400`.

To stop exploring or driving at once, even mid-turn:

```bash
curl -H "X-Robot-Token: <TOKEN>" -X POST http://LocalAiRobot-Jarvis.local:8080/stop
```

## Set up your phone

**Android**, with the free HTTP Shortcuts app:

1. Create a shortcut called "Tell Jarvis": method `POST`, URL `http://<hostname>.local:8080/speak`.
2. Body: `{"text": "{{input}}"}`, with a Text Input variable named `input`.
3. Header: `X-Robot-Token` set to your token.
4. Optionally, show the response as a toast. Add it to your home screen.

Now tap it, tap the keyboard's microphone, speak and send. Make a second shortcut that POSTs to `/stop` for a
one-tap stop.

**iPhone**, with Shortcuts: Dictate Text, then Get Contents of URL (POST, JSON body `{"text": <dictated text>}`,
header `X-Robot-Token`).

## Security

- It only answers on your own network. Don't forward the port to the internet; use a VPN such as Tailscale if
  you need remote access.
- The token travels as plain HTTP, so anyone on your Wi-Fi could read it. Use WPA2 or WPA3, and to change the
  token, empty `ROBOT_HTTP_TOKEN` and restart.
- Someone with the token can drive the robot and make it talk, within the usual safety limits. They can't see
  through the camera or listen: there's no video or audio feed, and camera commands (look, find, remember) are
  refused unless you set `ROBOT_HTTP_ALLOW_CAMERA=1`.
- Commands can't run code on the Pi. What really needs protecting is SSH: see the key setup in
  [change-pi-wifi.md](change-pi-wifi.md).

## Settings and problems

`ROBOT_HTTP_ENABLE=0` turns it off and `ROBOT_HTTP_PORT` changes the port (8080).

- **The phone can't find `.local`:** some Android versions can't; use the Pi's IP address instead.
- **`401 unauthorized`:** the token header is missing or wrong.
- **`connection refused`:** the robot isn't running, or `ROBOT_HTTP_ENABLE=0`.
- **"I can only drive and chat over the phone":** that command needs the camera, which is off over HTTP by default.
