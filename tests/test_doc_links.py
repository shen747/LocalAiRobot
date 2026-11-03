"""Every relative link and image in the repo's Markdown must point at something that exists.

A dead link fails silently: the page still renders and the reader just hits a 404. This checks every link in
every tracked .md file, including #anchors into Markdown pages (matched against GitHub's heading slugs), so a
moved page or a renamed heading fails the test run instead.
"""
import os
import re
import subprocess
import sys
from urllib.parse import unquote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []

LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_REF = re.compile(r"""<(?:img|a)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
HTML_ID = re.compile(r"""<a\b[^>]*?\b(?:name|id)\s*=\s*["']([^"']+)["']""", re.I)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
CODE_SPAN = re.compile(r"`[^`]*`")


def tracked_files():
    """Every file git tracks, or None without git. A link to an untracked file (config/robot.env, say) works on
    the machine that has it and 404s for everyone else, so with git only tracked files count as existing."""
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [f for f in out.splitlines() if f]


TRACKED = tracked_files()
if TRACKED is not None:
    EXISTING = set()
    for f in TRACKED:
        parts = f.split("/")
        EXISTING.update(os.path.normpath(os.path.join(REPO, *parts[:i])) for i in range(1, len(parts) + 1))


def exists(path):
    return path in EXISTING if TRACKED is not None else os.path.exists(path)


def md_files():
    if TRACKED is not None:
        return [f for f in TRACKED if f.endswith(".md")]
    found = []                                          # no git: walk the tree, skipping hidden dirs and venvs
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [os.path.relpath(os.path.join(root, f), REPO) for f in files if f.endswith(".md")]
    return found


def prose_lines(path):
    """The file's lines outside fenced code blocks."""
    inside = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if FENCE.match(line):
                inside = not inside
                continue
            if not inside:
                yield line


def slug(text):
    """GitHub's anchor for a heading: link text kept, markup and punctuation dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


_anchors = {}


def anchors(path):
    if path not in _anchors:
        seen, found = {}, set()
        for line in prose_lines(path):
            found.update(HTML_ID.findall(line))
            m = HEADING.match(line)
            if m:
                s = slug(m.group(1))
                n = seen.get(s, 0)
                seen[s] = n + 1
                found.add(s if n == 0 else f"{s}-{n}")
        _anchors[path] = found
    return _anchors[path]


for rel in md_files():
    src = os.path.join(REPO, rel)
    for line in prose_lines(src):
        line = CODE_SPAN.sub("", line)
        for target in LINK.findall(line) + HTML_REF.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):      # http:, https:, mailto: ...
                continue
            path, _, anchor = unquote(target).partition("#")
            dest = os.path.normpath(os.path.join(os.path.dirname(src), path) if path else src)
            if not exists(dest):
                FAILS.append(f"{rel}: {target} (no such file in the repo)")
            elif anchor and dest.endswith(".md") and anchor not in anchors(dest):
                FAILS.append(f"{rel}: {target} (no heading for #{anchor})")

if FAILS:
    print("  FAIL  broken links in the Markdown:")
    for f in FAILS:
        print(f"          {f}")
else:
    print("  PASS  every relative link and anchor in the Markdown resolves")
print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not FAILS else 'FAILURES: ' + str(len(FAILS))}")
print("=" * 58)
sys.exit(1 if FAILS else 0)
