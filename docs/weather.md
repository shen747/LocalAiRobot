# Weather

The robot fetches a 3-day forecast and uses it three ways:

1. a **forecast card** (today, tomorrow, the day after, with animated icons and highs and lows) on the sleep
   screen, in place of the sleeping face;
2. **weather in greetings**: "Good morning, Ramesh! It's a very chilly day today. Make sure to pack your jumper.";
3. **real answers** to "what's the weather?", from the forecast rather than made up by the language model.

It's on by default and the only feature that needs the internet. Nothing else waits on it: with no network,
the robot simply leaves the weather out.

## Set your location

It ships pointed at the Melbourne city centre, a public landmark. Point it at yours: right-click your home in
any map app to get latitude and longitude, and keep two decimal places (about 1 km, plenty for a forecast):

```bash
sed -i -e 's|^ROBOT_WEATHER_LAT=.*|ROBOT_WEATHER_LAT=-37.87|' \
       -e 's|^ROBOT_WEATHER_LON=.*|ROBOT_WEATHER_LON=145.09|' config/robot.env
./scripts/run_me.sh --reload
journalctl -u robot.service -n 40 | grep -a weather    # "open-meteo forecast for -37.87,145.09 ..."
```

Outside Australia, also set `ROBOT_WEATHER_TZ` (for example `Europe/London`), or the day rolls over at the wrong
hour. `ROBOT_WEATHER_ENABLE=0` turns the whole feature off.

> **Privacy.** The shipped coordinates are a landmark on purpose. Put your own in `config/robot.env`, which git
> ignores, and use two decimal places (about 1 km) rather than your exact address. See
> [configuration.md](configuration.md#secrets).

The forecast comes from [Open-Meteo](https://open-meteo.com/), which needs no key or account; one robot uses
48 of its roughly 10,000 free daily requests. Weather data by Open-Meteo.com, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): keep that credit if you share pictures of the card.

## The card

Each column shows the day, an icon, the high and low, and the condition in a word or two (`SUNNY`, `SHOWERS`,
`STORMS`). After sunset the first column becomes **TONIGHT** with the overnight low and a moon icon, and the card
dims. `ROBOT_WEATHER_EVENING_ROLL=0` and `ROBOT_WEATHER_NIGHT_ICONS=0` turn those off.

The icons are GIFs in `brain/weather_icons/`, one per condition: `Sunny`, `PartlyCloudy`, `Cloudy`, `SunnyRain`,
`PartlyCloudyRain`, `CloudyRain` and `Thunderstorms`. Replace one by dropping in a file with the same name:
square, at least 128 px, on a transparent or dark background, any number of frames. Optional extras are used
when present: `<Condition>-night.gif` for TONIGHT, `Frost.gif` for a low at or below `ROBOT_WEATHER_FROST_C`, and
`Hot.gif` for a high at or above `ROBOT_WEATHER_SCORCHER_C`. Snow maps to the nearest rain icon.

The card only replaces the sleeping face, and goes back to it when the forecast is too old to trust.

## Greetings

A greeting is a time-of-day opener plus one piece of advice. Rain and storms come first; dry days go by
temperature: at or below `ROBOT_WEATHER_CHILLY_C` (15 °C) it suggests a jumper, at or above `ROBOT_WEATHER_HOT_C`
(30 °C) water and sunscreen, and anything in between is a pleasant day. There are 50 lines for each kind, in
`brain/weather.py`, where you can add your own (`[Name]` becomes the person's name). `ROBOT_WEATHER_GREETING=0`
turns off just the greetings. In Halloween mode the scare wins and no weather is added.

## Asking about the weather

Phrases such as "the weather", "will it rain" or "need an umbrella" get an answer read from the forecast:

> "Today it's 9 to 17 degrees and partly cloudy. Tomorrow, 8 to 15 and showers. Sunday, 18 to 31 and sunny."

`ROBOT_WEATHER_TRIGGERS` adds your own phrases (use several words: a bare `temperature` would also catch "what's
your CPU temperature"). A one-line summary of the forecast also goes to the model each turn, so "should I take a
jacket?" gets a sensible answer too. It works from the [phone remote](http_setup.md) as well.

## When the internet is down

| Situation | Card | Greetings | "What's the weather?" |
|---|---|---|---|
| fresh forecast | shown | opener and advice | full answer |
| offline, forecast under 6 h old | shown | opener and advice | answered from the saved forecast |
| forecast older than `ROBOT_WEATHER_STALE_S` (6 h) | sleeping face | opener only | "I can't reach the weather service right now." |
| no location set, or `ROBOT_WEATHER_ENABLE=0` | sleeping face | plain greeting | not answered |

The last good forecast is saved, so a reboot during an outage still has one. A saved forecast for different
coordinates is ignored.

## Troubleshooting

| Log or symptom | Fix |
|---|---|
| `weather: ROBOT_WEATHER_LAT/LON not set` | set both, then reload |
| `weather: timezone ... unusable` | `sudo apt install tzdata` |
| `provider HTTP 400 ... Invalid timezone` | use an exact name such as `Australia/Melbourne` |
| no card, but weather greetings work | check `ROBOT_FACE_ENABLE=1`, and look for `could not put the forecast card on the panel` |
| plain-looking text on the card | `sudo apt install fonts-dejavu-core` |
| "what's the weather" gets a made-up answer | add your phrase to `ROBOT_WEATHER_TRIGGERS` |

Another forecast source is one class in `brain/weather.py` (next to `_OpenMeteoProvider`) plus an entry in
`_PROVIDERS`, selected with `ROBOT_WEATHER_PROVIDER`.
