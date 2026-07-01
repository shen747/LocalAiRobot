#!/usr/bin/env python3
# brain/diagnostics.py -- pre-first-run HARDWARE WIRING SELF-TEST.
#
# The OPPOSITE of dry-run: dry-run fakes every device; this forces LIVE hardware and verifies each
# connection against the build docs, printing a green [v] / red [x] per item and, at the end, a summary
# that names exactly what's wrong and which doc/phase fixes it. Run it once the car is built, wired,
# powered (LiPo + Pi) and you're SSH'd in. Launch via ./scripts/run_diagnostics.sh (it forces live).
#
# REALITY: the Pi has no electrical feedback from the motor, servo or speaker, so those are verified by
# the operator watching/listening -- the script performs the action then asks "did it work? [y/n]"
# (interactive by default; --yes auto-confirms for quick re-runs). Camera live-feed isn't viewable over
# headless SSH, so the camera checks save a still + a short clip to inspect instead.
#
# SAFETY: all harmless checks run BEFORE the driver is ever built (building it arms the ESC). The motor
# spin is last, behind a hard "wheels off the ground?" gate; under --yes it is SKIPPED unless --wheels-off
# is also given, so an unattended run can never spin wheels on the ground.

import argparse
import os
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUTDIR = os.path.expanduser("~/.localairobot/diagnostics")

# ---- doc fix-pointers (repo-relative) ----
DOC_PWM = "docs/wiring/pwm-esc-servo-wiring.md"
DOC_SENS = "docs/wiring/ultrasonic-sensors-wiring.md"
DOC_ASM = "docs/assembly.md"
DOC_WAKE = "docs/opnewakeword/openwake-word.md"

_TTY = sys.stdout.isatty()


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def green(s): return _c("32", s)
def red(s): return _c("31", s)
def dim(s): return _c("2", s)
def bold(s): return _c("1", s)


class Diag:
    def __init__(self, args):
        self.args = args
        self.passed, self.failed, self.skipped = [], [], []
        # Confirm prompts auto-skip after this many seconds so a check can never block the run.
        try:
            self.timeout = float(os.environ.get("ROBOT_DIAG_TIMEOUT", "8") or 8)
        except ValueError:
            self.timeout = 8.0

    # ---- result emitters (also print a live line) ----
    def ok(self, label, detail=""):
        print(f"[{green('v')}] {label}" + (f"  {dim(detail)}" if detail else ""))
        self.passed.append(label)

    def bad(self, label, reason, doc):
        print(f"[{red('x')}] {label}  {red(reason)}")
        self.failed.append((label, reason, doc))

    def skip(self, label, reason=""):
        print(f"[{dim('-')}] {label}" + (f"  {dim(reason)}" if reason else ""))
        self.skipped.append(label)

    # ---- prompts ----
    def confirm(self, question, timeout=None):
        """Ask y/n. Returns True / False, or None if there's no answer within the timeout (so a check
        NEVER blocks the run). Auto-True under --yes. timeout defaults to self.timeout."""
        if self.args.yes:
            print(dim(f"    (--yes) {question} -> assumed yes"))
            return True
        to = self.timeout if timeout is None else timeout
        sys.stdout.write(f"    {bold(question)} [y/N] {dim(f'(auto-skip in {int(to)}s)')} ")
        sys.stdout.flush()
        try:
            ready, _, _ = select.select([sys.stdin], [], [], to)
        except Exception:
            ready = [sys.stdin]      # stdin not selectable (rare) -> fall back to a blocking read
        if not ready:
            print(dim("-> no answer, moving on"))
            return None
        try:
            return sys.stdin.readline().strip().lower() in ("y", "yes")
        except Exception:
            return None

    def confirm_step(self, label, question, fail_reason, doc):
        """Confirm an actuation and emit ok / bad / skip (skip = unconfirmed within the timeout)."""
        res = self.confirm(question)
        if res is True:
            self.ok(label)
        elif res is False:
            self.bad(label, fail_reason, doc)
        else:
            self.skip(label, f"unconfirmed (no answer in {int(self.timeout)}s)")

    def motors_allowed(self):
        """Decide whether the motor/servo spin may run. Never auto-spins under --yes, and the safety
        prompt is TIMEOUT-BOUNDED so an unanswered run can never hang here (no answer -> skip, safe)."""
        if self.args.no_motors:
            return False, "skipped (--no-motors)"
        if self.args.wheels_off:
            return True, ""
        if self.args.yes:
            return False, "skipped (--yes needs --wheels-off to spin the motor unattended)"
        print(f"\n  {bold('SAFETY')}: the motor test SPINS THE WHEELS.")
        res = self.confirm("Are the wheels OFF the ground (or the motor unplugged)?",
                           timeout=max(self.timeout, 15.0))
        if res is True:
            return True, ""
        if res is False:
            return False, "skipped (wheels-off declined)"
        return False, "skipped (no answer -- pass --wheels-off to run the motor test unattended)"

    # ===================== checks (harmless; no driver) =====================
    def check_pca9685(self):
        label = "ESC / PWM board present (PCA9685 @ I2C 0x40)"
        try:
            out = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return self.bad(label, "i2cdetect not found -- `sudo apt install i2c-tools`", DOC_ASM)
        except Exception as e:
            return self.bad(label, f"i2cdetect failed: {e}", DOC_PWM)
        if out.returncode != 0:
            return self.bad(label, (out.stderr.strip() or f"i2cdetect exit {out.returncode}; is I2C enabled?"), DOC_PWM)
        for line in out.stdout.splitlines():
            label_col, sep, cells = line.partition(":")
            if sep and "40" in cells.split():
                return self.ok(label, "found 0x40 on bus 1")
        return self.bad(label, "0x40 NOT on the bus -- check VCC(3.3V)/SDA/SCL/GND and that I2C is enabled", DOC_PWM)

    def check_sensors(self):
        import warnings
        try:
            from gpiozero import DistanceSensor
        except Exception as e:
            return self.bad("Ultrasonic sensors (gpiozero)", f"import failed: {e}", DOC_SENS)
        # Silence gpiozero's software-PWM spam; the no-echo warning we CAPTURE per sensor (below) to tell a
        # truly unwired sensor from one just aimed at open space.
        warnings.filterwarnings("ignore", message=".*PWMSoftwareFallback.*")
        pins = {
            "front": (os.environ.get("ROBOT_TRIG_PIN", "23"), os.environ.get("ROBOT_ECHO_PIN", "24")),
            "left": (os.environ.get("ROBOT_LEFT_TRIG_PIN", "5"), os.environ.get("ROBOT_LEFT_ECHO_PIN", "6")),
            "right": (os.environ.get("ROBOT_RIGHT_TRIG_PIN", "20"), os.environ.get("ROBOT_RIGHT_ECHO_PIN", "21")),
        }
        for name, (tp, ep) in pins.items():
            label = f"Ultrasonic sensor {name.upper()} (TRIG {tp} / ECHO {ep})"
            s = None
            try:
                # partial=True -> .distance returns immediately (no multi-second queue-fill block on a
                # dead/no-echo sensor); a small queue keeps it snappy even on older gpiozero (fallback).
                print(dim(f"    measuring {name.upper()} -- wave a hand in front of it..."))
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    # partial=True -> .distance returns immediately (no multi-second queue-fill block on a
                    # dead/no-echo sensor); a small queue keeps it snappy on older gpiozero (fallback).
                    try:
                        s = DistanceSensor(echo=int(ep), trigger=int(tp), max_distance=4.0, queue_len=3, partial=True)
                    except TypeError:
                        s = DistanceSensor(echo=int(ep), trigger=int(tp), max_distance=4.0, queue_len=3)
                    time.sleep(0.5)                   # let one ping land before sampling
                    hi = lo = s.distance * 100.0
                    # A sensor already reporting NO ECHO is unwired -- don't wait the full wave window for
                    # it (fails in ~0.5s instead of ~3s), so an unplugged array can't drag the run out.
                    early_dead = any("echo" in str(w.message).lower() for w in caught)
                    t_end = time.time() + (0.0 if (self.args.yes or early_dead) else 2.5)
                    while time.time() < t_end:
                        d = s.distance * 100.0
                        hi, lo = max(hi, d), min(lo, d)
                        time.sleep(0.1)
                    cur = s.distance * 100.0
                    no_echo = any("echo" in str(w.message).lower() for w in caught)
                if not self.args.yes and hi - lo > 5.0:
                    self.ok(label, f"responded: {hi:.0f} -> {lo:.0f} cm")
                elif no_echo:
                    self.bad(label, "NO ECHO received -- sensor not responding: check ECHO/TRIG pins & 3.3V wiring", DOC_SENS)
                elif self.args.yes:
                    self.ok(label, f"reading {cur:.0f} cm (response not tested under --yes)")
                elif lo >= 395.0:
                    self.bad(label, "reads ~400cm (max) & nothing changed -- wave closer, or check aim/wiring", DOC_SENS)
                else:
                    self.bad(label, f"steady {lo:.0f}cm, didn't change when waved -- wave during the window, or check wiring", DOC_SENS)
            except Exception as e:
                self.bad(label, f"read failed: {e}", DOC_SENS)
            finally:
                if s is not None:
                    # Stop gpiozero's background queue thread FIRST, then close -- close() alone can race
                    # that thread and hang/traceback. MUST free the GPIO pin for the driver to re-open later.
                    try:
                        q = getattr(s, "_queue", None)
                        if q is not None:
                            q.stop()
                    except Exception:
                        pass
                    try:
                        s.close()
                    except Exception:
                        pass

    def check_speaker(self):
        label = 'Speaker output (TTS greeting)'
        if os.environ.get("ROBOT_SOUNDS_ENABLE", "1") == "0":
            return self.skip(label, "ROBOT_SOUNDS_ENABLE=0")
        piper = os.path.expanduser(os.environ.get("ROBOT_PIPER_BIN", "~/piper/piper/piper"))
        voice = os.path.expanduser(os.environ.get("ROBOT_PIPER_VOICE", "~/piper/en_US-lessac-medium.onnx"))
        text = ("Hello, I am Jarvis, your friendly robot car, powered by AI and created by Ramesh.")
        wav = os.path.join(OUTDIR, "diag_speaker.wav")
        try:
            syn = subprocess.run([piper, "-m", voice, "--output_file", wav],
                                 input=text.encode("utf-8"), timeout=40,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if syn.returncode != 0:
                return self.bad(label, f"piper failed: {syn.stderr.decode('utf-8', 'replace').strip()[-120:]}", DOC_ASM)
            pb = subprocess.run(["aplay", wav], timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if pb.returncode != 0:
                return self.bad(label, f"aplay failed: {pb.stderr.strip()[-120:]} -- check ~/.asoundrc / `aplay -l`", DOC_ASM)
        except FileNotFoundError as e:
            return self.bad(label, f"missing binary: {e} -- run ./scripts/install_stack.sh", DOC_ASM)
        except Exception as e:
            return self.bad(label, f"{e}", DOC_ASM)
        self.confirm_step(label, "Did you HEAR the greeting?",
                          "not heard -- set the USB speaker as the ALSA default (./scripts/setup_audio.sh)", DOC_ASM)

    def check_camera(self):
        if os.environ.get("ROBOT_CAMERA_ENABLE", "1") == "0":
            for la in ("Camera picture", "Camera video clip", "Camera live feed"):
                self.skip(la, "ROBOT_CAMERA_ENABLE=0")
            return
        # Use the libcamera CLI tools (subprocess + timeout) so a detected-but-undelivering camera (bad CSI
        # ribbon -> "Failed to queue buffer") can't HANG the run -- it times out and fails cleanly.
        for label, cmd, fname, t in (
            ("Camera picture (still)", ["rpicam-jpeg", "-n", "-t", "2000", "-o"], "diag_picture.jpg", 10),
            ("Camera video clip (3 s)", ["rpicam-vid", "-n", "-t", "3000", "-o"], "diag_video.h264", 12),
        ):
            path = os.path.join(OUTDIR, fname)
            try:
                r = subprocess.run(cmd + [path], timeout=t,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
                    self.ok(label, f"{os.path.getsize(path)} bytes -> {path}")
                else:
                    self.bad(label, f"{cmd[0]} failed: {(r.stderr or '').strip()[-160:]}", DOC_ASM)
            except subprocess.TimeoutExpired:
                self.bad(label, f"{cmd[0]} timed out -- camera detected but no frames: reseat the CSI ribbon /"
                                " 22->15 adapter (contacts the right way, latches closed)", DOC_ASM)
            except FileNotFoundError:
                self.bad(label, f"{cmd[0]} not found (libcamera apps missing)", DOC_ASM)
            except Exception as e:
                self.bad(label, f"{e}", DOC_ASM)
        self.skip("Camera live feed", "not viewable over headless SSH; the saved clip covers the pipeline")

    def check_mic(self):
        label = "Microphone input (say 'hey jarvis')"
        mic = os.environ.get("ROBOT_MIC", "plughw:1,0")
        wav = os.path.join(OUTDIR, "diag_mic.wav")
        if not self.args.yes:
            print(f"    Recording 3 s from {dim(mic)} -- say {bold('hey jarvis')} now...")
        try:
            rec = subprocess.run(["arecord", "-D", mic, "-d", "3", "-f", "S16_LE", "-r", "16000", "-c", "1", wav],
                                 timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if rec.returncode != 0:
                return self.bad(label, f"arecord failed on ROBOT_MIC={mic!r}: {rec.stderr.strip()[-120:]}", DOC_ASM)
        except FileNotFoundError:
            return self.bad(label, "arecord not found (alsa-utils missing)", DOC_ASM)
        except Exception as e:
            return self.bad(label, f"{e}", DOC_ASM)
        # measure level + (best effort) score the wake word over the clip
        try:
            import wave
            import numpy as np
            wf = wave.open(wav, "rb")
            raw = wf.readframes(wf.getnframes())
            wf.close()
            data = np.frombuffer(raw, dtype=np.int16)
            peak_pct = (100.0 * float(np.max(np.abs(data))) / 32767.0) if data.size else 0.0
        except Exception as e:
            return self.bad(label, f"could not read recording: {e}", DOC_ASM)
        wake_note = self._score_wake(data)
        if peak_pct < 2.0:
            self.bad(label, f"almost silent (peak {peak_pct:.0f}%) -- wrong ROBOT_MIC or mic gain too low", DOC_ASM)
        else:
            self.ok(label, f"level peak {peak_pct:.0f}%{wake_note}")

    def _score_wake(self, data):
        """Best-effort: run the wake model over the recorded clip; return ' | wake ...' or ''."""
        if data is None or os.environ.get("ROBOT_WAKEWORD_ENABLE", "1") == "0":
            return ""
        try:
            from wakeword import WakeWord
            ww = WakeWord({})
            if not getattr(ww, "available", False) or getattr(ww, "_model", None) is None:
                return ""
            best = 0.0
            for i in range(0, len(data) - 1280, 1280):
                scores = ww._model.predict(data[i:i + 1280])
                if scores:
                    best = max(best, max(scores.values()))
            hit = "DETECTED" if best >= ww.threshold else "not detected"
            return f" | wake '{ww.label}' {hit} (score {best:.2f})"
        except Exception:
            return ""

    def check_face(self):
        label = "Face panel / monitor"
        if os.environ.get("ROBOT_FACE_ENABLE", "1") == "0":
            return self.skip(label, "ROBOT_FACE_ENABLE=0")
        try:
            from face import Face
            fc = Face()
            backend = type(fc.backend).__name__ if fc.backend is not None else "None"
        except Exception as e:
            return self.bad(label, f"face init failed: {e}", DOC_ASM)
        try:
            if backend == "_TuringBackend":
                detail = f"USB-C serial panel (turing, port {os.environ.get('ROBOT_FACE_PORT', 'AUTO')})"
            elif backend == "_FramebufferBackend":
                detail = "DSI/HDMI framebuffer (/dev/fb0)"
            else:
                self.bad(label, "no panel found (fell through to simulated) -- data USB-C cable? in `dialout`? /dev/ttyACM0?", DOC_ASM)
                return
            for state in ("happy", "thinking", "talking", "idle"):
                fc.set_expression(state)
                time.sleep(0.6)
            res = self.confirm(f"Do you SEE the face on the {detail.split('(')[0].strip()}?")
            if res is True:
                self.ok(label, detail)
            elif res is False:
                self.bad(label, f"panel detected ({detail}) but nothing shown -- check the revision (ROBOT_FACE_REVISION)", DOC_ASM)
            else:
                self.skip(label, f"{detail}; unconfirmed (no answer in {int(self.timeout)}s)")
        finally:
            try:
                fc.close()
            except Exception:
                pass

    # ===================== checks (motor/servo; builds the driver) =====================
    def check_motors_and_servo(self):
        labels = ["ESC arm (neutral)", "Motor FORWARD spin", "Motor REVERSE spin",
                  "Servo steer RIGHT", "Servo steer LEFT", "Servo steer CENTER"]
        allowed, why = self.motors_allowed()
        if not allowed:
            for la in labels:
                self.skip(la, why)
            return
        # Fully disable the forward obstacle gate for the spin test: a benchtop wall -- OR an UNPLUGGED
        # front sensor reading an error (-1) -- must not block the motor. -1000 => the gate never trips.
        os.environ["ROBOT_OBSTACLE_CM"] = "-1000"
        try:
            import driver
        except Exception as e:
            for la in labels:
                self.bad(la, f"driver import failed: {e}", DOC_PWM)
            return
        print(dim("    Building the driver (the ESC should beep its arming tones)..."))
        d = None
        try:
            d = driver.RobotDriver()
            if isinstance(d.kit, driver.FakeServoKit):
                for la in labels:
                    self.bad(la, "PCA9685 not driving (faked) -- fix the I2C check above first", DOC_PWM)
                return
            esc_ch, steer_ch = d.esc_ch, d.steer_ch
            print(dim(f"    ESC on ch{esc_ch}, steering servo on ch{steer_ch} (ROBOT_ESC_CHANNEL / "
                      f"ROBOT_STEER_CHANNEL). BOTH need the LiPo connected + CHARGED -- the servo is powered by "
                      f"the ESC's BEC, so no/low battery = no motor AND no steering."))
            # ESC arm: the driver holds NEUTRAL (throttle ROBOT_ESC_NEUTRAL) on the ESC channel -- with the
            # ESC powered ON, holding that is what arms it (cell-count tones, then a long "ready" beep). If
            # it doesn't beep, power-cycle the ESC now WHILE this neutral is held.
            print(dim(f"    Holding NEUTRAL on ch{esc_ch} (throttle {d.neutral:+.2f}) with the ESC powered ON"
                      " -- listen for cell tones + a long 'ready' beep. No beep? Power-cycle the ESC now."))
            ares = self.confirm("ESC armed? (heard the long 'ready' beep)", timeout=15)
            if ares is True:
                self.ok("ESC arm (neutral)")
            elif ares is False:
                self.bad("ESC arm (neutral)", f"won't arm -- power-cycle it while neutral is held; check the "
                         f"ch{esc_ch} signal lead, a CHARGED LiPo, and ROBOT_ESC_NEUTRAL (some ESCs need ~0.25)", DOC_PWM)
            else:
                self.skip("ESC arm (neutral)", "unconfirmed (no answer in 15s)")
            # forward / reverse (direction-specific fixes: if FORWARD passed, a REVERSE fail is the ESC mode)
            motor_fix = {
                "FORWARD": f"no spin -- check the ESC signal on ch{esc_ch}, the ESC<->motor leads, and a "
                           f"CHARGED LiPo via the kill switch (a flat pack just beeps and won't drive)",
                "REVERSE": ("no reverse -- if FORWARD worked this is the ESC's mode, not wiring: it's in "
                            "Forward/Brake (no reverse) or wants a double-tap. Switch it to Forward/Reverse "
                            "via the ESC's jumper / programming (see its manual)"),
            }
            for la, drive in (("Motor FORWARD spin", "FORWARD"), ("Motor REVERSE spin", "REVERSE")):
                d.command(drive, "CENTER", 30, 1500)
                time.sleep(2.0)
                self.confirm_step(la, f"Did the wheels spin {drive}?", motor_fix[drive], DOC_PWM)
            # steering
            for la, steer in (("Servo steer RIGHT", "RIGHT"), ("Servo steer LEFT", "LEFT"), ("Servo steer CENTER", "CENTER")):
                d.command("STOP", steer, 0, 500)
                time.sleep(1.2)
                self.confirm_step(la, f"Did the front wheels steer {steer}?",
                                  f"no/!wrong steer -- check the servo on ch{steer_ch}, that the LiPo is ON "
                                  f"(its BEC powers the servo), and the ROBOT_STEER_* angles", DOC_PWM)
        except Exception as e:
            for la in labels:
                if la not in self.passed:
                    self.bad(la, f"{e}", DOC_PWM)
        finally:
            if d is not None:
                try:
                    d.close()    # neutral throttle + center steer on the way out
                except Exception:
                    pass

    # ===================== orchestration =====================
    def run(self):
        os.makedirs(OUTDIR, exist_ok=True)
        print(bold("\nLocalAiRobot hardware diagnostics") +
              dim("  (live wiring self-test -- artifacts in ~/.localairobot/diagnostics)\n"))
        if not self.args.yes:
            print(dim("  Interactive: each action is performed, then you confirm it worked. Ctrl-C to abort.\n"))
        # harmless checks first (NO driver -> ESC not armed yet)
        self.check_pca9685()
        self.check_sensors()
        self.check_speaker()
        self.check_camera()
        self.check_mic()
        self.check_face()
        # motor/servo last, behind the safety gate (builds the driver -> arms the ESC)
        self.check_motors_and_servo()
        return self.report()

    def report(self):
        n_p, n_f, n_s = len(self.passed), len(self.failed), len(self.skipped)
        print("\n" + bold("Summary: ") +
              f"{green(str(n_p) + ' passed')}  {red(str(n_f) + ' failed') if n_f else dim('0 failed')}  {dim(str(n_s) + ' skipped')}")
        if self.failed:
            print(bold("\nFix these:"))
            for label, reason, doc in self.failed:
                print(f"  {red('x')} {label}: {reason}")
                print(dim(f"      -> see {doc}"))
        else:
            print(green("\nAll connected checks passed. ") + dim("You're ready for Phase 8 calibration."))
        return 1 if self.failed else 0


def main():
    ap = argparse.ArgumentParser(description="LocalAiRobot live hardware wiring self-test.")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="non-interactive: auto-confirm 'did it work?' prompts (motor spin still needs --wheels-off)")
    ap.add_argument("--wheels-off", action="store_true",
                    help="pre-confirm the motor safety gate (wheels are off the ground) for unattended runs")
    ap.add_argument("--no-motors", action="store_true", help="skip the motor/servo spin tests entirely")
    args = ap.parse_args()

    # This is a LIVE test; the wrapper forces these, but guard anyway so `python diagnostics.py` is honest.
    if os.environ.get("ROBOT_DRY_RUN") == "1" or os.environ.get("ROBOT_SIMULATE") == "1":
        print(red("ROBOT_DRY_RUN=1 fakes all hardware -- run via ./scripts/run_diagnostics.sh (forces live)."))
        return 2
    try:
        return Diag(args).run()
    except KeyboardInterrupt:
        print("\naborted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
