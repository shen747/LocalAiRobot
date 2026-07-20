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
    """True when VISION is served by a REMOTE model (Gemini) -- faster + smarter, so agentic explore (whose
    every step IS a vision call) gets 2x the caps and near-silent per-step cues. Under ROBOT_AI_BACKEND=mixed
    this tracks the VISION backend (remote only when a key is set), NOT the local text LLM."""
    return vision.vision_backend(cfg) == "gemini"


def _clear(cm):
    """A side beam's usable clearance in cm. A FAILED read (-1.0) counts as WIDE OPEN so a dead/again-jittering
    side sensor never traps the escape into always turning the same way."""
    return 999.0 if not isinstance(cm, (int, float)) or cm < 0 else float(cm)


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
    # The weak LOCAL VLM often returns done=True on the very FIRST frame (it "recognised" a few objects and
    # calls the room explored) -- which made explore stop having driven ZERO steps. Force at least this many
    # movement steps before honouring "done", so an explore command ALWAYS actually moves.
    min_iters = max(0, _envi("ROBOT_EXPLORE_MIN_ITERS", 2))
    # Obstacle ESCAPE: the VLM step can only choose FORWARD/STOP, so at a wall it just oscillates STOP while the
    # hard FORWARD-cut gate blocks forward -- the car gets pinned. When the FRONT beam is within escape_cm (a
    # margin ABOVE the hard gate) we skip the slow VLM step and do a deterministic back-up-and-turn toward the
    # more-open side. escape_cm should be > ROBOT_OBSTACLE_CM so we react BEFORE slamming into the gate.
    escape_cm = _envf("ROBOT_EXPLORE_ESCAPE_CM", 25.0)
    # The escape REVERSE is BLIND (no rear beam), so keep the SPEED a low crawl -- a gentle back-off that taps,
    # never slams, anything behind. Give it DURATION instead (escape_ms, default 1.5s) to actually clear the
    # obstacle before turning: at crawl speed a longer reverse is still a tap. Raise escape_ms (the SAFE lever)
    # if it doesn't back up far enough; raise escape_speed only if you must (faster = a harder blind bump).
    escape_ms = _envi("ROBOT_EXPLORE_ESCAPE_MS", 1500)
    escape_speed = max(1, _envi("ROBOT_EXPLORE_ESCAPE_SPEED", 8))
    # If the front stays blocked after this many CONSECUTIVE escapes, STOP escaping and end the explore -- a
    # wedged corner (or a front sensor STARVED to a false-low read under a power sag) would otherwise grind the
    # whole iteration budget escaping in place. The reverse also lengthens on each repeat (the SAFE lever).
    escape_max_tries = max(1, _envi("ROBOT_EXPLORE_ESCAPE_MAX_TRIES", 3))
    narrate = os.environ.get("ROBOT_EXPLORE_NARRATE", "1") == "1"

    face.set_expression("exploring")
    log.info("EXPLORE (agentic) start: backend=%s max_iters=%d max_secs=%.0f speed=%d",
             "remote" if remote else "local", max_iters, max_secs, speed)
    deadline = time.monotonic() + max_secs
    seen, reason, moves, escape_tries = [], "iteration cap", 0, 0
    for i in range(max_iters):
        if aborted():
            reason = "aborted (stop)"
            break
        if time.monotonic() >= deadline:
            reason = "time cap"
            break
        # Boxed in? Run a deterministic back-up-and-turn BEFORE the slow VLM step (the VLM only knows
        # FORWARD/STOP and would just oscillate at the wall). Sequence, matching the intended maneuver:
        #   1. reverse STRAIGHT for escape_ms -- BLIND rear (no beam) so a gentle crawl, but LONG enough to
        #      actually pull back off the obstacle (this is the fix for the old 500ms nudge that the 1.5s
        #      forward out-ran, so it only ever slowly rotated in place).
        #   2-3. RE-READ the left + right beams from the NEW backed-up position (fresher than reading before we
        #      reversed -- the car has moved, so the "which side is clearer" decision should use where it IS now).
        #   4-5. turn toward whichever side has MORE room and drive that way (still front-gated).
        #   6. `continue` -> the loop re-checks front at the top and repeats from step 1 if it's still boxed.
        # A failed front read (-1.0) is NOT treated as close.
        dist = driver.distances()
        front = dist.get("front")
        if isinstance(front, (int, float)) and 0 <= front <= escape_cm:
            escape_tries += 1
            if escape_tries > escape_max_tries:
                # Front is STILL blocked after escape_max_tries in a row -- a wedged corner, or a front sensor
                # starved to a false-low read (power sag). Bail instead of grinding the rest of the budget.
                log.warning("EXPLORE iter %d: front=%.0fcm STILL blocked after %d escapes -> stopping (stuck)",
                            i, front, escape_max_tries)
                face.set_expression("confused")
                say("I seem to be stuck, so I'll stop exploring here.")
                reason = "stuck (front blocked after %d escapes)" % escape_max_tries
                break
            rev_ms = int(escape_ms * (1.0 + 0.5 * (escape_tries - 1)))       # escalate the DURATION each repeat (safe lever)
            log.info("EXPLORE iter %d: front=%.0fcm <= %.0fcm (boxed in, escape %d/%d) -> reverse %dms (spd=%d), then turn to the open side",
                     i, front, escape_cm, escape_tries, escape_max_tries, rev_ms, escape_speed)
            driver.stop()
            driver.command("REVERSE", "CENTER", escape_speed, rev_ms)        # 1. back straight (blind rear -> crawl)
            _sleep(rev_ms / 1000.0 + 0.1, aborted)
            if aborted():
                reason = "aborted (stop)"
                break
            d2 = driver.distances()                                          # 2-3. fresh side reads AFTER backing up
            left, right = _clear(d2.get("left")), _clear(d2.get("right"))
            open_side = "LEFT" if left >= right else "RIGHT"                  # 4. more room -> the turn direction
            log.info("EXPLORE iter %d: after reverse -> L=%.0fcm R=%.0fcm, turning %s and going", i, left, right, open_side)
            driver.command("FORWARD", open_side, escape_speed, step_ms)      # 5. drive the open way (front-gated)
            moves += 1
            _sleep(step_ms / 1000.0 + 0.1, aborted)
            continue                                                          # 6. loop repeats if still boxed
        escape_tries = 0     # reached here => front is CLEAR (not boxed) -> reset the consecutive-escape counter
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
        if step.get("done") and i >= min_iters:
            reason = "model satisfied"
            break
        if step.get("done"):
            # Weak LOCAL VLM: it called "done" before driving (this is why explore sometimes never moved).
            # Ignore it until min_iters, and make sure THIS step drives -- coerce a STOP/blank into a FORWARD
            # crawl (still ultrasonic-gated in the control thread).
            log.info("EXPLORE iter %d: model said DONE at %d/%d min steps -- overriding to keep moving",
                     i, i, min_iters)
            if step.get("move") in (None, "", "STOP"):
                step["move"] = "FORWARD"
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
        # Ease off near obstacles: a full-length step at roam speed can overshoot from "clear" to nose-in
        # before the gate reacts (esp. an angled wall the ultrasonic beam misses until very close). Halve the
        # step when the front beam already shows we're approaching something (within 2x the escape margin).
        this_step_ms = step_ms
        if isinstance(front, (int, float)) and 0 <= front < 2 * escape_cm:
            this_step_ms = max(400, step_ms // 2)
        log.info("EXPLORE iter %d: FORWARD %s (%dms) (%s)", i, steer, this_step_ms, step.get("reason", "")[:60])
        driver.command("FORWARD", steer, speed, this_step_ms)   # sensor-gated in the 20Hz control thread
        moves += 1
        _sleep(this_step_ms / 1000.0 + 0.1, aborted)

    driver.stop()
    face.set_expression("talking")
    if seen:
        say("I had a look around. I can see " + ", ".join(seen[:6]) + ".")
    else:
        say("I had a look around but I'm not sure what I'm seeing.")
    face.set_expression("idle")
    log.info("EXPLORE (agentic) done (%s) -- drove %d step(s), objects=%s", reason, moves, seen)
    return seen
