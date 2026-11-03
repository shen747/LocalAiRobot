# brain/captures.py -- keep the frame Jarvis ACTUALLY LOOKED AT when a motion wake fired.
#
# WHY: in Concierge Mode a PIR trigger makes the robot smile and grab a burst of frames, then greet (by name /
# generically) or stay silent. When that goes wrong -- a "ghost" greeting with nobody there, or a familiar face
# it failed to recognise -- the log alone can't tell you WHY, because the evidence (the frame) was thrown away.
# So we write the DECISIVE frame to a folder you can browse: ~/detect-captures.
#
# Retention is deliberately BELT-AND-BRACES, because a 24/7 greeter on a 250GB NVMe would otherwise fill it:
#   * a cron job purges the folder by AGE (scripts/purge_detect_captures.sh, installed by run_me.sh
#     --install-boot / install_stack_pi5.sh / install_stack_jetson.sh), and
#   * this module ALSO self-prunes in-process (age + a hard size cap), so a robot whose cron never got
#     installed -- or that captures far faster than the daily purge -- still can't run the disk out.
#
# Pure stdlib, never raises: a full/read-only disk degrades to "no capture saved", never a crashed greeting.

import logging
import os
import re
import time

log = logging.getLogger("robot.captures")

_TRUE = {"1", "true", "yes", "on"}
_DEFAULT_DIR = "~/detect-captures"


def _envf(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except (TypeError, ValueError):
        return float(default)


def enabled():
    """True when detect-capture keeping is on (ROBOT_DETECT_CAPTURE, default ON)."""
    return (os.environ.get("ROBOT_DETECT_CAPTURE", "1") or "1").strip().lower() in _TRUE


def keep_all():
    """True when even the SILENT looks (motion -> no face -> no greeting) are kept. Default off: those are the
    overwhelming majority of frames on a chattery PIR, and keeping them is what fills a disk. Turn it on
    (ROBOT_DETECT_CAPTURE_MODE=all) for a session when you're diagnosing false PIR triggers."""
    return (os.environ.get("ROBOT_DETECT_CAPTURE_MODE", "greeted") or "greeted").strip().lower() == "all"


def directory():
    """The capture folder (ROBOT_DETECT_CAPTURE_DIR, default ~/detect-captures), expanded."""
    return os.path.expanduser((os.environ.get("ROBOT_DETECT_CAPTURE_DIR", "") or _DEFAULT_DIR).strip()
                              or _DEFAULT_DIR)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def save(jpeg_bytes, outcome, name=None, when=None):
    """Write one JPEG into the capture folder. Returns the path, or None (disabled / no frame / write failed).

    `outcome` is the greeting decision this frame produced -- "known", "unknown", "noface" or "spook" -- and
    goes in the FILENAME, so `ls ~/detect-captures` reads as an audit trail of why Jarvis spoke:
        20260730-074742-known-ramesh.jpg     recognised -> greeted by name (this is the matching frame)
        20260730-074743-unknown.jpg          a face was seen but not matched -> generic greeting
        20260730-013120-noface.jpg           motion but no face -> stayed silent (only with MODE=all)
        20261031-195512-spook.jpg            Halloween: the frame that confirmed somebody was there -> spooked
        20261031-195514-spook-reaction.jpg   ...and their face a couple of seconds into the scare
    The timestamp leads so the names sort chronologically (and the cron purge can glob by date).
    """
    if not enabled() or not jpeg_bytes:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when if when is not None else time.time()))
    tail = _slug(name)
    fname = f"{stamp}-{_slug(outcome) or 'look'}{'-' + tail if tail else ''}.jpg"
    d = directory()
    path = os.path.join(d, fname)
    try:
        os.makedirs(d, exist_ok=True)
        # Collisions are possible (two looks inside the same second) -- suffix rather than overwrite, so we
        # never lose the frame that explains a greeting.
        if os.path.exists(path):
            base = path[:-4]
            for n in range(2, 100):
                if not os.path.exists(f"{base}-{n}.jpg"):
                    path = f"{base}-{n}.jpg"
                    break
        with open(path, "wb") as f:
            f.write(jpeg_bytes)
        log.debug("detect capture saved: %s (%d bytes)", path, len(jpeg_bytes))
        return path
    except Exception as e:
        log.warning("detect capture save failed (%s) -- greeting is unaffected", e)
        return None


def prune(directory_path=None, keep_days=None, max_mb=None):
    """In-process safety net: delete captures older than ROBOT_DETECT_CAPTURE_KEEP_DAYS, then -- if the folder
    is STILL over ROBOT_DETECT_CAPTURE_MAX_MB -- delete oldest-first until it fits. The cron purge does the
    same thing on a schedule; this exists so a robot with no cron installed (or one capturing faster than the
    daily purge) still can't fill the disk. Returns (files_deleted, bytes_freed). Never raises."""
    d = directory_path or directory()
    days = _envf("ROBOT_DETECT_CAPTURE_KEEP_DAYS", 7) if keep_days is None else float(keep_days)
    cap_mb = _envf("ROBOT_DETECT_CAPTURE_MAX_MB", 2000) if max_mb is None else float(max_mb)
    n, freed = 0, 0
    try:
        entries = []
        with os.scandir(d) as it:
            for e in it:
                if not e.is_file() or not e.name.lower().endswith(".jpg"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append([e.path, st.st_mtime, st.st_size])
    except FileNotFoundError:
        return 0, 0
    except Exception as e:
        log.debug("detect capture prune scan failed: %s", e)
        return 0, 0

    def _rm(item):
        nonlocal n, freed
        try:
            os.remove(item[0])
            n += 1
            freed += item[2]
            return True
        except OSError:
            return False

    entries.sort(key=lambda i: i[1])            # oldest first
    if days > 0:
        cutoff = time.time() - days * 86400.0
        survivors = []
        for item in entries:
            if item[1] < cutoff and _rm(item):
                continue
            survivors.append(item)
        entries = survivors
    if cap_mb > 0:
        total = sum(i[2] for i in entries)
        cap = cap_mb * 1024 * 1024
        for item in entries:                     # oldest first until we're under the cap
            if total <= cap:
                break
            if _rm(item):
                total -= item[2]
    if n:
        log.info("detect captures: pruned %d file(s), freed %.1f MB from %s", n, freed / 1048576.0, d)
    return n, freed
