# Weather — forecast card + weather-aware greetings

Three surfaces, one forecast. Set `ROBOT_WEATHER_ENABLE=1` (plus your coordinates) and the robot:

1. shows a **3-day forecast card** — `TODAY | TOMORROW | DAY AFTER`, an animated icon and a high/low per
   day — on the **sleep screen**, in place of the Zzz face;
2. **greets people with the weather**: *"Good morning, Ramesh! It's a very chilly day today, isn't it? Make
   sure to pack your jumper."*;
3. **answers "what's the weather"** from the actual forecast instead of letting the language model invent
   one.

It is the only online feature in an otherwise offline robot, so it is built to be **strictly additive**.
Nothing in the greeting path, the command loop or startup ever waits on the network.

---

## Quick start

```bash
# 1. Your coordinates. Right-click your house in any map app -- the two numbers are lat, lon.
#    2 decimal places (~1 km) is plenty for a forecast grid cell and leaks less.
sed -i -e 's|^ROBOT_WEATHER_ENABLE=.*|ROBOT_WEATHER_ENABLE=1|' \
       -e 's|^ROBOT_WEATHER_LAT=.*|ROBOT_WEATHER_LAT=-37.81|' \
       -e 's|^ROBOT_WEATHER_LON=.*|ROBOT_WEATHER_LON=144.96|' \
       -e 's|^ROBOT_WEATHER_TZ=.*|ROBOT_WEATHER_TZ=Australia/Melbourne|' \
       config/robot.env

# 2. Reload
./scripts/run_me.sh --reload

# 3. Confirm
journalctl -u robot.service -n 40 | grep -a 'weather'
# want: weather: open-meteo forecast for -37.81,144.96 (Australia/Melbourne) -- refresh 1800s, ...
```

> **Privacy.** `ROBOT_WEATHER_LAT/LON` are your home address to within a street, and `config/robot.env` is
> **tracked by git**. The template ships them blank on purpose — put your real values in your local-only
> commit, alongside the API key. See the git workflow in `BENCH_SCRIPTS.md`.

No API key and no account: [Open-Meteo](https://open-meteo.com) is keyless. One robot polling every 30
minutes uses 48 requests a day against a fair-use budget of roughly 10,000.

---

## What each surface does

### The forecast card (sleep screen only)

The `sleeping` face is the robot's long-duration idle surface, which makes it the natural home for a
forecast — you glance at the hallway panel and the weather is simply *there*. So the card **replaces** the
Zzz face whenever weather is enabled and a forecast is in hand, and appears **nowhere else**: greetings keep
their normal happy → talking → idle sequence, and a spoken weather answer uses the ordinary talking face.

It reverts to the honest Zzz face the moment the forecast expires, so the panel can never show yesterday's
"Today". It also rolls over at **midnight** without needing a wake event or a network call.

Idle cost, stated plainly: the Zzz face renders once and then sends nothing, whereas the card animates at
`ROBOT_FACE_FPS` (default 4) for the whole drowse. That is acceptable because the panel is IPS (no burn-in)
and, on the Turing panel, only the three icon rectangles cross the serial link per frame — everything else
is pixel-identical between frames by construction. `ROBOT_FACE_FPS` remains the global throttle.

### Weather-aware greetings

A greeting becomes a **time-of-day opener** plus **one** advice sentence:

| Today's forecast | What it says |
|---|---|
| Thunderstorms | *"Storms are forecast today, so maybe keep the outdoor plans flexible."* |
| Any rain condition | *"There's rain in the forecast today, so do take an umbrella."* |
| max ≤ `ROBOT_WEATHER_CHILLY_C` (15) | *"It's a very chilly day today, isn't it? Make sure to pack your jumper."* |
| max ≥ `ROBOT_WEATHER_HOT_C` (30) | *"It's going to be a hot one today, so keep your water bottle handy."* |
| anything else | *"It's looking like a lovely day out there."* |
| **no usable forecast** | the opener alone — no padding, no delay |

Rain outranks both temperature bands, because being rained on is the thing a person would actually have
changed their mind about. The band uses today's **max** — the "how should I dress" number — unrounded, so a
15.4° day isn't talked into the chilly band by display rounding.

Time bands are **5–11 morning, 12–16 afternoon, 17–4 evening**, in `ROBOT_WEATHER_TZ`.

`ROBOT_WEATHER_GREETING=0` turns just this surface off; the card and the spoken answer keep working.
**Halloween Mode outranks it** — on Halloween the spooky greeting wins and no weather line is added.

### "What's the weather?"

Answered deterministically from the forecast, not by the language model. A weather question is detected on
the **raw transcript** after the model has classified it — the same post-LLM promotion the `LOOK` gate uses
— and the robot then speaks a summary built from the data:

> *"Today it's 9 to 17 degrees and partly cloudy. Tomorrow, 8 to 15 and showers. Sunday, 18 to 31 and
> sunny."*

Built-in trigger phrases: `the weather`, `weather today`, `weather tomorrow`, `weather like`, `the
forecast`, `is it raining`, `will it rain`, `going to rain`, `need an umbrella`, `how hot is it`, `how cold
is it`, `how hot today`, `how cold today`. `ROBOT_WEATHER_TRIGGERS` **adds** to that list (it does not
replace it, unlike `ROBOT_LOOK_TRIGGERS`). Keep additions **multi-word** — matching is plain substring, so
a bare `temperature` would hijack *"what's your CPU temperature"*.

This works over the phone endpoint too: `POST /speak` runs the same path and the summary comes back as the
JSON `reply`. It is deliberately **not** in the `ROBOT_HTTP_ALLOW_CAMERA=0` block list, because it doesn't
use the camera.

### Follow-up questions

The gate's phrase list can't anticipate everything, so a **one-sentence note** is also prepended to the
model's context on every turn:

```
(Current weather, Melbourne: today 9-17C partly cloudy; tomorrow 8-15C showers; Monday 18-31C sunny.)
```

That is what lets *"should I take a jacket?"* or *"will I need the umbrella tomorrow?"* answer from real
data despite matching no trigger phrase.

The forecast is deliberately **never written into the RAG memory store**. That store dedupes facts in
relevance-rank order and never prunes them, so yesterday's forecast would routinely outrank today's and be
spoken as current — the exact failure this feature exists to prevent. The trade-off: `scripts/rag.sh` won't
show the forecast. It appears in the DEBUG prompt log instead, which is the right place for a 30-minute
fact.

---

## What happens when the internet is down

The whole design is this ladder, walked in order:

| State | Card | Greetings | *"What's the weather?"* |
|---|---|---|---|
| Fresh forecast | shown | opener + advice | exact 3-day answer |
| Network down, cache < `STALE_S` (6 h) | shown | opener + advice | answered from cache |
| Cache older than `STALE_S` | **Zzz face** | opener only | *"I can't reach the weather service right now."* |
| `LAT`/`LON` never set | Zzz face | plain concierge greeting | (gate is off) |
| `ROBOT_WEATHER_ENABLE=0` | Zzz face | plain concierge greeting | (gate is off) |

A stale forecast is **dropped rather than shown**, because the robot speaks these numbers out loud to
people who then dress for them — being confidently wrong about tomorrow is worse than saying nothing.

The last-good forecast is also written to `~/.localairobot/weather_cache.json`, so a **reboot during an
outage** still has one. A cache written for different coordinates is ignored, so moving the robot never
makes it greet the household with its old address's weather.

Logging is edge-triggered: one warning on entering the unusable state, one line on recovery, and successful
polls at DEBUG only. There is one summary line an hour, and quiet hours print nothing.

---

## The icons

Seven committed GIFs in `brain/weather_icons/`, named exactly for the seven canonical conditions:

```
Sunny.gif  PartlyCloudy.gif  Cloudy.gif  SunnyRain.gif  PartlyCloudyRain.gif  CloudyRain.gif  Thunderstorms.gif
```

To replace one, drop in a new file with the same name. Guidance:

- **Square, ≥128×128**, transparent or dark background (frames are alpha-composited onto a near-black card;
  a white-background GIF shows as a white square).
- **Any frame count.** Each icon is resampled across its *whole* timeline into the card's 8 frames, so a
  smooth 90-frame 30 fps loop plays its full motion, just slower — it is not truncated to the first 8
  frames.
- A missing or corrupt file degrades to a labelled placeholder with one warning. Nothing crashes, and the
  other two columns are unaffected.

Every forecast condition maps to one of those seven. There is no snow icon (this is a Melbourne hallway):
snow and freezing codes map to their closest rain equivalent rather than being filtered out, which is what
guarantees the card always has something to draw.

---

## Swapping the forecast provider

`ROBOT_WEATHER_PROVIDER` is the hot-swap point. Everything Open-Meteo-specific — its URL, its parameters
and its WMO code table — lives inside `_OpenMeteoProvider` in `brain/weather.py` and nowhere else. A new
source is:

1. one class with `fetch(lat, lon, tz, days, timeout_s)` returning normalized days
   (`{"date": "2026-08-08", "condition": "PartlyCloudy", "tmax": 17.2, "tmin": 9.1}`, where `condition` is
   one of the seven names above);
2. one entry in the `_PROVIDERS` registry;
3. the new key in `config/robot.env`.

An unknown value logs a warning naming the providers it does know and leaves weather unavailable — it never
raises.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `weather: ROBOT_WEATHER_LAT/LON not set` | coordinates missing | set both keys, then `--reload` |
| `weather: timezone ... unusable` | no tz database on the image | `sudo apt install tzdata` (the robot still runs, on the system clock) |
| `weather: provider HTTP 400 ... Invalid timezone` | typo in `ROBOT_WEATHER_TZ` | use an exact IANA name, e.g. `Australia/Melbourne` |
| Card never appears, greetings still weather-aware | the panel has no frames registered | check `ROBOT_FACE_ENABLE=1` and look for `weather: could not put the forecast card on the panel` |
| Card appears but the icons don't move | an unlisted face state holds frame 1 | `weather` must be in `face._TIMING` with `loop=True` — `tests/test_weather_screen.py` gates this |
| Text looks crude | no TrueType font found | `sudo apt install fonts-dejavu-core` |
| *"what's the weather"* gets a made-up answer | the phrase matched no trigger | add it to `ROBOT_WEATHER_TRIGGERS` (multi-word) |

Watch it live:

```bash
journalctl -u robot.service -f | grep -a 'weather'
./scripts/run_wake_tests.sh          # includes the four weather harnesses -- no network needed
```
