"""Catch UnboundLocalError-by-construction: a nested helper CALLED before its `def` in the same scope.

Why this exists. brain/main.py's `main()` is a long function that defines dozens of nested helpers and then
uses them. Python resolves those names at CALL time, so referencing one above its `def` parses, imports and
lints clean -- and then raises UnboundLocalError the first time that line actually runs. A startup-path
example shipped once and the robot never left idle:

    UnboundLocalError: cannot access local variable '_concierge_recognize_s'

The file's own comment already warned about it ("referencing them any earlier in main() is an unbound
local"), which is exactly the kind of knowledge that lives in a comment and dies in a review. This makes it
a gate instead.

Scope: only flags calls at the SAME nesting depth as the def -- i.e. straight-line code in the enclosing
function body. Calls from INSIDE another nested function are fine no matter where they sit, because that
body runs later; flagging those would be a flood of false positives.
"""
import ast
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(path):
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Where each nested helper becomes bound.
        defined = {n.name: n.lineno for n in outer.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if not defined:
            continue
        # Statements of THIS body only -- descend into nested defs is deliberately skipped below.
        for stmt in outer.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue          # its body runs later; any order is fine in there
            for node in ast.walk(stmt):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                name = node.func.id
                if name in defined and node.lineno < defined[name]:
                    FAILS.append(f"{os.path.basename(path)}:{node.lineno} calls {name}() "
                                 f"but it is only defined at line {defined[name]} "
                                 f"(inside {outer.name}) -> UnboundLocalError at runtime")


for rel in ("brain/main.py", "brain/pir.py", "brain/concierge.py",
            "brain/wake_sources.py", "brain/zigbee_wake.py", "brain/face_id.py"):
    p = os.path.join(REPO, rel)
    if os.path.exists(p):
        check(p)

if FAILS:
    print("  FAIL  forward references that will raise UnboundLocalError:")
    for f in FAILS:
        print(f"          {f}")
else:
    print("  PASS  no nested helper is called above its def in the same scope")
print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + str(len(FAILS))}")
print("=" * 58)
sys.exit(1 if FAILS else 0)
