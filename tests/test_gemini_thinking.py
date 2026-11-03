"""A thinking LEVEL must REPLACE the budget, never ride alongside it -- Gemini answers both with HTTP 400."""
import json
import os
import sys

sys.path.insert(0, os.environ["BRAIN_DIR"])
import inference

F = []
KEYS = ("ROBOT_GEMINI_VISION_THINKING_LEVEL", "ROBOT_GEMINI_THINKING_LEVEL",
        "ROBOT_GEMINI_VISION_THINKING_BUDGET", "ROBOT_GEMINI_THINKING_BUDGET")


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


def env(**kv):
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update(kv)


def thinking(is_vision, **kv):
    env(**kv)
    return inference._gemini_thinking_config(is_vision)


# -- the selection rule ----------------------------------------------------------------------------------
ck("vision, nothing set -> budget 0 (the old default, unchanged)", thinking(True), {"thinkingBudget": 0})
ck("text, nothing set -> send nothing (model default)", thinking(False), None)
ck("vision level set -> level ONLY, the budget is dropped",
   thinking(True, ROBOT_GEMINI_VISION_THINKING_LEVEL="minimal", ROBOT_GEMINI_VISION_THINKING_BUDGET="0"),
   {"thinkingLevel": "minimal"})
ck("level is trimmed + lower-cased", thinking(True, ROBOT_GEMINI_VISION_THINKING_LEVEL=" Low "),
   {"thinkingLevel": "low"})
ck("blank level -> falls back to the budget",
   thinking(True, ROBOT_GEMINI_VISION_THINKING_LEVEL="", ROBOT_GEMINI_VISION_THINKING_BUDGET="-1"),
   {"thinkingBudget": -1})
ck("budget 'auto' and no level -> send nothing",
   thinking(True, ROBOT_GEMINI_VISION_THINKING_BUDGET="auto"), None)
ck("the VISION level never leaks into text",
   thinking(False, ROBOT_GEMINI_VISION_THINKING_LEVEL="minimal", ROBOT_GEMINI_THINKING_BUDGET="0"),
   {"thinkingBudget": 0})
ck("text level set -> level only",
   thinking(False, ROBOT_GEMINI_THINKING_LEVEL="medium", ROBOT_GEMINI_THINKING_BUDGET="0"),
   {"thinkingLevel": "medium"})


# -- the wire: what _gemini_generate actually POSTs (fake transport, no network) --------------------------
class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


SENT = []


def _fake_urlopen(req, timeout=None):
    SENT.append((req.full_url, json.loads(req.data)))
    return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode())


inference.urllib.request.urlopen = _fake_urlopen
env(ROBOT_GEMINI_VISION_THINKING_LEVEL="minimal", ROBOT_GEMINI_VISION_THINKING_BUDGET="0")
inference._gemini_generate({"gemini_api_key": "k", "gemini_vision_model": "gemini-robotics-er-2-preview"},
                           {"prompt": "what do you see?", "images": ["aGk="]}, 5)
url, body = SENT[-1]
ck("vision call goes to the ER 2 model", url.endswith("/models/gemini-robotics-er-2-preview:generateContent"),
   True)
ck("vision body carries the level and NO budget", body["generationConfig"].get("thinkingConfig"),
   {"thinkingLevel": "minimal"})

print("\n" + "=" * 60)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + ', '.join(F)}")
print("=" * 60)
sys.exit(1 if F else 0)
