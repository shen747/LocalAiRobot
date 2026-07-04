# brain/remote.py -- tiny token-secured HTTP endpoint so a PHONE can POST a text command to the robot.
#
# Runs on a daemon thread INSIDE the brain (started by main.py); run_me.sh is unchanged. A phone (Android
# HTTP Shortcuts / iOS Shortcuts) POSTs the text it dictated to  http://<hostname>.local:<port>/speak  with
# the shared token; we hand the text to main.py's handle_command() -- the SAME pipeline a spoken command
# uses -- and return the robot's reply as JSON. Setup + security: docs/http_setup.md.
#
# Stdlib only (http.server) -- no Flask, no pip install. Best-effort: a bad request never crashes the brain.

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("robot.remote")


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalAiRobot/1.0"

    # ---- helpers ----
    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authed(self):
        """Token via 'X-Robot-Token' header OR '?token=' query. Constant-time compare (no timing leak)."""
        token = self.server.token
        if not token:
            return True   # no token set -> open (the brain auto-generates one, so this shouldn't happen)
        sent = self.headers.get("X-Robot-Token", "")
        if not sent:
            sent = (parse_qs(urlparse(self.path).query).get("token", [""]) or [""])[0]
        return hmac.compare_digest(str(sent), str(token))

    def _extract_text(self, raw):
        """Accept JSON {"text": "..."}, form 'text=...', or raw text/plain -- whatever the phone sends."""
        ctype = (self.headers.get("Content-Type", "") or "").lower()
        s = raw.decode("utf-8", "replace").strip()
        if not s:
            return ""
        if s.startswith("{") or "application/json" in ctype:
            try:
                return str(json.loads(s).get("text", "")).strip()
            except Exception:
                pass
        if "application/x-www-form-urlencoded" in ctype:
            return (parse_qs(s).get("text", [""]) or [""])[0].strip()
        return s

    # ---- routes ----
    def do_GET(self):
        # Browser sanity check: GET the base URL to confirm the endpoint is up.
        self._json(200, {"status": "LocalAiRobot ready", "post": "/speak or /stop (token required)"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/speak", "/stop"):
            return self._json(404, {"error": "not found -- POST /speak or /stop"})
        if not self._authed():
            log.warning("remote: rejected POST %s (bad/missing token) from %s", path, self.client_address[0])
            return self._json(401, {"error": "unauthorized (bad or missing token)"})
        if path == "/stop":
            # Emergency interrupt for a running autonomous behaviour (explore/find) or timed drive. This
            # does NOT go through the command pipeline / turn_lock, so it works even WHILE an explore holds
            # the lock -- it just trips the abort flag + neutralises the ESC. No body needed.
            log.info("remote: STOP from %s", self.client_address[0])
            stopped = False
            try:
                stopped = bool(self.server.on_stop()) if self.server.on_stop else False
            except Exception as e:
                log.warning("remote: stop failed: %s", e)
            return self._json(200, {"status": "stopping" if stopped else "nothing running", "stopped": stopped})
        # ---- /speak ----
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            n = 0
        text = self._extract_text(self.rfile.read(n) if n > 0 else b"")
        if not text:
            return self._json(400, {"error": 'no text -- send JSON {"text": "..."} or form text=...'})
        log.info("remote: command from %s: %r", self.client_address[0], text[:80])
        try:
            action, reply = self.server.on_command(text, source="http")
        except Exception as e:                       # never let a handler error kill the server/brain
            log.warning("remote: command failed: %s", e)
            return self._json(500, {"error": f"command failed: {e}"})
        self._json(200, {"reply": reply or "", "action": action or ""})

    def log_message(self, *args):
        pass   # silence the default stderr access log -- we log our own lines


class RemoteServer:
    """Owns the HTTP server thread. start() to run, stop() to shut down cleanly."""

    def __init__(self, port, token, on_command, on_stop=None):
        # Bind to 0.0.0.0 so the phone can reach it over Wi-Fi/hotspot (not just localhost).
        self._httpd = ThreadingHTTPServer(("0.0.0.0", int(port)), _Handler)
        self._httpd.token = str(token or "")
        self._httpd.on_command = on_command          # = main.py remote_command(text, source=...)
        self._httpd.on_stop = on_stop                # = main.py on_stop() -> abort a running behaviour
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="remote-http")

    def start(self):
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass
