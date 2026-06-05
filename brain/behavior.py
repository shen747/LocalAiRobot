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


def _sleep(secs, aborted):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if aborted():
            return
        time.sleep(0.05)


def run_find(cfg, driver, cam, memory, face, say, target, aborted=lambda: False):
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
        obs = vision.look(cfg, target, cam.capture_jpeg())
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


def run_explore(cfg, driver, cam, memory, face, say, aborted=lambda: False):
    """Look around (coarse arc-scan), record what's seen to memory, and describe it. Returns objects."""
    scans = max(1, _envi("ROBOT_EXPLORE_SCANS", 3))
    scan_ms = _envi("ROBOT_SCAN_STEP_MS", 700)
    speed = _envi("ROBOT_EXPLORE_SPEED", 30)

    face.set_expression("exploring")
    log.info("EXPLORE start (scans=%d)", scans)
    seen, sweep = [], "LEFT"
    for i in range(scans):
        if aborted():
            break
        driver.stop()                                  # neutral while the camera/LLM works
        desc = vision.describe(cfg, cam.capture_jpeg())
        for o in desc.get("objects", []):
            memory.record("explore", target=o, visible=True, distances=driver.distances())
            if o.lower() not in (s.lower() for s in seen):
                seen.append(o)
        if i < scans - 1:
            steer = sweep if not driver.side_blocked(sweep) else ("RIGHT" if sweep == "LEFT" else "LEFT")
            driver.command("FORWARD", steer, speed, scan_ms)
            _sleep(scan_ms / 1000.0 + 0.1, aborted)
            sweep = "RIGHT" if sweep == "LEFT" else "LEFT"

    driver.stop()
    face.set_expression("talking")
    if seen:
        say("I had a look around. I can see " + ", ".join(seen[:6]) + ".")
    else:
        say("I had a look around but I'm not sure what I'm seeing.")
    face.set_expression("idle")
    log.info("EXPLORE done, objects=%s", seen)
    return seen
