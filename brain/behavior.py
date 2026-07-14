# brain/behavior.py -- bounded autonomous explore / find-and-drive-to-object behaviour.
#
# Reached ONLY via the trigger-phrase gate in main.py (action EXPLORE / FIND). Each loop is a
# discrete stop -> look -> act step: the car is held at NEUTRAL during the (slow, CPU-bound)
# vision call, so a multi-second inference can never coincide with motion. Every forward step
# goes through driver.command("FORWARD", ...), which the 20 Hz control thread still gates on the
# forward ultrasonic (<ROBOT_OBSTACLE_CM) -- vision can be wrong; the sensor is the authority.
# Side beams keep steering off near walls. Bounded by ROBOT_EXPLORE_MAX_ITERS so it always ends.
#
# Honest limit: with a FIXED camera on a no-spin Ackermann crawler, "looking around" = driving
# short arcs, so the sweep is coarse and needs floor space (a future pan servo would fix it).

import logging
import os
import time

import vision

log = logging.getLogger("robot.behavior")

_STEER = {"left": "LEFT", "center": "CENTER", "right": "RIGHT"}


def _envi(key, default):
    try:
        return int(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return int(default)


def _envf(key, default):
    try:
        return float(os.environ.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def _is_remote(cfg):
    """True when a REMOTE model backend is configured (Gemini or any non-local) -- it's faster + smarter,
    so agentic explore gets 2x the caps and near-silent per-step cues."""
    return (cfg.get("ai_backend") or "local").lower() != "local"


def _sleep(secs, aborted):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if aborted():
            return
        time.sleep(0.05)


def run_find(cfg, driver, cam, memory, face, say, snd, target, aborted=lambda: False):
    """Explore until `target` is seen and approached, or the iteration cap is hit. Returns found?"""
    max_iters = _envi("ROBOT_EXPLORE_MAX_ITERS", 6)
    step_ms = _envi("ROBOT_EXPLORE_STEP_MS", 900)
    scan_ms = _envi("ROBOT_SCAN_STEP_MS", 700)
    speed = _envi("ROBOT_EXPLORE_SPEED", 30)
    conf_min = _envf("ROBOT_FIND_CONFIDENCE", 0.55)
    approach = _envf("ROBOT_APPROACH_CM", 40.0)

    face.set_expression("exploring")
    log.info("FIND '%s' start (max_iters=%d, speed=%d)", target, max_iters, speed)
    sweep = "LEFT"
    for i in range(max_iters):
        if aborted():
            log.info("FIND aborted by operator")
            break
        driver.stop()                                  # neutral -- safe to run slow vision
        face.set_expression("capturing")               # "I'm looking" cue during the camera grab
        frame = cam.capture_jpeg()
        snd.start_thinking()                            # proof-of-life across the slow look
        obs = vision.look(cfg, target, frame)
        snd.stop_thinking()
        face.set_expression("exploring")
        memory.record_look(obs, driver.distances())

        if obs["visible"] and obs["confidence"] >= conf_min:
            front = driver.distance_cm("front")
            if obs["distance"] == "near" or (0 <= front < approach):
                driver.stop()
                face.set_expression("happy")
                say(f"Found the {target}.")
                log.info("FIND '%s' SUCCESS at iter %d (front=%.0fcm)", target, i, front)
                return True
            steer = _STEER.get(obs["horizontal"] or "center", "CENTER")
            if driver.side_blocked(steer):
                log.info("side %s blocked -> steering CENTER", steer)
                steer = "CENTER"
            log.info("FIND iter %d: %s seen %s -> FORWARD %s", i, target, obs["horizontal"], steer)
            driver.command("FORWARD", steer, speed, step_ms)
            _sleep(step_ms / 1000.0 + 0.1, aborted)
        else:                                          # not visible -> arc-scan (no spin possible)
            steer = sweep
            if driver.side_blocked(steer):
                steer = "RIGHT" if sweep == "LEFT" else "LEFT"
            log.info("FIND iter %d: not visible -> arc-scan %s", i, steer)
            driver.command("FORWARD", steer, speed, scan_ms)
            _sleep(scan_ms / 1000.0 + 0.1, aborted)
            sweep = "RIGHT" if sweep == "LEFT" else "LEFT"

    driver.stop()
    face.set_expression("confused")
    say(f"I could not find the {target}.")
    log.info("FIND '%s' gave up after %d iters", target, max_iters)
    return False


def run_explore(cfg, driver, cam, memory, face, say, snd, aborted=lambda: False):
    """AGENTIC look-around: each step the model sees the frame + memory + sensor distances and decides the
    next move AND whether it's satisfied (vision.explore_step). Bounded by BACKEND-AWARE iteration + time
    caps (2x on remote) and aborted() (HTTP /stop, wake word, or Ctrl-C). Every forward step is still
    ultrasonic-gated by driver.command, and the car is held at NEUTRAL during each (slow) look. The full
    scene descriptions are stored (memory.record_scene) so a later run reuses what this run learned.
    Returns the distinct objects seen."""
    remote = _is_remote(cfg)
    if remote:
        max_iters = max(1, _envi("ROBOT_EXPLORE_MAX_ITERS_REMOTE", 24))
        max_secs = _envf("ROBOT_EXPLORE_MAX_SECONDS_REMOTE", 120)
        think_lead, think_repeat = _envi("ROBOT_THINKING_LEAD_REMOTE_MS", 4000), 0
    else:
        max_iters = max(1, _envi("ROBOT_EXPLORE_MAX_ITERS", 12))
        max_secs = _envf("ROBOT_EXPLORE_MAX_SECONDS", 60)
        think_lead, think_repeat = _envi("ROBOT_THINKING_LEAD_MS", 500), _envi("ROBOT_THINKING_REPEAT_S", 10)
    step_ms = _envi("ROBOT_EXPLORE_STEP_MS", 900)
    speed = _envi("ROBOT_EXPLORE_SPEED", 15)
    narrate = os.environ.get("ROBOT_EXPLORE_NARRATE", "1") == "1"

    face.set_expression("exploring")
    log.info("EXPLORE (agentic) start: backend=%s max_iters=%d max_secs=%.0f speed=%d",
             "remote" if remote else "local", max_iters, max_secs, speed)
    deadline = time.monotonic() + max_secs
    seen, reason = [], "iteration cap"
    for i in range(max_iters):
        if aborted():
            reason = "aborted (stop)"
            break
        if time.monotonic() >= deadline:
            reason = "time cap"
            break
        driver.stop()                                  # NEUTRAL while the (slow) camera + LLM step runs
        face.set_expression("capturing")
        frame = cam.capture_jpeg()
        snd.start_thinking(lead_ms=think_lead, repeat_s=think_repeat)
        step = vision.explore_step(cfg, frame, goal="roam and cover as much of the room as you can -- keep moving toward open, unexplored areas, don't stop after one look",
                                   memory_summary=memory.context_string(), distances=driver.distances())
        snd.stop_thinking()
        face.set_expression("exploring")
        # RAG: store the FULL scene description + objects so a later (offline/local) run reuses it.
        memory.record_scene(step.get("description", ""), step.get("objects", []), driver.distances())
        new_objs = []
        for o in step.get("objects", []):
            if o and o.lower() not in (s.lower() for s in seen):
                seen.append(o)
                new_objs.append(o)
        # Narrate ONLY when something NEW is in view (and keep it SHORT). Re-reading the same scene every
        # step -- a 6-8s TTS sentence each time -- is what made explore feel stuck on "talking"; now it
        # drives quietly until it sees something new, then remarks briefly. The wrap-up still lists all.
        if narrate and new_objs and step.get("speak"):
            face.set_expression("talking")
            say(step["speak"][:140])
            face.set_expression("exploring")
        if step.get("done"):
            reason = "model satisfied"
            break
        if aborted():
            reason = "aborted (stop)"
            break
        if step.get("move") == "STOP":
            _sleep(0.4, aborted)                       # model wants to hold + look again; don't drive
            continue
        steer = step.get("steer") or "CENTER"
        if steer in ("LEFT", "RIGHT") and driver.side_blocked(steer):
            log.info("EXPLORE iter %d: side %s blocked -> CENTER", i, steer)
            steer = "CENTER"
        log.info("EXPLORE iter %d: FORWARD %s (%s)", i, steer, step.get("reason", "")[:60])
        driver.command("FORWARD", steer, speed, step_ms)   # sensor-gated in the 20Hz control thread
        _sleep(step_ms / 1000.0 + 0.1, aborted)

    driver.stop()
    face.set_expression("talking")
    if seen:
        say("I had a look around. I can see " + ", ".join(seen[:6]) + ".")
    else:
        say("I had a look around but I'm not sure what I'm seeing.")
    face.set_expression("idle")
    log.info("EXPLORE (agentic) done (%s), objects=%s", reason, seen)
    return seen
