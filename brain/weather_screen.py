# brain/weather_screen.py -- composites the 3-day forecast CARD shown on the face panel.
#
# Input: one weather.WeatherService snapshot ({"days": [today, tomorrow, day_after], ...}).
# Output: a short list of finished RGB frames sized to the panel, which main.py hands to
# Face.set_frames("weather", frames). Nothing here talks to the network, the Face, or the config -- it is a
# pure function of (snapshot, width, height), which is also what makes it unit-testable.
#
# TWO THINGS DRIVE THE WHOLE DESIGN:
#
# 1. THE CHROME MUST BE PIXEL-IDENTICAL ACROSS FRAMES. The Turing panel is a SERIAL device: face.py diffs
#    consecutive frames and sends only the changed rectangles, falling back to a slow full-frame repaint
#    once more than ~70% of the screen changes. This card sits on screen for the entire drowse, so if the
#    labels or the temperatures wobbled by a pixel between frames the robot would repaint the whole panel
#    over a serial link several times a second, all night. So the chrome is rendered ONCE into a base image
#    and every frame is a copy of it with only the three icon boxes pasted over -- identical by
#    construction, not by care. icon_boxes() exports exactly those rectangles so the test can prove it.
#
# 2. THE ICONS ARE LONG ANIMATIONS PLAYED SLOWLY. The supplied gifs are 31-91 frames at ~30ms, i.e. 1-3
#    second loops authored for ~30fps. The panel runs at ROBOT_FACE_FPS (default 4, hard-capped at 10 on
#    the Turing). Showing frames 0..N-1 would therefore play the first quarter-second of a three-second
#    animation and loop that -- a twitch, not weather. Each icon is instead RESAMPLED across its whole
#    timeline into CARD_FRAMES steps, so the complete motion plays, just slower. Icons of different lengths
#    resample to the same count, which is also what keeps three columns in lockstep with no LCM arithmetic.
#
# PIL is imported lazily inside the functions (the face.py convention) so this module imports cleanly on a
# machine with no Pillow -- the feature then just never builds a card.

import logging
import os

log = logging.getLogger("robot.weather")

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_icons")

# How many frames one card loop has. At the effective 4 fps that is a 2-second cycle, which lands close to
# the 1-3s the source animations were authored at. Also the memory bill: 8 frames x panel size x 3 bytes.
CARD_FRAMES = 8

COL_LABELS = ("TODAY", "TOMORROW", "DAY AFTER")
NIGHT_COL_LABELS = ("TONIGHT", "TOMORROW", "DAY AFTER")

# What each condition is CALLED on the card. Deliberately separate from weather._CONDITION_WORDS, which is
# spoken prose ("sunny with showers") -- a screen wants one or two words that survive being shrunk to fit a
# third of a 480px panel. Longest here is "PARTLY CLOUDY"; the fitter handles it.
CONDITION_LABELS = {
    "Sunny":            "SUNNY",
    "PartlyCloudy":     "PARTLY CLOUDY",
    "Cloudy":           "CLOUDY",
    "SunnyRain":        "SUNNY SHOWERS",
    "PartlyCloudyRain": "SHOWERS",
    "CloudyRain":       "RAIN",
    "Thunderstorms":    "STORMS",
    # Temperature variants. These are labels for an ICON, not for a sky: when the Frost icon wins the
    # column, the text says FROST too. A frost picture over the word SUNNY reads as a mismatch at a
    # glance, and the sky is the less useful of the two facts on a morning you have to scrape a car.
    "Frost":            "FROST",
    "Hot":              "SCORCHER",
}

BG = (16, 18, 24)            # near-black, matching the procedural faces' dark aesthetic
LABEL_FG = (140, 158, 184)
TMAX_FG = (236, 244, 255)
TMIN_FG = (124, 146, 174)
CONDITION_FG = (255, 255, 255)
DIVIDER = (34, 38, 48)
# Night ink. Same layout, same pixel positions, cooler and dimmer -- a hallway panel showing bright white
# at 2am is the brightest object in the room, and this card is on screen for half the year in the dark.
NIGHT_LABEL_FG = (104, 122, 152)
NIGHT_TMAX_FG = (196, 212, 236)
NIGHT_TMIN_FG = (100, 120, 150)
NIGHT_CONDITION_FG = (206, 220, 240)

# Fonts: this repo commits none, and face.py hand-draws its "Zzz" strokes specifically to avoid the
# dependency. Raspberry Pi OS Bookworm ships DejaVu, so try that first and degrade to PIL's built-in.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

_icon_cache = {}             # (condition, side, n) -> [RGBA frames], bounded by 7 conditions x 1 layout
_font_cache = {}             # px -> font; the shrink-to-fit loop asks for many sizes, so don't re-open the
                             # TTF each time (it runs once per poll, but a file open per candidate px adds up)
_warned_icons = set()
_warned_font = [False]


def _font(size):
    """A bold font at `size` px, or PIL's default. Memoised. Never raises."""
    from PIL import ImageFont
    size = max(1, int(size))
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for path in _FONT_CANDIDATES:
        try:
            if os.path.exists(path):
                font = ImageFont.truetype(path, size)
                break
        except Exception:
            continue
    if font is None:
        if not _warned_font[0]:
            _warned_font[0] = True
            log.warning("weather: no TrueType font found (looked for DejaVu/Liberation/Noto) -- the "
                        "forecast card falls back to the built-in bitmap font, which looks cruder but works")
        try:
            font = ImageFont.load_default(size=size)      # Pillow >= 10.1 scales the built-in font
        except TypeError:
            font = ImageFont.load_default()               # older Pillow: fixed-size only
    _font_cache[size] = font
    return font


def _text_w(draw, text, font):
    try:
        l, _t, r, _b = draw.textbbox((0, 0), text, font=font)
        return r - l
    except Exception:
        return len(text) * 6                              # last-resort estimate; never raise over a label


def _fit_font(draw, text, max_w, start_px, min_px=6):
    """The largest font at or below start_px whose rendered `text` fits inside max_w.

    This is what makes the condition label's "always one line" promise true rather than hopeful. The labels
    range from "RAIN" to "PARTLY CLOUDY" -- more than 3x in width -- across panels from a 160px column to a
    267px one, so no single fixed size can be both readable on the short labels and safe on the long ones.
    Measure and shrink instead. Falls back to min_px rather than ever wrapping or overflowing."""
    size = max(int(min_px), int(start_px))
    while size > min_px:
        if _text_w(draw, text, _font(size)) <= max_w:
            return _font(size)
        size -= 1
    return _font(min_px)


def _layout(width, height):
    """Every number the card is drawn from, derived from the LIVE panel size.

    Never hardcode 480x320: the Turing landscape canvas is that, but a DSI framebuffer auto-sizes itself to
    whatever the panel reports (e.g. 800x480) and ignores the size env keys entirely."""
    col_w = width / 3.0
    side = int(min(col_w * 0.72, height * 0.37))          # icon box, square, fits both aspect ratios
    label_px = max(9, int(height * 0.072))
    tmax_px = max(14, int(height * 0.155))
    tmin_px = max(10, int(height * 0.080))
    cond_px = max(8, int(height * 0.062))                 # the STARTING size; _fit_font shrinks to fit
    icon_top = int(height * 0.155)
    tmax_y = icon_top + side + int(height * 0.030)
    tmin_y = tmax_y + int(tmax_px * 1.02)
    return {"col_w": col_w, "side": side, "label_px": label_px, "tmax_px": tmax_px,
            "tmin_px": tmin_px, "cond_px": cond_px, "icon_top": icon_top,
            "label_y": int(height * 0.040), "tmax_y": tmax_y, "tmin_y": tmin_y,
            # Clear the FULL height of the low-temperature text, then add a real gap. Written as
            # (tmin_y + tmin_px + gap) rather than a multiplier so the gap is a gap and not an accident of
            # font metrics: the previous 1.25x left about 6px on a 320px panel, so "10 degrees" and "RAIN"
            # read as one crowded block. There is ~50px of unused space below, so this costs nothing.
            "cond_y": tmin_y + tmin_px + int(height * 0.030),
            "cond_max_w": col_w * 0.92}                   # leave a gutter so labels never touch the divider


def card_columns(snapshot, at_night=False, roll=True):
    """The three columns to draw: [{label, condition, headline, sub}] -- a pure function, the test surface.

    THE EVENING ROLL. At 22:00 the `TODAY` column reports a day that finished hours ago: its max is
    history, and the only number still ahead of the viewer is the overnight low. After sunset the first
    column therefore becomes `TONIGHT` and leads with a minimum instead of a maximum.

    WHICH minimum, precisely. Open-Meteo's `temperature_2m_min` for a date is the minimum over that
    CALENDAR day, so today's row holds *this* morning's low -- also already history. The coming overnight
    low falls after midnight and therefore belongs to TOMORROW's row. That is what TONIGHT shows.
    Getting this wrong is the difference between "it'll drop to 8 tonight" and quoting a number from
    eighteen hours ago, which on a cold-front night is exactly when somebody cares.

    The condition and icon still come from TODAY's row -- tonight's sky follows today's weather, not
    tomorrow's.

    DELIBERATELY NOT DONE IN WeatherService.get(). That snapshot also feeds spoken_summary() and the
    greeting advice; rolling it there would make the robot say "Today it's 8 degrees and raining" about
    tomorrow, and advise on the wrong day. The roll is a DISPLAY decision and lives with the display.

    ONLY THE TONIGHT COLUMN IS "NIGHT". Tomorrow and the day after are DAYS -- rendering them with a moon
    because the viewer happens to be looking at 10pm would say the day after next is dark, which is
    nonsense. This is what every weather app does: the now/tonight cell gets the moon, future days keep
    their day icons. The dimmed night palette still applies to the whole card, which is what actually
    solves the glare; the moon is about meaning, not brightness.
    """
    days = (snapshot or {}).get("days") or []
    if len(days) < 3:
        return []
    if not (at_night and roll):
        # No TONIGHT column exists, so no column represents a night and none gets a night icon.
        return [{"label": COL_LABELS[i], "condition": days[i].get("condition"),
                 "headline": days[i].get("tmax"), "sub": days[i].get("tmin"),
                 "night": False, "day": days[i]}
                for i in range(3)]
    tonight_low = days[1].get("tmin")          # the coming overnight low -- see the docstring
    cols = [{"label": NIGHT_COL_LABELS[0], "condition": days[0].get("condition"),
             "headline": tonight_low, "sub": None, "night": True, "day": days[0]}]
    cols += [{"label": NIGHT_COL_LABELS[i], "condition": days[i].get("condition"),
              "headline": days[i].get("tmax"), "sub": days[i].get("tmin"),
              "night": False, "day": days[i]}
             for i in (1, 2)]
    return cols


def icon_boxes(width, height):
    """The three icon rectangles [(x, y, w, h)] -- the ONLY pixels that differ between card frames.
    Exported so the chrome-static test can mask them instead of re-deriving the layout."""
    L = _layout(width, height)
    side = L["side"]
    return [(int(i * L["col_w"] + (L["col_w"] - side) / 2.0), L["icon_top"], side, side)
            for i in range(3)]


def icon_candidates(col, frost_c=None, hot_c=None):
    """Ordered icon basenames to try for a column, most specific first, ALWAYS ending in the plain
    condition.

    That last element is the whole safety story: every variant is optional. Drop `Sunny-night.gif` into
    brain/weather_icons/ and clear nights start using it; delete it again and the card silently returns to
    `Sunny.gif`. Nothing needs a code change, a config change or a restart to add or remove artwork, and a
    half-supplied set can never produce a hole in the card.

    Order is deliberate: TEMPERATURE outranks time of day. A frosty night is more worth telling somebody
    about than the fact that it is dark -- they can see that it is dark."""
    out = []
    cond = col.get("condition")
    day = col.get("day") or {}
    try:
        if frost_c is not None and day.get("tmin") is not None and float(day["tmin"]) <= float(frost_c):
            out.append("Frost")
        if hot_c is not None and day.get("tmax") is not None and float(day["tmax"]) >= float(hot_c):
            out.append("Hot")
    except (TypeError, ValueError):
        pass
    if col.get("night") and cond:
        out.append(f"{cond}-night")
    if cond:
        out.append(cond)
    return out


def resolve_icon(candidates):
    """The first candidate whose gif actually exists on disk, or None.

    Separated from loading so the card can LABEL the column with whatever won: a Frost picture over the
    word SUNNY is a mismatch, and the label should tell the same story as the image."""
    for name in (candidates or []):
        if name and os.path.exists(os.path.join(ICON_DIR, f"{name}.gif")):
            return name
    return None


def _load_icon(condition, side, n):
    """`n` frames of the FIRST usable icon in `condition`, which may be a single basename or an ordered
    list of candidates (see icon_candidates).

    Only the resampled result is cached -- decoding a 91-frame gif costs ~100ms and happens once per poll,
    whereas holding every source frame of all seven conditions would be ~57 MB of RAM for no benefit.
    No candidate usable -> None, and the caller draws a labelled placeholder."""
    if isinstance(condition, (list, tuple)):
        for name in condition:
            frames = _load_icon(name, side, n)
            if frames is not None:
                return frames
        return None
    # A variant that simply has not been drawn yet is the NORMAL case, not an error: fall through to the
    # next candidate without warning, or the log would carry a line per poll for every icon nobody made.
    if not os.path.exists(os.path.join(ICON_DIR, f"{condition}.gif")):
        return None
    key = (condition, side, n)
    if key in _icon_cache:
        return _icon_cache[key]
    path = os.path.join(ICON_DIR, f"{condition}.gif")
    try:
        from PIL import Image, ImageSequence
        src = []
        with Image.open(path) as im:
            for fr in ImageSequence.Iterator(im):
                src.append(fr.convert("RGBA"))
        if not src:
            raise ValueError("no frames")
        out = []
        for i in range(n):
            fr = src[(i * len(src)) // n]                  # spread n samples over the whole loop
            out.append(_keyed(fr).resize((side, side)))
        _icon_cache[key] = out
        return out
    except Exception as e:
        if condition not in _warned_icons:
            _warned_icons.add(condition)
            log.warning("weather: icon %s unusable (%s) -- that column shows a text placeholder instead; "
                        "the rest of the card is unaffected", path, e)
        return None


def _keyed(frame):
    """Guarantee the frame has a usable alpha channel.

    Normally the gifs carry their own transparency and this is a no-op. But GIF decoding is a known Pillow
    version minefield -- some versions hand back later frames of a transparent gif as flat RGB, with the
    palette's transparent colour baked in. That would paste a solid green (or white) square onto a
    near-black card. So: if a frame arrives fully opaque yet all four corners are the same colour, treat
    that colour as the background and key it out. Cheap, and it fails safe in both directions."""
    from PIL import Image, ImageChops
    if frame.mode != "RGBA":
        frame = frame.convert("RGBA")
    px = frame.load()
    w, h = frame.size
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    if any(c[3] != 255 for c in corners):
        return frame                                       # already has transparency -- trust it
    if len(set(c[:3] for c in corners)) != 1:
        return frame                                       # busy edges: a real full-bleed image, leave it
    rgb = frame.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", frame.size, corners[0][:3])).convert("L")
    frame.putalpha(diff.point(lambda v: 0 if v == 0 else 255))
    return frame


def _placeholder(draw, box, condition):
    """What a missing icon looks like: a dim outlined box with the condition name, never an empty hole."""
    x, y, w, h = box
    draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=max(2, w // 12),
                           outline=(70, 80, 96), width=max(1, w // 40))
    f = _font(max(8, w * 0.14))
    words = _split_camel(condition)
    line_h = max(9, int(w * 0.17))
    top = y + (h - line_h * len(words)) / 2.0
    for i, word in enumerate(words):
        _centred(draw, x + w / 2.0, top + i * line_h, word, f, (110, 124, 146))


def _split_camel(name):
    """'PartlyCloudyRain' -> ['Partly', 'Cloudy', 'Rain'] so a placeholder wraps instead of overflowing."""
    out, cur = [], ""
    for ch in str(name):
        if ch.isupper() and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out or [str(name)]


def _centred(draw, cx, y, text, font, fill):
    """Draw `text` horizontally centred on cx with its top at y."""
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        draw.text((cx - (r - l) / 2.0 - l, y - t), text, font=font, fill=fill)
    except Exception:
        draw.text((cx, y), text, font=font, fill=fill)


def _temp(v):
    try:
        return f"{int(round(float(v)))}°"
    except (TypeError, ValueError):
        return "--"


def build_frames(snapshot, width, height, n=CARD_FRAMES, at_night=None, roll=None,
                 frost_c=None, hot_c=None):
    """snapshot -> a list of `n` finished RGB frames of exactly width x height, or [] if it can't be built.

    `at_night` / `roll` / `frost_c` / `hot_c` default to the configuration; pass them explicitly from
    tests. Never raises: this runs on the weather poll thread, whose loop would otherwise lose a cycle."""
    try:
        from PIL import Image, ImageDraw
        import weather                                       # for is_night + the knobs; lazy like PIL
        width, height, n = int(width), int(height), max(1, int(n))
        if at_night is None:
            at_night = weather.night_icons_enabled() and weather.is_night(snapshot)
        if roll is None:
            roll = weather.evening_roll_enabled()
        if frost_c is None:
            frost_c = weather.frost_c()
        if hot_c is None:
            hot_c = weather.scorcher_c()

        cols = card_columns(snapshot, at_night=at_night, roll=roll)
        if len(cols) < 3:
            return []
        L = _layout(width, height)
        boxes = icon_boxes(width, height)
        # At night the whole card cools down, not just the icons: a bright white 15 degrees on a hallway
        # panel at 2am is the brightest thing in the room. Same layout, same pixels, cooler ink.
        label_fg, tmax_fg, tmin_fg, cond_fg = ((NIGHT_LABEL_FG, NIGHT_TMAX_FG, NIGHT_TMIN_FG,
                                                NIGHT_CONDITION_FG) if at_night else
                                               (LABEL_FG, TMAX_FG, TMIN_FG, CONDITION_FG))

        # --- the static half, drawn exactly once ---
        base = Image.new("RGB", (width, height), BG)
        d = ImageDraw.Draw(base)
        f_label, f_max, f_min = _font(L["label_px"]), _font(L["tmax_px"]), _font(L["tmin_px"])
        for i in range(1, 3):                                    # column dividers
            x = int(i * L["col_w"])
            d.line([(x, int(height * 0.08)), (x, int(height * 0.94))], fill=DIVIDER, width=1)
        # The condition labels are sized ONCE, from the widest of the three, and that one size is used for
        # all three columns. Sizing each independently would fit just as well but look wrong -- "RAIN" would
        # render half again as large as "PARTLY CLOUDY" beside it. One size reads as a row.
        # Resolve the artwork FIRST, so the text can name what actually got drawn. A '-night' variant has
        # no label of its own and falls through to its base condition, which is right: a moon over the
        # word CLEAR is the same fact as a sun over it. A Frost or Hot win DOES relabel.
        won = [resolve_icon(icon_candidates(c, frost_c=frost_c, hot_c=hot_c)) for c in cols]
        conditions = [(CONDITION_LABELS.get(w) if w else None)
                      or CONDITION_LABELS.get(c.get("condition"))
                      or str(c.get("condition") or "").upper()
                      for w, c in zip(won, cols)]
        f_cond = _font(L["cond_px"])
        for text in conditions:
            if text:
                f_cond = min(f_cond, _fit_font(d, text, L["cond_max_w"], L["cond_px"]),
                             key=lambda f: getattr(f, "size", 0))

        for i, col in enumerate(cols):
            cx = (i + 0.5) * L["col_w"]
            _centred(d, cx, L["label_y"], col["label"], f_label, label_fg)
            _centred(d, cx, L["tmax_y"], _temp(col.get("headline")), f_max, tmax_fg)
            if col.get("sub") is not None:      # TONIGHT leads with the low and has no second number
                _centred(d, cx, L["tmin_y"], _temp(col["sub"]), f_min, tmin_fg)
            if conditions[i]:
                _centred(d, cx, L["cond_y"], conditions[i], f_cond, cond_fg)

        # --- the animated half: three icon strips, each already resampled to n frames ---
        strips = []
        for i, col in enumerate(cols):
            frames = _load_icon(won[i], boxes[i][2], n) if won[i] else None
            if frames is None:
                # placeholder is CHROME: draw it on base
                _placeholder(d, boxes[i], col.get("condition") or "?")
            strips.append(frames)

        out = []
        for k in range(n):
            frame = base.copy()
            for i, strip in enumerate(strips):
                if strip is None:
                    continue
                x, y, _w, _h = boxes[i]
                frame.paste(strip[k], (x, y), strip[k])           # alpha mask -> transparent gifs sit clean
            out.append(frame)
        return out
    except Exception as e:
        log.warning("weather: could not build the forecast card (%s) -- the sleep screen stays as it is", e)
        return []
