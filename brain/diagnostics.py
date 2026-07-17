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
import re
import select
import shutil
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
        # Confirm prompts auto-skip after this many seconds so a check can never block the run. 12s gives
        # comfortable time to watch the wheels / listen / answer, while still never hanging the run.
        try:
            self.timeout = float(os.environ.get("ROBOT_DIAG_TIMEOUT", "12") or 12)
        except ValueError:
            self.timeout = 12.0

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
        """Ask y/n. Returns True / False, or None if there's no answer within the timeout (so on a
        select()-able stdin -- the normal tty/pipe case -- a check never blocks the run; if select() itself
        fails the fallback is one blocking readline(), guarded by the surrounding try). Auto-True under
        --yes. timeout defaults to self.timeout."""
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
        prompt is timeout-bounded (for a select()-able stdin) so an unanswered run does not hang here
        (no answer -> skip, safe)."""
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
    def check_power(self):
        """Battery brownout guard (Pi only). A UBEC that can't hold 5V under load trips UNDERVOLTAGE -> USB
        devices (mic/speaker/face) drop off mid-run and the Pi can brown out (frozen SSH). This reads
        get_throttled AND (Pi 5) the PMIC input voltage (EXT5V_V) so a marginal rail is caught BEFORE the
        first live run, not discovered by a mid-word cut-out. Bench PSU: 0x0 @ ~5.1V. See docs/assembly.md."""
        label = "Power rail (5V input holds under load)"
        if not shutil.which("vcgencmd"):
            return self.skip(label, "no vcgencmd (not a Pi) -- power telemetry unavailable")
        try:
            t = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=10)
            val = int(t.stdout.strip().split("=")[1], 16)
        except Exception as e:
            return self.skip(label, f"couldn't read get_throttled: {e}")
        v = None   # PMIC input voltage (Pi 5 only; Pi 4/older has no ADC -> fall back to the throttle bits)
        try:
            a = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True, timeout=10)
            m = re.search(r"EXT5V_V\s+volt\(\d+\)=([\d.]+)V", a.stdout)
            v = float(m.group(1)) if m else None
        except Exception:
            v = None
        vstr = f"{v:.2f}V input" if v is not None else "voltage n/a"
        now_uv = bool(val & (0x1 | 0x4))        # undervoltage / throttled NOW
        ever_uv = bool(val & (0x10000 | 0x40000))  # ... or at any point since boot
        fix = ("the UBEC can't hold 5V under load: raise it toward 5.1-5.2V (<=5.25V on USB-C), use short "
               "thick leads, or feed via a charged UPS HAT")
        if now_uv or (v is not None and v < 4.80):
            return self.bad(label, f"UNDERVOLTAGE/throttled NOW ({vstr}, get_throttled=0x{val:x}) -- {fix}", DOC_ASM)
        if ever_uv or (v is not None and v < 4.90):
            return self.bad(label, f"5V rail sagged this boot ({vstr}, get_throttled=0x{val:x}) -- marginal "
                            f"under load, will drop USB devices mid-run: {fix}", DOC_ASM)
        return self.ok(label, f"healthy ({vstr}, get_throttled=0x{val:x})")

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

    def _check_tof_sensors(self):
        """VL53L1X ToF variant of check_sensors (ROBOT_SENSOR_TYPE=vl53l1x): assign the 3 addresses, then
        read each beam. A plausible reading = PASS; a negative (I2C error) or no-target = a flagged FAIL."""
        try:
            import hal
            import vl53l1x_sensor as tof
        except Exception as e:
            return self.bad("VL53L1X ToF sensors (import)", f"import failed: {e}", DOC_SENS)

        def _addr(key, default):
            v = os.environ.get(key, "").strip()
            try:
                return int(v, 0) if v else default
            except ValueError:
                return default
        plan = [("front", os.environ.get("ROBOT_TOF_XSHUT_FRONT", ""), _addr("ROBOT_TOF_ADDR_FRONT", 0x2A)),
                ("left",  os.environ.get("ROBOT_TOF_XSHUT_LEFT", ""),  _addr("ROBOT_TOF_ADDR_LEFT", 0x2B)),
                ("right", os.environ.get("ROBOT_TOF_XSHUT_RIGHT", ""), _addr("ROBOT_TOF_ADDR_RIGHT", 0x29))]
        sensors, xshuts, i2c = {}, None, None
        try:
            i2c = tof.make_i2c()
            xshuts = tof.assign_addresses(i2c, [(int(p), a) for (_n, p, a) in plan],
                                          hal.current().make_digital_output)
            for name, _p, addr in plan:
                sensors[name] = tof.VL53L1XSensor(i2c, addr, 4.0)
            time.sleep(0.3)                       # let a sample or two land
            for name, _p, addr in plan:
                label = f"VL53L1X {name.upper()} @0x{addr:02x}"
                reads = []
                for _ in range(5):
                    reads.append(sensors[name].distance * 100.0)
                    time.sleep(0.06)
                plausible = [r for r in reads if 1.0 <= r < 395.0]
                if plausible:
                    self.ok(label, f"reading {plausible[-1]:.0f} cm")
                elif any(r < 0 for r in reads):
                    self.bad(label, "I2C read error -- check wiring/address/ROBOT_I2C_BUS", DOC_SENS)
                else:
                    self.bad(label, "no target / out of range -- aim it at a wall", DOC_SENS)
        except Exception as e:
            self.bad("VL53L1X ToF sensors", f"setup failed: {e} -- check I2C wiring/bus + the "
                     "ROBOT_TOF_XSHUT_* pins", DOC_SENS)
        finally:
            for s in sensors.values():
                try:
                    s.close()
                except Exception:
                    pass
            for x in (xshuts or []):
                try:
                    x.close()
                except Exception:
                    pass
            if i2c is not None:                  # P2-5: release the bus LAST (after the reader threads join)
                try:
                    i2c.deinit()                 # busio.I2C / ExtendedI2C expose deinit(), not close()
                except Exception:
                    pass

    def _board_label(self):
        """(board name, pin scheme) for the pin map -- from the HAL, else a safe fallback."""
        try:
            import hal
            b = hal.current()
            return b.name, b.pin_scheme
        except Exception:
            return (os.environ.get("ROBOT_BOARD", "?") or "?"), "BCM"

    def _resolved_hcsr04_pins(self):
        """The TRIG/ECHO pins the DRIVER will actually use (env > board default_pins() > Pi hard default).
        P2-7: delegates to hal.resolve_hcsr04_pins() -- the SINGLE source of truth shared with driver.py, so
        diagnostics tests + prints the exact pins the robot drives."""
        import hal
        return hal.resolve_hcsr04_pins()

    def _sensor_pinmap(self):
        """Print the pin/address map for the CONFIGURED sensor type + board, before the sensor checks run --
        so a Jetson (BOARD numbering) or a ToF build can see exactly which pins/addresses to wire. Info only."""
        stype = os.environ.get("ROBOT_SENSOR_TYPE", "hcsr04").strip().lower() or "hcsr04"
        bname, scheme = self._board_label()
        print(bold(f"Pin map -- board: {bname} ({scheme} numbering)  ·  sensor type: {stype}"))
        if stype == "vl53l1x":
            bus = os.environ.get("ROBOT_I2C_BUS", "").strip() or "default (Pi bus 1)"
            def _a(key, d):
                v = os.environ.get(key, "").strip()
                try:
                    return int(v, 0) if v else d
                except ValueError:
                    return d
            print(dim(f"    VL53L1X laser ToF on I2C bus {bus} -- XSHUT pins + assigned addresses:"))
            for name, xk, ak, ad in (("front", "ROBOT_TOF_XSHUT_FRONT", "ROBOT_TOF_ADDR_FRONT", 0x2A),
                                     ("left",  "ROBOT_TOF_XSHUT_LEFT",  "ROBOT_TOF_ADDR_LEFT",  0x2B),
                                     ("right", "ROBOT_TOF_XSHUT_RIGHT", "ROBOT_TOF_ADDR_RIGHT", 0x29)):
                xshut = os.environ.get(xk, "").strip() or "?"
                print(dim(f"      {name:<6} XSHUT={xshut}   addr=0x{_a(ak, ad):02x}"))
        else:
            print(dim(f"    HC-SR04P ultrasonic -- TRIG/ECHO ({scheme} pins):"))
            for name, (tp, ep) in self._resolved_hcsr04_pins().items():
                print(dim(f"      {name:<6} TRIG={tp}   ECHO={ep}"))

    def check_sensors(self):
        self._sensor_pinmap()              # show the board + sensor-type pin/address map first
        if os.environ.get("ROBOT_SENSOR_TYPE", "hcsr04").strip().lower() == "vl53l1x":
            return self._check_tof_sensors()   # ROBOT_SENSOR_TYPE=vl53l1x -> I2C ToF path
        import warnings
        try:
            from gpiozero import DistanceSensor
        except Exception as e:
            return self.bad("Ultrasonic sensors (gpiozero)", f"import failed: {e}", DOC_SENS)
        # Silence gpiozero's software-PWM spam; the no-echo warning we CAPTURE per sensor (below) to tell a
        # truly unwired sensor from one just aimed at open space.
        warnings.filterwarnings("ignore", message=".*PWMSoftwareFallback.*")
        pins = self._resolved_hcsr04_pins()   # board-resolved (env > board default > Pi default); values are ints
        for name, (tp, ep) in pins.items():
            label = f"Ultrasonic sensor {name.upper()} (TRIG {tp} / ECHO {ep})"
            s = None
            try:
                # AUTOMATED: a valid reading = PASS -- no hand-waving needed. Only a sensor returning NO ECHO
                # (not responding at all) is a failure. partial=True -> .distance returns immediately (no
                # multi-second queue-fill block on a dead sensor); a small queue keeps older gpiozero snappy.
                print(dim(f"    measuring {name.upper()}..."))
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        s = DistanceSensor(echo=int(ep), trigger=int(tp), max_distance=4.0, queue_len=3, partial=True)
                    except TypeError:
                        s = DistanceSensor(echo=int(ep), trigger=int(tp), max_distance=4.0, queue_len=3)
                    time.sleep(0.5)                   # let a ping or two land before sampling
                    reads = [s.distance * 100.0]
                    for _ in range(4):                # ~0.3s of samples -- enough to read + catch a no-echo
                        time.sleep(0.08)
                        reads.append(s.distance * 100.0)
                # (the catch_warnings above just SUPPRESSES gpiozero's no-echo warning spam during the reads)
                # A WIRED, responding sensor returns a PLAUSIBLE distance (HC-SR04 range ~2..400 cm) at least
                # once across the window -- even if a far/soft target makes the odd ping miss. An UNCONNECTED
                # sensor reads garbage: ~0 cm (ECHO pin floating LOW) or max/no-echo (floating high / dead).
                # Require a plausible reading to PASS -- 0 cm is NOT a real reading (that was the bug where an
                # unplugged sensor passed). Aim it at a wall / your hand during the test, not open space.
                plausible = [r for r in reads if 2.0 <= r < 395.0]
                if plausible:
                    self.ok(label, f"reading {plausible[-1]:.0f} cm")
                elif any(r < 2.0 for r in reads):
                    self.bad(label, "reads ~0 cm -- ECHO pin floating/unconnected: check ECHO/TRIG + 3.3V wiring", DOC_SENS)
                else:
                    self.bad(label, "no echo / out of range -- not responding: check ECHO/TRIG + 3.3V wiring (aim it at a wall)", DOC_SENS)
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
        name = os.environ.get("ROBOT_NAME", "Jarvis").strip() or "Jarvis"
        text = (f"Hello, I am {name}, your friendly robot car, powered by A I.")
        wav = os.path.join(OUTDIR, "diag_speaker.wav")
        # 1) Synthesize with Piper -- this exercises the REAL voice pipeline. If Piper fails we STILL want
        #    to prove the speaker, so we fall back to a guaranteed ALSA test tone (the exact 'default' path
        #    scripts/setup_audio.sh sets up, same as `speaker-test -D default`).
        piper_ok, tts_note = False, ""
        try:
            syn = subprocess.run([piper, "-m", voice, "--output_file", wav], input=text.encode("utf-8"),
                                 timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            piper_ok = syn.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 0
            if not piper_ok:
                tts_note = f" -- Piper TTS failed: {syn.stderr.decode('utf-8', 'replace').strip()[-90:]}"
        except FileNotFoundError:
            tts_note = " -- Piper binary missing (run ./scripts/install_stack_pi5.sh)"
        except Exception as e:
            tts_note = f" -- Piper error: {e}"
        # 2) Play it, with a LOUD cue first so you know to listen RIGHT NOW (a big reason a greeting gets
        #    "missed"). aplay/speaker-test both use the ALSA default (~/.asoundrc) -- same as the robot voice.
        print(f"    {bold('>> PLAYING SOUND NOW -- listen to the speaker...')}")
        try:
            if piper_ok:
                pb = subprocess.run(["aplay", wav], timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                played = "greeting"
            else:
                pb = subprocess.run(["speaker-test", "-D", "default", "-t", "wav", "-l", "1"],
                                    timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                played = "test tone"
        except FileNotFoundError as e:
            return self.bad(label, f"missing binary: {e} -- run ./scripts/install_stack_pi5.sh", DOC_ASM)
        except Exception as e:
            return self.bad(label, f"{e}", DOC_ASM)
        if pb.returncode != 0:
            return self.bad(label, f"playback failed: {(pb.stderr or '').strip()[-120:]} -- check ~/.asoundrc "
                            "(run ./scripts/setup_audio.sh)", DOC_ASM)
        # 3) Only a human can confirm sound actually came out of the cone.
        res = self.confirm(f"Did you HEAR the {played}?{tts_note}")
        if res is False:
            self.bad(label, "not heard -- make the USB speaker the ALSA default (./scripts/setup_audio.sh)", DOC_ASM)
        elif res is None:
            self.skip(label, f"unconfirmed (no answer in {int(self.timeout)}s)")
        elif piper_ok:
            self.ok(label)
        else:
            self.bad(label, f"speaker OK (tone heard) but the VOICE needs Piper{tts_note} -- "
                     "re-run ./scripts/install_stack_pi5.sh", DOC_ASM)

    def check_camera(self):
        if os.environ.get("ROBOT_CAMERA_ENABLE", "1") == "0":
            for la in ("Camera picture", "Camera video clip", "Camera live feed"):
                self.skip(la, "ROBOT_CAMERA_ENABLE=0")
            return
        # USB webcam (ROBOT_CAMERA_BACKEND=v4l2)? The robot captures through OpenCV (cv2.VideoCapture), NOT
        # libcamera -- rpicam-jpeg/-vid don't drive UVC webcams and would falsely report "no cameras
        # available". Test the REAL robot path instead of the CSI tools below.
        if os.environ.get("ROBOT_CAMERA_BACKEND", "").strip().lower() == "v4l2":
            return self._check_camera_usb()
        # CSI camera: use the libcamera CLI tools (subprocess + timeout) so a detected-but-undelivering camera
        # (bad CSI ribbon -> "Failed to queue buffer") can't HANG the run -- it times out and fails cleanly.
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

    def _check_camera_usb(self):
        """USB webcam (ROBOT_CAMERA_BACKEND=v4l2): test the ACTUAL robot path -- camera.Camera() ->
        cv2.VideoCapture -- not rpicam (the libcamera CLIs don't drive UVC webcams). Pre-checks (cv2 present,
        /dev/videoN exists) so a failure names the cause instead of a bare 'no cameras available'."""
        import glob
        label = "Camera picture (USB webcam / v4l2)"
        src = (os.environ.get("ROBOT_CAMERA_SOURCE", "0") or "0").strip()
        vids = ", ".join(sorted(glob.glob("/dev/video*"))) or "none"
        # 1) OpenCV present? (installed by scripts/patches/pi5/3_camera_usb_webcam.sh, not the base install)
        try:
            import cv2  # noqa: F401
        except Exception as e:
            self.bad(label, f"OpenCV (cv2) not importable: {e} -- run "
                     "./scripts/patches/pi5/3_camera_usb_webcam.sh (installs opencv-python)", DOC_ASM)
            self.skip("Camera video clip", "skipped (cv2 missing)")
            self.skip("Camera live feed", "not viewable over headless SSH")
            return
        # 2) device node present? (a bare index N addresses /dev/videoN)
        if src.isdigit() and not os.path.exists(f"/dev/video{src}"):
            self.bad(label, f"/dev/video{src} not found (present: {vids}) -- is the webcam plugged in? point "
                     "ROBOT_CAMERA_SOURCE at a listed index (`v4l2-ctl --list-devices`)", DOC_ASM)
            self.skip("Camera video clip", "skipped (no device)")
            self.skip("Camera live feed", "not viewable over headless SSH")
            return
        # 3) capture through the real backend (camera.Camera -> GstCamera -> cv2.VideoCapture(SOURCE))
        try:
            from camera import Camera
            cam = Camera()
            try:
                if not cam.available:
                    self.bad(label, f"backend could not open the webcam (source={src}; /dev/video*: {vids}) -- "
                             "another process may hold it, or try a different index", DOC_ASM)
                else:
                    data = cam.capture_jpeg()
                    if data:
                        os.makedirs(OUTDIR, exist_ok=True)
                        path = os.path.join(OUTDIR, "diag_webcam.jpg")
                        with open(path, "wb") as f:
                            f.write(data)
                        self.ok(label, f"{len(data)} bytes -> {path}  (cv2, source={src})")
                    else:
                        self.bad(label, f"opened the webcam (source={src}) but captured NO frame -- try "
                                 "another index or unplug/replug the cam", DOC_ASM)
            finally:
                cam.close()
        except Exception as e:
            self.bad(label, f"capture failed: {e}", DOC_ASM)
        self.skip("Camera video clip", "n/a for a USB webcam -- the robot only does still capture on v4l2")
        self.skip("Camera live feed", "not viewable over headless SSH; the still capture covers the pipeline")

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
            with wave.open(wav, "rb") as wf:     # P2-5: context manager -> handle released even if read raises
                raw = wf.readframes(wf.getnframes())
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
        # P2-6: saved here and restored in the finally so the override can't leak past this test (the
        # outer try/finally also covers the `import driver` early-return path).
        prev_gate = os.environ.get("ROBOT_OBSTACLE_CM")
        os.environ["ROBOT_OBSTACLE_CM"] = "-1000"
        d = None
        try:
            try:
                import driver
            except Exception as e:
                for la in labels:
                    self.bad(la, f"driver import failed: {e}", DOC_PWM)
                return
            print(dim("    Building the driver (the ESC should beep its arming tones)..."))
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
            # Let the ESC finish its arm tones before the first throttle command -- the long "ready" beep
            # can land a couple seconds after neutral is first held, so a too-eager FORWARD would no-op.
            time.sleep(2.0)
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
            # P2-1: only labels not ALREADY emitted get the blanket failure. self.failed holds
            # (label, reason, doc) TUPLES, so unpack the label -- passed AND failed AND skipped.
            emitted = set(self.passed) | set(self.skipped) | {la for (la, _r, _d) in self.failed}
            for la in labels:
                if la not in emitted:
                    self.bad(la, f"{e}", DOC_PWM)
        finally:
            if d is not None:
                try:
                    d.close()    # neutral throttle + center steer on the way out
                except Exception:
                    pass
            # P2-6: undo the gate override (driver caches obstacle_cm at __init__, so timing vs d.close()
            # is irrelevant; delete the key if it was unset before this test).
            if prev_gate is None:
                os.environ.pop("ROBOT_OBSTACLE_CM", None)
            else:
                os.environ["ROBOT_OBSTACLE_CM"] = prev_gate

    # ===================== orchestration =====================
    def check_fan(self):
        """Enclosure exhaust fan (ROBOT_FAN_ENABLE). Skips when disabled (still shows the SoC temp). When
        enabled, PULSES the fan pin ON for ~2s so you can confirm airflow -- driving a GPIO output is harmless
        (no motion). Also prints the current SoC temperature so you can sanity-check the ON/OFF thresholds."""
        from fan import read_cpu_temp_c
        label = "Enclosure exhaust fan (GPIO)"
        t = read_cpu_temp_c()
        tstr = f"{t:.1f}C" if t is not None else "unknown"
        if os.environ.get("ROBOT_FAN_ENABLE", "0").strip().lower() not in ("1", "true", "yes", "on"):
            return self.skip(label, f"disabled (ROBOT_FAN_ENABLE=0); SoC temp now {tstr}")
        pin = os.environ.get("ROBOT_FAN_PIN", "").strip()
        if not pin:
            return self.bad(label, "ROBOT_FAN_ENABLE=1 but ROBOT_FAN_PIN is empty -- set the fan's GPIO pin", DOC_ASM)
        out = None
        try:
            import hal
            out = hal.current().make_digital_output(int(pin))
            print(dim(f"    pulsing the fan ON for 2s (pin {pin}, SoC temp {tstr}) -- listen/feel for airflow..."))
            out.on(); time.sleep(2.0); out.off()
            if self.args.yes:
                self.ok(label, f"drove pin {pin}; SoC temp now {tstr}")
            else:
                self.confirm_step(label, "Did the fan spin / did you feel airflow?",
                                  "fan didn't spin -- check the MOSFET gate wiring + the fan's power", DOC_ASM)
        except Exception as e:
            self.bad(label, f"could not drive pin {pin}: {e} -- check ROBOT_FAN_PIN + the MOSFET wiring", DOC_ASM)
        finally:
            if out is not None:
                try:
                    out.off(); out.close()
                except Exception:
                    pass

    def run(self):
        os.makedirs(OUTDIR, exist_ok=True)
        print(bold("\nLocalAiRobot hardware diagnostics") +
              dim("  (live wiring self-test -- artifacts in ~/.localairobot/diagnostics)\n"))
        if not self.args.yes:
            print(dim("  Automated checks run first; the ones that need YOU (listen / speak / watch) come after."
                      " Ctrl-C to abort.\n"))
        # --- automated: measured, no user input (and NO driver yet -> the ESC stays unarmed) ---
        print(bold("-- Automated checks --"))
        self.check_power()      # power underlies everything else -- a sagging rail fakes ALL the failures below
        self.check_pca9685()
        self.check_sensors()
        self.check_camera()
        # --- need YOU: listen / speak / watch. Motor/servo is LAST -- it builds the driver (arms the ESC). ---
        print(bold("\n-- Checks that need you (listen / speak / watch) --"))
        self.check_speaker()
        self.check_mic()
        self.check_face()
        self.check_fan()
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
    # Step 19: refuse to race a LIVE brain: two writers on the ESC/PCA9685 plus GPIO/camera/mic contention
    # give false FAILs. Best-effort (mirrors scripts/shutdown.sh); an error here must never block a
    # legitimate run (via the wrapper the service/brain are already stopped, so this is a no-op).
    try:
        service_up = bool(shutil.which("systemctl")) and subprocess.run(
            ["systemctl", "is-active", "--quiet", "robot.service"], timeout=5).returncode == 0
    except Exception:
        service_up = False
    try:
        # Same anchored pattern as scripts/shutdown.sh -- matches ONLY the brain's python process, never an
        # editor/tail mentioning the path, and never this diagnostics process.
        brain_up = subprocess.run(["pgrep", "-f", r"python.*brain/main\.py"], timeout=5,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        brain_up = False
    if service_up or brain_up:
        print(red("The robot brain is RUNNING -- a live run would put TWO writers on the ESC/GPIO and"))
        print(red("give false results. Stop it first (./scripts/shutdown.sh, or"))
        print(red("`sudo systemctl stop robot.service`), or launch via ./scripts/run_diagnostics.sh"))
        print(red("which stops it for you."))
        return 3
    try:
        return Diag(args).run()
    except KeyboardInterrupt:
        print("\naborted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
