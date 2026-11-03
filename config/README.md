# Settings reference

Every setting in `config/robot.env`, in the same order as the file. The default is the value the template
ships; *(empty)* means the code's own default applies. How settings work, and the ones people usually change:
[docs/configuration.md](../docs/configuration.md).

- Edit `config/robot.env` (your own copy, which git ignores) and apply it with `./scripts/run_me.sh --reload`.
- `1` means on and `0` means off. A path starting with `~` means your home folder.
- A setting missing from your file uses the code's default. After an update, `./scripts/check_config.sh`
  lists the new ones.

## Run mode

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_CONCIERGE_MODE` | `0` | 1 = stationary greeter (never drives, wakes on motion); 0 = crawler car |
| `ROBOT_HALLOWEEN_MODE` | `0` | 1 = scare visitors instead of greeting them (needs ROBOT_CONCIERGE_MODE=1) |
| `ROBOT_HALLOWEEN_CAMERA_CHECK` | `1` | Halloween: 1 = scare only when the camera sees a person; 0 = motion alone |
| `ROBOT_HALLOWEEN_PERSON_SCORE` | `0.5` | Halloween: how sure the person detector must be (0-1) |
| `ROBOT_DRY_RUN` | `0` | 1 = fake the motor and sensors and log what it would do (bench mode) |
| `ROBOT_FAKE_DISTANCE_CM` | `200` | dry run: pretend front distance; under 30 trips the obstacle stop |
| `ROBOT_MOTORS_OFF` | `0` | 1 = run live but send the ESC and steering nothing (desk testing) |
| `ROBOT_LOG_LEVEL` | `INFO` | INFO or DEBUG (see [docs/troubleshooting.md](../docs/troubleshooting.md)) |
| `ROBOT_POWER_MONITOR` | `1` | 1 = warn when the Pi reports under-voltage or throttling |
| `ROBOT_POWER_LOG_PERIOD_S` | `10` | seconds between power checks (at least 2) |
| `ROBOT_POWER_MIN_V` | `4.90` | Pi 5: warn early when the input voltage drops below this |

## Install-time flags

Read by the installers, not the robot. Override one inline: ROBOT_INSTALL_COMITUP=0 ./scripts/install_stack_pi5.sh

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_INSTALL_COMITUP` | `1` | 1 = set up the Wi-Fi fallback portal ([docs/change-pi-wifi.md](../docs/change-pi-wifi.md)) |
| `ROBOT_PERSIST_JOURNAL` | `1` | 1 = keep logs across reboots |
| `ROBOT_BOOT_TO_CONSOLE` | `1` | 1 = boot to the console, not the desktop (a DSI/HDMI face needs this) |

## Identity

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_NAME` | `Jarvis` | what the robot calls itself (this is not the wake word) |

## Board

On a Jetson the sensor pins are physical header numbers. `install_stack_jetson.sh` comments out the
Pi pins below so the Jetson defaults apply (front 16/18, left 29/31, right 32/33); check them against
your wiring.

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_BOARD` | `auto` | auto \| pi5 \| jetson |
| `ROBOT_I2C_BUS` | *(empty)* | PCA9685 I2C bus; empty = bus 1 on the Pi (Jetson: 7) |
| `ROBOT_CAMERA_BACKEND` | *(empty)* | empty = board default; v4l2 = USB webcam (the camera patches set it) |
| `ROBOT_CAMERA_SOURCE` | *(empty)* | v4l2: webcam index or /dev/videoN; gstreamer: a pipeline |

## AI backend

local runs offline on the Pi. gemini uses Google Gemini (needs a key; camera frames leave the Pi).

mixed uses Gemini and falls back to local when it can't reach it. See [docs/gemini-setup.md](../docs/gemini-setup.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_AI_BACKEND` | `local` | local \| gemini \| mixed |
| `ROBOT_GEMINI_API_KEY` | *(empty)* | Google AI Studio key; keep it secret |
| `ROBOT_GEMINI_MODEL` | `gemini-3.5-flash` | Gemini text model (others: gemini-3-flash, gemini-3.1-pro, gemini-3.1-flash-lite) |
| `ROBOT_GEMINI_VISION_MODEL` | `gemini-robotics-er-2-preview` | Gemini vision model |
| `ROBOT_GEMINI_VISION_THINKING_LEVEL` | `minimal` | vision thinking: minimal \| low \| medium \| high |
| `ROBOT_GEMINI_VISION_THINKING_BUDGET` | `0` | vision thinking budget, used when the level is empty; 0 = off |
| `ROBOT_GEMINI_THINKING_LEVEL` | *(empty)* | text thinking level; empty = use the budget below |
| `ROBOT_GEMINI_THINKING_BUDGET` | `0` | text thinking budget; 0 = off |
| `ROBOT_GEMINI_URL` | *(a list; see the file)* | API address (change it for a proxy) |

## Local AI, speech in and out

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_MODEL` | `gemma4:e2b-it-qat` | local text model; gemma4:e4b-it-qat is smarter but about 2x slower |
| `ROBOT_VISION_MODEL` | `moondream` | local vision model; empty = use ROBOT_MODEL |
| `ROBOT_OLLAMA_URL` | *(a list; see the file)* | Ollama address |
| `ROBOT_LLM_TIMEOUT_S` | `120` | seconds allowed per model call; raise it if the first command times out |
| `ROBOT_LLM_RETRIES` | `1` | retries after a network hiccup |
| `ROBOT_WARMUP` | `1` | 1 = load the model at start-up so the first command is quick |
| `ROBOT_WARMUP_RETRY_S` | `90` | keep retrying that warm-up for this many seconds |
| `ROBOT_OLLAMA_KEEPALIVE` | `30m` | how long the model stays loaded ('30m'; -1 = forever) |
| `ROBOT_NUM_CTX` | `4096` | model context size; smaller is faster |
| `ROBOT_WHISPER_BIN` | *(a list; see the file)* | speech-to-text program |
| `ROBOT_WHISPER_MODEL` | *(a list; see the file)* | speech-to-text model |
| `ROBOT_PIPER_BIN` | `~/piper/piper/piper` | text-to-speech program |
| `ROBOT_PIPER_VOICE` | `~/piper/en_US-lessac-medium.onnx` | voice model; audition others with ./scripts/scan_piper_voice.sh |
| `ROBOT_PIPER_SPEAKER` | *(empty)* | speaker id inside a multi-speaker voice; empty for most voices |
| `ROBOT_TTS_PRONUNCIATIONS` | `"Ramesh=Ruh-mesh"` | how to say names: "Name=Respelling; Other=..." |
| `ROBOT_MIC` | `plughw:CARD=Device,DEV=0` | microphone for commands, by name (set by ./scripts/setup_audio.sh) |
| `ROBOT_RECORD_SECS` | `6` | seconds recorded per command |
| `ROBOT_TMPDIR` | `/dev/shm` | scratch folder for audio (in memory) |

## Conversation and wake word

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_FOLLOWUP_ENABLE` | `1` | 1 = keep listening after a reply, with no wake word needed |
| `ROBOT_FOLLOWUP_SILENCE_S` | `12` | seconds of quiet before it stops listening |
| `ROBOT_WAKEWORD_ENABLE` | `1` | 1 = listen for the wake word |
| `ROBOT_WAKEWORD_MODEL` | *(a list; see the file)* | wake word model; see [models/README.md](../models/README.md) |
| `ROBOT_WAKEWORD_THRESHOLD` | `0.4` | detection score needed (0-1); lower wakes more easily |
| `ROBOT_WAKEWORD_TRIGGER_LEVEL` | `1` | detections in a row needed; 2 if it wakes by itself |
| `ROBOT_WAKEWORD_COOLDOWN_S` | `2.0` | quiet time after a wake or after speaking |
| `ROBOT_WAKEWORD_DEVICE` | `PnP` | wake microphone: part of its name (set by setup_audio.sh) |
| `ROBOT_WAKEWORD_CAPTURE_RATE` | `0` | 0 = pick the microphone's sample rate automatically |
| `ROBOT_WAKEWORD_QUEUE_MAX` | `32` | microphone buffer, in 80 ms chunks |
| `ROBOT_WAKEWORD_LATENCY` | `high` | microphone buffer: high (safer under load), low, or seconds |
| `ROBOT_WAKEWORD_STREAM_RESET_S` | `15` | restart the microphone after this long of dropped audio |
| `ROBOT_WAKEWORD_DEGRADED_PCT` | `20` | warn when this percentage of microphone audio is dropped |
| `ROBOT_WAKEWORD_DROP_RESET_N` | `3` | dropped chunks in a row before the detector resets |
| `ROBOT_WAKEWORD_MAX_ERRORS` | `10` | microphone errors in a row before the robot restarts |
| `ROBOT_WAKEWORD_REBUILD_MAX_S` | `60` | restart the robot if the microphone can't recover in this long |
| `ROBOT_FAKE_WAKE` | `0` | dry run: 1 = fire the wake word once by itself |

## Sounds

The short spoken clips. See [sounds/README.md](../sounds/README.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_SOUNDS_ENABLE` | `1` | 1 = play the clips |
| `ROBOT_AUDIO_SELFHEAL` | `1` | 1 = set up the speaker and volume again at every start |
| `ROBOT_SPEAKER_VOLUME` | `100` | speaker volume, 0-100 (apply now with ./scripts/setup_audio.sh) |
| `ROBOT_SOUNDS_DIR` | *(empty)* | empty = the repo's sounds/ folder |
| `ROBOT_SOUNDS_DEVICE` | `plughw:CARD=UACDemoV10,DEV=0` | speaker for the clips, by name (set by setup_audio.sh) |
| `ROBOT_SOUNDS_GREETING` | `1` | clip at start-up |
| `ROBOT_SOUNDS_WAKE_ACK` | `1` | "Yes?" when it hears the wake word |
| `ROBOT_SOUNDS_ACK` | `1` | "Got it" when it accepts a command |
| `ROBOT_SOUNDS_REMOTE_ACK` | `1` | clip when a phone command arrives |
| `ROBOT_SOUNDS_THINKING` | `1` | clip while the model works |
| `ROBOT_SOUNDS_THINKING_STILL` | `1` | "Still on it" when that takes longer |
| `ROBOT_SOUNDS_SLEEP` | `1` | goodbye when a conversation ends |
| `ROBOT_SOUNDS_ERROR` | `1` | clip on an error or an obstacle |
| `ROBOT_THINKING_LEAD_MS` | `500` | local model: wait this long before the first thinking clip |
| `ROBOT_THINKING_REPEAT_S` | `10` | local model: seconds between thinking clips; 0 = once |
| `ROBOT_THINKING_LEAD_REMOTE_MS` | `4000` | Gemini: wait this long before a thinking clip |
| `ROBOT_THINKING_REPEAT_REMOTE_S` | `0` | Gemini: seconds between thinking clips; 0 = once |

## Camera and vision

A USB webcam on either board. Run one camera patch: scripts/patches/<board>/3_camera_usb_*.sh

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_CAMERA_ENABLE` | `1` | 1 = use the camera |
| `ROBOT_CAMERA_SIZE` | `768` | square image size for vision and face recognition, in pixels |
| `ROBOT_CAMERA_CAPTURE_WIDTH` | `1280` | USB webcam: resolution to ask for (width) |
| `ROBOT_CAMERA_CAPTURE_HEIGHT` | `720` | USB webcam: resolution to ask for (height) |
| `ROBOT_CAMERA_WARMUP_S` | `1.0` | CSI camera: seconds for exposure to settle before a shot |
| `ROBOT_CAMERA_DISCARD_FRAMES` | `5` | USB webcam: stale frames to skip on a first capture |
| `ROBOT_CAMERA_DISCARD_FRAMES_WARM` | `1` | USB webcam: frames to skip when the last capture was recent |
| `ROBOT_CAMERA_WARM_WINDOW_S` | `2.0` | how many seconds counts as recent |
| `ROBOT_CAMERA_AUTOFOCUS` | `1` | 1 = autofocus; 0 for fixed-lens modules |
| `ROBOT_CAMERA_AF_MIN_INTERVAL_S` | `3` | CSI camera: minimum seconds between focus cycles |
| `ROBOT_WAKE_PERSON_CHECK` | `auto` | on a wake, check who is in view: auto (Gemini only) \| 1 \| 0 |
| `ROBOT_VISION_TIMEOUT_S` | `600` | seconds allowed for a local vision call (slow on the Pi) |
| `ROBOT_VISION_TIMEOUT_REMOTE_S` | `45` | seconds allowed for a Gemini vision call |
| `ROBOT_FAKE_VISION` | *(empty)* | dry run: pretend sighting, e.g. sofa:left:near:0.9 |

## Concierge mode

Only used when ROBOT_CONCIERGE_MODE=1. See [docs/concierge.md](../docs/concierge.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_WAKE_SENSOR` | `pir` | the arrival sensor; pir is the only option |
| `ROBOT_PIR_PIN` | `4` | PIR signal pin (BCM); empty = wake by voice only |
| `ROBOT_PIR_WARMUP_S` | `0` | ignore the PIR this long after start-up (PaPIRs: 210) |
| `ROBOT_PIR_PRESENCE_HOLD_S` | `5` | a PIR pulse counts as someone present for this long |
| `ROBOT_PIR_PULL` | `down` | pin resistor: down (right for both supported PIRs) or none |
| `ROBOT_PIR_HOLD_S` | `0` | spike filter; 0 = off (recommended) |
| `ROBOT_PIR_COOLDOWN_S` | `2` | PIR triggers this close together count as one |
| `ROBOT_CONCIERGE_SILENT_COOLDOWN_S` | `4` | ignore motion this long after finding nobody |
| `ROBOT_CONCIERGE_GREET_COOLDOWN_S` | `60` | ignore motion this long after a greeting |
| `ROBOT_HALLOWEEN_COOLDOWN_S` | `10` | Halloween: ignore motion this long after a scare |
| `ROBOT_CONCIERGE_PROBE_TRIES` | `4` | quick "anyone there?" frames before recognition starts |
| `ROBOT_CONCIERGE_PROBE_INTERVAL_S` | `0.3` | seconds between those frames |
| `ROBOT_CONCIERGE_PROBE_MAX_S` | `20` | keep looking while the PIR still sees someone, up to this |
| `ROBOT_CONCIERGE_SMILE_HOLD_S` | `5` | how long the smile outlasts the PIR |
| `ROBOT_CONCIERGE_RECOGNIZE_MAX_S` | `45` | longest a recognition attempt may run |
| `ROBOT_CONCIERGE_SMILE_ON_MOTION` | `1` | 1 = smile as soon as motion fires; 0 = once someone is seen |
| `ROBOT_CONCIERGE_GREET_ONLY` | `0` | 1 = skip the spoken goodbye |
| `ROBOT_CONCIERGE_SUMMARY_S` | `3600` | seconds between summary lines in the log; 0 = off |
| `ROBOT_PIR_CHATTER_N` | `20` | warn if more wakes than this happen... |
| `ROBOT_PIR_CHATTER_WINDOW_S` | `600` | ...within this many seconds |

### Wireless second sensor

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_ZIGBEE_WAKE` | `0` | 1 = also wake on a Zigbee motion sensor |
| `ROBOT_ZIGBEE_TOPIC` | *(empty)* | its Zigbee2MQTT topic, e.g. zigbee2mqtt/pir_hallway |
| `ROBOT_MQTT_HOST` | `localhost` | MQTT broker (keep it on this Pi) |
| `ROBOT_MQTT_PORT` | `1883` | MQTT port |
| `ROBOT_MQTT_USER` | *(empty)* | empty = anonymous |
| `ROBOT_MQTT_PASS` | *(empty)* | keep it secret |
| `ROBOT_ZIGBEE_COOLDOWN_S` | `2` | Zigbee triggers this close together count as one |
| `ROBOT_ZIGBEE_OCCUPANCY_TIMEOUT_S` | `90` | copy your sensor's own occupancy_timeout here |
| `ROBOT_WAKE_DEDUPE_S` | `2` | both sensors firing within this long count as one arrival |

### Face recognition

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_CONCIERGE_RECOGNIZE_S` | `7` | keep trying to recognise someone for this long |
| `ROBOT_CONCIERGE_RECOGNIZE_INTERVAL_S` | `0.2` | seconds between recognition frames |
| `ROBOT_CONCIERGE_FACE_HITS` | `2` | frames with a face before a stranger gets a hello |
| `ROBOT_FACE_ID_MODEL` | *(empty)* | recognition model; empty = the one the installer downloads |
| `ROBOT_FACE_ID_THRESHOLD` | `0.38` | how close a face must match (0-1); higher is stricter |
| `ROBOT_FACE_ID_INPUT` | `112` | model input size, in pixels |
| `ROBOT_FACE_ID_DETECTOR` | *(empty)* | Haar cascade for the fallback detector; empty = OpenCV's own |
| `ROBOT_FACE_ID_DETECTOR_BACKEND` | `auto` | auto \| yunet \| haar; YuNet aligns faces, which matters most |
| `ROBOT_FACE_ID_YUNET_MODEL` | *(empty)* | YuNet model; empty = the one the installer downloads |
| `ROBOT_FACE_ID_YUNET_SCORE` | `0.6` | YuNet confidence (0-1); raise it if scenery looks like a face |
| `ROBOT_FACE_ID_MIN_MARGIN` | `0.08` | how far ahead of the runner-up a match must be to use a name |
| `ROBOT_FACE_ID_AGGREGATE` | *(empty)* | combining one person's photos: max (default) \| topk \| mean \| centroid |
| `ROBOT_FACE_ID_TOPK` | `3` | photos averaged by topk |
| `ROBOT_FACE_ID_MIN_NEIGHBORS` | `5` | Haar only: how strict detection is |
| `ROBOT_FACE_ID_MIN_FACE_PX` | `60` | ignore live faces smaller than this, in pixels |
| `ROBOT_FACE_ID_MIN_FACE_PX_ENROLL` | `40` | the same for enrolment photos |
| `ROBOT_FACE_ID_WARMUP` | `1` | 1 = load the face model at start-up |

### "Who is this?" questions

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_IDENTIFY_TRIGGERS` | *(empty)* | extra phrases, comma-separated (added to the built-in ones) |
| `ROBOT_IDENTIFY_FRAMES` | `3` | frames looked at per question |
| `ROBOT_IDENTIFY_MAX_FACES` | `5` | faces recognised per frame |
| `ROBOT_LOOK_IDENTIFIES` | `1` | 1 = also name people in "what do you see" answers |

### Detect captures

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_DETECT_CAPTURE` | `1` | 1 = keep the deciding frame of each motion wake |
| `ROBOT_DETECT_CAPTURE_DIR` | `~/detect-captures` | where they go (a folder of their own) |
| `ROBOT_DETECT_CAPTURE_MODE` | `greeted` | greeted = greetings only; all = every look (fills the disk fast) |
| `ROBOT_DETECT_CAPTURE_KEEP_DAYS` | `7` | delete captures older than this; 0 = keep them |
| `ROBOT_DETECT_CAPTURE_MAX_MB` | `2000` | keep the folder under this size; 0 = no limit |
| `ROBOT_DETECT_CAPTURE_CRON` | `1` | 1 = install the daily clean-up job |
| `ROBOT_DETECT_CAPTURE_CRON_SCHEDULE` | *(empty)* | its cron schedule; empty = 04:17 daily |

## Social memory

Greetings that remember when they last saw you and what you said. See [docs/social.md](../docs/social.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_SOCIAL_MEMORY` | `1` | 1 = on |
| `ROBOT_SOCIAL_LONG_GAP_D` | `14` | days away before "it's been a while!" |
| `ROBOT_SOCIAL_PLAN_MIN_AGE_S` | `7200` | seconds before it asks how a plan went |
| `ROBOT_SOCIAL_CURIOSITY` | `1` | 1 = ask what someone does if it doesn't know |
| `ROBOT_SOCIAL_CURIOSITY_AFTER` | `3` | exchanges before it asks |
| `ROBOT_SOCIAL_CURIOSITY_COOLDOWN_D` | `30` | days before it asks again |
| `ROBOT_SOCIAL_EXTRACT_TIMEOUT_S` | `25` | seconds allowed for the after-chat learning call |
| `ROBOT_MEMORY_RETAIN_SIGHTINGS` | `2000` | sightings kept |
| `ROBOT_MEMORY_RETAIN_NOTES` | `50` | notes kept per person |

## Weather

The 3-day forecast card and weather-aware greetings. See [docs/weather.md](../docs/weather.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_WEATHER_ENABLE` | `1` | 1 = on; it fetches the forecast from the internet |
| `ROBOT_WEATHER_PROVIDER` | `open-meteo` | open-meteo (no key needed) |
| `ROBOT_WEATHER_LAT` | `-37.81` | latitude, 2 decimals; the default is Melbourne CBD, not anyone's home |
| `ROBOT_WEATHER_LON` | `144.96` | longitude, 2 decimals |
| `ROBOT_WEATHER_TZ` | *(empty)* | time zone, e.g. Europe/London; empty = Australia/Melbourne |
| `ROBOT_WEATHER_REFRESH_S` | `1800` | seconds between forecast fetches |
| `ROBOT_WEATHER_TIMEOUT_S` | `8` | seconds allowed per fetch |
| `ROBOT_WEATHER_STALE_S` | `21600` | how long a saved forecast stays usable, in seconds |
| `ROBOT_WEATHER_CACHE` | *(empty)* | saved forecast; empty = ~/.localairobot/weather_cache.json |
| `ROBOT_WEATHER_GREETING` | `1` | 1 = greetings mention the time of day and the weather |
| `ROBOT_WEATHER_CHILLY_C` | `15` | today's high at or below this: "pack a jumper" |
| `ROBOT_WEATHER_HOT_C` | `30` | today's high at or above this: sunscreen advice |
| `ROBOT_WEATHER_TRIGGERS` | *(empty)* | extra weather-question phrases, comma-separated |
| `ROBOT_WEATHER_NIGHT_ICONS` | `1` | 1 = moon icons after sunset |
| `ROBOT_WEATHER_EVENING_ROLL` | `1` | 1 = after sunset the first column shows tonight |
| `ROBOT_WEATHER_FROST_C` | `2` | tonight's low at or below this: frost icon, if drawn |
| `ROBOT_WEATHER_SCORCHER_C` | `35` | today's high at or above this: hot icon, if drawn |

## Drive

Find these values with [docs/calibration.md](../docs/calibration.md). The throttle values come from your ESC patch.

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_PCA9685_CHANNELS` | `16` | PCA9685 channel count |
| `ROBOT_ESC_CHANNEL` | `0` | PCA9685 channel for the ESC (the WP-1060 patch sets 14) |
| `ROBOT_STEER_CHANNEL` | `1` | PCA9685 channel for the steering servo |
| `ROBOT_STEER_CENTER` | `95` | steering angle for straight ahead |
| `ROBOT_STEER_LEFT` | `135` | full left, short of buzzing |
| `ROBOT_STEER_RIGHT` | `30` | full right, short of buzzing |
| `ROBOT_MIN_FORWARD_SPEED` | *(empty)* | slowest forward throttle (set by the ESC patch) |
| `ROBOT_MAX_FORWARD_SPEED` | *(empty)* | fastest forward throttle (ESC patch) |
| `ROBOT_MIN_REVERSE_SPEED` | *(empty)* | slowest reverse throttle (ESC patch) |
| `ROBOT_MAX_REVERSE_SPEED` | *(empty)* | fastest reverse throttle; more negative is faster (ESC patch) |
| `ROBOT_ESC_NEUTRAL` | *(empty)* | stop-and-arm throttle (ESC patch); empty = 0.0 |
| `ROBOT_THROTTLE_INVERT` | *(empty)* | 1 if forward and reverse come out swapped (ESC patch) |
| `ROBOT_ESC_REVERSE_DOUBLETAP` | *(empty)* | 0 = direct reverse; 1 = ESCs that need brake-then-reverse (ESC patch) |
| `ROBOT_SPEED_INCREMENT_STEP` | `0.05` | round the throttle to steps of this size; 0 = off |
| `ROBOT_DRIVE_CRAWL_SPEED` | `5` | speed cap (0-100) unless you say fast or hurry |
| `ROBOT_ESC_ARM_SECS` | `3` | seconds of neutral at start-up so the ESC arms |
| `ROBOT_ESC_ARM_PROMPT` | `1` | 1 = say "switch the speed controller on now" at start-up |
| `ROBOT_ESC_REV_BRAKE_MS` | `400` | brake-then-reverse ESCs only: brake tap length, in ms |
| `ROBOT_ESC_REV_GAP_MS` | `300` | brake-then-reverse ESCs only: gap between taps, in ms |
| `ROBOT_MAX_MOVE_MS` | `5000` | longest one command may drive, in ms (failsafe) |

## Obstacle sensors

Three HC-SR04P on 3.3 V, or VL53L1X lasers. Wiring: [docs/wiring/ultrasonic-sensors-wiring.md](../docs/wiring/ultrasonic-sensors-wiring.md)

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_OBSTACLE_CM` | `8` | stop when the front sensor reads closer than this (cm) |
| `ROBOT_OBSTACLE_DEBOUNCE` | `3` | close readings in a row before it stops |
| `ROBOT_SIDE_OBSTACLE_CM` | `25` | exploring steers away from side readings closer than this (cm) |
| `ROBOT_SIDE_CUT_CM` | `12` | stop when a side sensor reads closer than this (cm) |
| `ROBOT_SENSOR_MAX_DISTANCE_M` | `1.0` | ultrasonic range limit in metres; shorter keeps the microphone smooth |
| `ROBOT_SENSOR_IDLE_QUIESCE` | `1` | 1 = pause the sensors while waiting for the wake word |
| `ROBOT_SENSOR_VERBOSE` | `0` | 1 = log every distance reading and PIR edge |
| `ROBOT_SENSOR_LOG_PERIOD_S` | `2.0` | seconds between those log lines |
| `ROBOT_SENSOR_TYPE` | `hcsr04` | hcsr04 (ultrasonic) \| vl53l1x (laser) |
| `ROBOT_HCSR04_BACKEND` | `rawping` | rawping (default) \| gpiozero (fallback) |

### Ultrasonic pins

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_TRIG_PIN` | `23` | front TRIG |
| `ROBOT_ECHO_PIN` | `24` | front ECHO |
| `ROBOT_LEFT_TRIG_PIN` | `5` | left TRIG |
| `ROBOT_LEFT_ECHO_PIN` | `6` | left ECHO |
| `ROBOT_RIGHT_TRIG_PIN` | `20` | right TRIG |
| `ROBOT_RIGHT_ECHO_PIN` | `21` | right ECHO |
| `ROBOT_FAKE_LEFT_CM` | `200` | dry run: pretend left distance |
| `ROBOT_FAKE_RIGHT_CM` | `200` | dry run: pretend right distance |

### VL53L1X lasers

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_TOF_XSHUT_FRONT` | `17` | front sensor's XSHUT pin |
| `ROBOT_TOF_XSHUT_LEFT` | `27` | left sensor's XSHUT pin |
| `ROBOT_TOF_XSHUT_RIGHT` | `22` | right sensor's XSHUT pin |
| `ROBOT_TOF_ADDR_FRONT` | `0x2A` | front sensor's I2C address |
| `ROBOT_TOF_ADDR_LEFT` | `0x2B` | left sensor's I2C address |
| `ROBOT_TOF_ADDR_RIGHT` | `0x29` | right sensor's I2C address (the power-on default) |

### Optional extras

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_IMU_TYPE` | *(empty)* | empty = none; bno085 |
| `ROBOT_IMU_ADDR` | `0x4A` | BNO085 I2C address (0x4B if SA0 is high) |
| `ROBOT_ENCODER_TYPE` | *(empty)* | empty = none; as5600 |
| `ROBOT_ENCODER_ADDR` | `0x36` | AS5600 I2C address (clashes with a UPS fuel gauge at 0x36) |

## Enclosure fan

Only for a sealed box. Wiring: [docs/assembly.md](../docs/assembly.md), step 7.

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_FAN_ENABLE` | `0` | 1 = run the exhaust fan |
| `ROBOT_FAN_PIN` | `26` | GPIO pin that switches the fan's MOSFET |
| `ROBOT_FAN_ON_TEMP_C` | `70` | fan on at or above this temperature (C) |
| `ROBOT_FAN_OFF_TEMP_C` | `60` | fan off at or below this (keep it a few degrees under ON) |
| `ROBOT_FAN_POLL_S` | `5` | seconds between temperature checks |

## Explore and find

These only run when you use one of the phrases below. See [docs/crawler.md](../docs/crawler.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_EXPLORE_TRIGGERS` | *(a list; see the file)* | phrases that start exploring or finding (comma-separated) |
| `ROBOT_LOOK_TRIGGERS` | *(a list; see the file)* | phrases that mean "look now and describe what you see" (comma-separated) |
| `ROBOT_EXPLORE_MAX_ITERS` | `12` | most look-and-step rounds (local model) |
| `ROBOT_EXPLORE_MAX_ITERS_REMOTE` | `24` | the same with Gemini |
| `ROBOT_EXPLORE_MAX_SECONDS` | `60` | longest an explore may run, in seconds (local model) |
| `ROBOT_EXPLORE_MAX_SECONDS_REMOTE` | `120` | the same with Gemini |
| `ROBOT_EXPLORE_NARRATE` | `1` | 1 = say what it notices |
| `ROBOT_EXPLORE_STEP_MS` | `1500` | drive time per step, in ms |
| `ROBOT_SCAN_STEP_MS` | `700` | find: nudge time while scanning for the target, in ms |
| `ROBOT_EXPLORE_SPEED` | `25` | speed while exploring (0-100) |
| `ROBOT_EXPLORE_MIN_ITERS` | `2` | steps before it may decide it's done |
| `ROBOT_EXPLORE_ESCAPE_CM` | `25` | back away when the front sensor reads closer than this (cm) |
| `ROBOT_EXPLORE_ESCAPE_SPEED` | `8` | speed when backing away; keep it low, the rear has no sensor |
| `ROBOT_EXPLORE_ESCAPE_MS` | `1500` | how long it backs away, in ms |
| `ROBOT_EXPLORE_ESCAPE_MAX_TRIES` | `3` | failed escapes before it stops and says it's stuck |
| `ROBOT_FIND_CONFIDENCE` | `0.55` | vision confidence needed to act on a sighting |
| `ROBOT_APPROACH_CM` | `40` | target reached when the front sensor reads closer than this (cm) |

## Memory

What it has seen and been taught, in SQLite. Look inside with ./scripts/rag.sh.

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_MEMORY_DB` | `~/.localairobot/memory.db` | database file |
| `ROBOT_MEMORY_MAX_CONTEXT` | `8` | recent things fed to the model each turn |
| `ROBOT_MEMORY_MAX_SCENES` | `3` | explore descriptions fed to the model |
| `ROBOT_MEMORY_RETAIN` | `5000` | observations kept |
| `ROBOT_MEMORY_RETAIN_SCENES` | `500` | explore descriptions kept |

## Face screen

A USB-C panel (turing) or a DSI/HDMI screen (framebuffer). Custom faces: [brain/faces/README.md](../brain/faces/README.md)

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_FACE_ENABLE` | `1` | 1 = show a face |
| `ROBOT_FACE_BACKEND` | `auto` | auto \| turing \| framebuffer \| simulated |
| `ROBOT_FACE_PORT` | `AUTO` | turing: serial port, or AUTO |
| `ROBOT_FACE_REVISION` | `A` | turing: A or B (check with the library's simple-program.py) |
| `ROBOT_FACE_WIDTH` | `320` | turing: the panel's own width (portrait) |
| `ROBOT_FACE_HEIGHT` | `480` | turing: the panel's own height |
| `ROBOT_FACE_ORIENTATION` | `landscape` | turing: landscape \| portrait \| reverse_landscape \| reverse_portrait |
| `ROBOT_FACE_PARTIAL` | `1` | turing: 1 = send only what changed (faster) |
| `ROBOT_FACE_MAX_FPS` | *(empty)* | frame rate cap; empty = 10 (turing) or 30 (framebuffer) |
| `ROBOT_FACE_TALK_DELAY_MS` | `0` | delay before the talking mouth starts, in ms |
| `ROBOT_FACE_BRIGHTNESS` | `80` | screen brightness |
| `ROBOT_FACE_LIB` | `~/turing-smart-screen-python` | turing: library folder (the installer clones it) |
| `ROBOT_FACE_DUMP` | *(empty)* | save each rendered face as a PNG here (debugging) |
| `ROBOT_FACE_FRAMES_DIR` | *(empty)* | custom face frames; empty = brain/faces |
| `ROBOT_FACE_FPS` | `4` | animation frame rate |
| `ROBOT_FACE_DEBUG_LABEL` | `0` | 1 = draw the state name on the screen (debugging) |

## Phone remote

Command the robot from your phone. See [docs/http_setup.md](../docs/http_setup.md).

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_HTTP_ENABLE` | `1` | 1 = on |
| `ROBOT_HTTP_PORT` | `8080` | port: http://<hostname>.local:PORT/speak |
| `ROBOT_HTTP_TOKEN` | *(empty)* | the phone's password; empty = made and saved here on first start. Keep it secret. |
| `ROBOT_HTTP_ALLOW_CAMERA` | `0` | 1 = allow camera actions over HTTP |
