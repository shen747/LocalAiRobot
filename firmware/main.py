# firmware/main.py  -- Pico W serial motor + sensor controller
#
# Protocol (newline-terminated, 115200 8N1):
#   IN : FWD REV STRAFE_L STRAFE_R TURN_L TURN_R STOP  SPEED <0-100>  PING  DIST?
#   OUT: OK <CMD> | PONG | DIST <cm> | EVT OBSTACLE <cm> | LOG <text> | ERR <reason>

import sys, select, time
from machine import Pin, PWM, time_pulse_us


class Motor:
    """Two-pin (PWM forward / PWM backward) brushed DC motor."""
    def __init__(self, pin_fwd, pin_bwd):
        self.fwd = PWM(Pin(pin_fwd)); self.fwd.freq(1000)
        self.bwd = PWM(Pin(pin_bwd)); self.bwd.freq(1000)

    def drive(self, duty):                 # duty in [-65535, 65535]
        duty = max(-65535, min(65535, int(duty)))
        if duty >= 0:
            self.fwd.duty_u16(duty);  self.bwd.duty_u16(0)
        else:
            self.fwd.duty_u16(0);     self.bwd.duty_u16(-duty)


# --- UPDATE THESE PINS to match your Freenove board revision's schematic. ---
FL = Motor(2, 3)    # front-left
FR = Motor(4, 5)    # front-right
RL = Motor(6, 7)    # rear-left
RR = Motor(8, 9)    # rear-right

# --- Ultrasonic (HC-SR04). ECHO MUST be level-shifted to 3.3V (see warning). ---
TRIG = Pin(16, Pin.OUT)   # UPDATE
ECHO = Pin(17, Pin.IN)    # UPDATE

def distance_cm():
    TRIG.low(); time.sleep_us(2); TRIG.high(); time.sleep_us(10); TRIG.low()
    dur = time_pulse_us(ECHO, 1, 30000)   # 30ms timeout (~5m)
    if dur < 0:
        return -1.0                         # no echo
    return (dur / 2) / 29.1                 # cm

# --- Mecanum motion ---
SPEED = 40000          # default duty (~61%); change via "SPEED <0-100>"
OBSTACLE_CM = 20       # refuse FWD closer than this

def set_speed_pct(pct):
    global SPEED
    SPEED = int(max(0, min(100, pct)) / 100 * 65535)

def stop():       FL.drive(0);      FR.drive(0);      RL.drive(0);      RR.drive(0)
def fwd():        FL.drive(SPEED);  FR.drive(SPEED);  RL.drive(SPEED);  RR.drive(SPEED)
def rev():        FL.drive(-SPEED); FR.drive(-SPEED); RL.drive(-SPEED); RR.drive(-SPEED)
def strafe_l():   FL.drive(-SPEED); FR.drive(SPEED);  RL.drive(SPEED);  RR.drive(-SPEED)
def strafe_r():   FL.drive(SPEED);  FR.drive(-SPEED); RL.drive(-SPEED); RR.drive(SPEED)
def turn_l():     FL.drive(-SPEED); FR.drive(SPEED);  RL.drive(-SPEED); RR.drive(SPEED)
def turn_r():     FL.drive(SPEED);  FR.drive(-SPEED); RL.drive(SPEED);  RR.drive(-SPEED)

# Single source of truth for the movement vocabulary (matches the Pi side).
MOVES = {"FWD": fwd, "REV": rev,
         "STRAFE_L": strafe_l, "STRAFE_R": strafe_r,
         "TURN_L": turn_l, "TURN_R": turn_r}

# --- Watchdog state ---
MOVE_TIMEOUT_MS = 1500
last_cmd_ms = time.ticks_ms()
moving = False

def emit(line):
    print(line)             # goes back over USB serial to the Pi


def handle(cmd):
    global moving, last_cmd_ms
    if cmd == "":
        return
    if cmd == "PING":
        emit("PONG")
    elif cmd == "DIST?":
        emit("DIST %.1f" % distance_cm())
    elif cmd.startswith("SPEED "):
        try:
            set_speed_pct(int(cmd.split()[1])); emit("OK SPEED")
        except Exception:
            emit("ERR SPEED")
    elif cmd == "STOP":
        stop(); moving = False; emit("OK STOP")
    elif cmd in MOVES:
        if cmd == "FWD":                       # forward obstacle gate
            d = distance_cm()
            if 0 <= d < OBSTACLE_CM:
                stop(); moving = False
                emit("EVT OBSTACLE %.1f" % d)
                return
        MOVES[cmd]()
        moving = True
        last_cmd_ms = time.ticks_ms()
        emit("OK " + cmd)
    else:
        emit("ERR UNKNOWN")


# --- Main loop ---
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
buf = ""
stop()
emit("LOG Pico ready")

while True:
    # Drain ALL currently-available bytes WITHOUT blocking on a newline.
    # This is the fix for watchdog starvation: a partial line (no '\n') can no
    # longer hang readline() and freeze the safety check below.
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch is None:
            break
        if ch == "\n":
            handle(buf.strip().upper()); buf = ""
        elif ch == "\r":
            pass
        else:
            buf += ch
            if len(buf) > 64:        # runaway guard: garbage with no newline
                buf = ""

    # Watchdog: reached every iteration, so a dropped link always stops the car.
    if moving and time.ticks_diff(time.ticks_ms(), last_cmd_ms) > MOVE_TIMEOUT_MS:
        stop(); moving = False; emit("LOG watchdog stop")

    time.sleep_ms(2)                 # don't pin the CPU at 100%
