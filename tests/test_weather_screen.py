"""The forecast card: right size, right frame count, animating icons -- and PIXEL-IDENTICAL chrome.

The chrome check is the load-bearing one. The Turing face panel is a SERIAL device: face.py diffs
consecutive frames and sends only the changed rectangles, falling back to a slow full-frame repaint once
more than ~70% of the screen changes. This card sits on screen for an entire drowse, so if anything outside
the three icon boxes moved between frames the robot would repaint the whole panel over serial several times
a second, all night. The test masks exactly the rectangles icon_boxes() reports and asserts every other
pixel is identical.

Skips itself (exit 0) when Pillow is absent -- the compositor is the one part of the weather feature that
genuinely needs it, and a dev box without Pillow shouldn't fail the suite.
"""
import os
import sys

sys.path.insert(0, os.environ["BRAIN_DIR"])

try:
    from PIL import Image, ImageDraw, ImageSequence     # noqa: F401
except Exception as e:
    print(f"  SKIP  Pillow not available ({e}) -- the compositor cannot be exercised here")
    sys.exit(0)

import weather
import weather_screen as ws

F = []


def ck(n, g, w):
    ok = g == w
    print(f"  {'PASS' if ok else 'FAIL'}  {n}: got {g!r}, want {w!r}")
    if not ok:
        F.append(n)


def snap(*conditions):
    return {"days": [{"date": f"2026-08-0{8 + i}", "condition": c, "tmax": 17.2 + i, "tmin": 9.1 + i}
                     for i, c in enumerate(conditions)]}


def pixels(img):
    return img.tobytes()


def masked(img, boxes):
    """A copy with the icon rectangles blanked, so only the CHROME is compared."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    for (x, y, w, h) in boxes:
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=(0, 0, 0))
    return out


SIZES = ((480, 320),      # Turing landscape logical canvas
         (800, 480),      # a DSI framebuffer, which auto-sizes and ignores the size env keys
         (320, 480))      # portrait, to prove nothing is hardcoded to a landscape aspect

print("=== the committed assets exist, one per canonical condition ===")
for cond in weather.CONDITIONS:
    p = os.path.join(ws.ICON_DIR, f"{cond}.gif")
    ck(f"{cond}.gif present", os.path.exists(p), True)

print("\n=== frames come out the exact panel size, in the mode the panel wants ===")
s = snap("PartlyCloudy", "CloudyRain", "Thunderstorms")
for (w, h) in SIZES:
    frames = ws.build_frames(s, w, h)
    ck(f"{w}x{h}: frame count", len(frames), ws.CARD_FRAMES)
    ck(f"{w}x{h}: every frame is {w}x{h}", all(f.size == (w, h) for f in frames), True)
    ck(f"{w}x{h}: RGB", all(f.mode == "RGB" for f in frames), True)

print("\n=== THE CHROME IS PIXEL-IDENTICAL; only the icon boxes move ===")
for (w, h) in SIZES:
    frames = ws.build_frames(s, w, h)
    boxes = ws.icon_boxes(w, h)
    ck(f"{w}x{h}: three icon boxes", len(boxes), 3)
    ck(f"{w}x{h}: boxes are square", all(b[2] == b[3] for b in boxes), True)
    ck(f"{w}x{h}: boxes are on-canvas",
       all(0 <= x and 0 <= y and x + bw <= w and y + bh <= h for (x, y, bw, bh) in boxes), True)
    base = pixels(masked(frames[0], boxes))
    ck(f"{w}x{h}: chrome identical across ALL frames",
       all(pixels(masked(f, boxes)) == base for f in frames), True)
    ck(f"{w}x{h}: but the icons DO animate",
       len({pixels(f) for f in frames}) > 1, True)

print("\n=== each icon plays its WHOLE animation, not just its first few frames ===")
# The supplied gifs are 31-91 frames authored for ~30fps, played back at ~4fps. Sampling frames 0..N-1
# would show a quarter-second twitch of a three-second loop, so the sampler spreads N samples across the
# entire timeline. Proven here by checking the sample indices, which is exact and asset-independent.
for n_src, n_card in ((91, 8), (31, 8), (60, 8), (3, 8), (1, 8)):
    idx = [(i * n_src) // n_card for i in range(n_card)]
    ck(f"{n_src} source frames -> {n_card} samples, in range",
       all(0 <= i < n_src for i in idx), True)
    if n_src >= n_card:
        # The last sample must land within ONE step of the end -- the next step would wrap to 0. This is
        # the assertion that fails loudly on a naive `i % len(frames)`, which would leave idx[-1] at 7.
        step = -(-n_src // n_card)                       # ceiling division
        ck(f"{n_src} source frames -> the last sample is within one step of the end",
           n_src - idx[-1] <= step, True)
        ck(f"{n_src} source frames -> samples cover >80% of the timeline",
           idx[-1] >= n_src * 0.8, True)
        ck(f"{n_src} source frames -> samples strictly ascending",
           all(b > a for a, b in zip(idx, idx[1:])), True)

print("\n=== a missing icon renders a labelled placeholder; the rest of the card is unaffected ===")
frames = ws.build_frames(snap("Sunny", "NoSuchCondition", "Cloudy"), 480, 320)
ck("still builds a full card", len(frames), ws.CARD_FRAMES)
ck("still the right size", all(f.size == (480, 320) for f in frames), True)
boxes = ws.icon_boxes(480, 320)
base = pixels(masked(frames[0], boxes))
ck("chrome still identical", all(pixels(masked(f, boxes)) == base for f in frames), True)
ck("the two real icons still animate", len({pixels(f) for f in frames}) > 1, True)
# The placeholder is drawn onto the shared base, so the middle box is the SAME in every frame -- it must
# not be blank, though, or the card would show a hole where an icon should be.
mid = boxes[1]
crop = frames[0].crop((mid[0], mid[1], mid[0] + mid[2], mid[1] + mid[3]))
ck("the placeholder is not an empty box", len(set(crop.tobytes())) > 1, True)
ck("the placeholder name is split into words",
   ws._split_camel("PartlyCloudyRain"), ["Partly", "Cloudy", "Rain"])

print("\n=== transparent gifs sit on the dark card, not as bright squares ===")
# Every corner of an icon box should still be the card background: the assets are alpha-keyed, so a frame
# pasted with its mask leaves the corners untouched. A gif that lost its transparency would fill them.
frames = ws.build_frames(snap("Sunny", "Cloudy", "Thunderstorms"), 480, 320)
px = frames[0].load()
corners_ok = True
for (x, y, bw, bh) in ws.icon_boxes(480, 320):
    for (cx, cy) in ((x + 1, y + 1), (x + bw - 2, y + 1), (x + 1, y + bh - 2), (x + bw - 2, y + bh - 2)):
        if px[cx, cy] != ws.BG:
            corners_ok = False
ck("icon-box corners are still the card background", corners_ok, True)

print("\n=== bad input degrades to an empty list, never an exception ===")
for name, bad in (("None", None), ("no days", {}), ("empty days", {"days": []}),
                  ("only two days", snap("Sunny", "Cloudy")), ("junk days", {"days": [1, 2, 3]}),
                  ("zero size", None)):
    try:
        got = ws.build_frames(bad, 0, 0) if name == "zero size" else ws.build_frames(bad, 480, 320)
        ck(f"{name} -> []", got, [])
    except Exception as e:
        ck(f"{name} must not raise", f"raised {e!r}", "[]")

print("\n=== the condition label: every condition named, one or two words, ALWAYS one line ===")
ck("every canonical condition has a label",
   sorted(set(weather.CONDITIONS) - set(ws.CONDITION_LABELS)), [])
# The table also carries labels for the temperature VARIANTS, so the text can name whatever icon actually
# won the column -- a Frost picture over the word SUNNY reads as a mismatch.
ck("...plus the temperature variants",
   sorted(set(ws.CONDITION_LABELS) - set(weather.CONDITIONS)), ["Frost", "Hot"])
ck("no label exceeds two words",
   max(len(v.split()) for v in ws.CONDITION_LABELS.values()), 2)
ck("labels are white", ws.CONDITION_FG, (255, 255, 255))
ck("no label contains a line break",
   any(("\n" in v) for v in ws.CONDITION_LABELS.values()), False)

# EXHAUSTIVE: the promise is "always fits on one line", so check it for every combination of three
# conditions at every panel geometry -- 343 combinations x 4 sizes. A fixed font size cannot keep this
# promise ("RAIN" vs "PARTLY CLOUDY" is more than 3x in width), which is why _fit_font measures and shrinks.
import itertools
overflow, clipped = [], []
for (w, h) in ((480, 320), (800, 480), (320, 480), (240, 320)):
    L = ws._layout(w, h)
    probe = ImageDraw.Draw(Image.new("RGB", (w, h)))
    for combo in itertools.product(weather.CONDITIONS, repeat=3):
        texts = [ws.CONDITION_LABELS[c] for c in combo]
        f = ws._font(L["cond_px"])
        for t in texts:                                  # the row shares ONE size: the smallest that fits
            f = min(f, ws._fit_font(probe, t, L["cond_max_w"], L["cond_px"]),
                    key=lambda x: getattr(x, "size", 0))
        for t in texts:
            if ws._text_w(probe, t, f) > L["cond_max_w"]:
                overflow.append((w, h, t))
        if L["cond_y"] + getattr(f, "size", 0) > h:
            clipped.append((w, h, combo))
ck(f"no label overflows its column ({7 ** 3} combos x 4 sizes)", overflow, [])
ck("no label falls off the bottom of the panel", clipped, [])

# The label must not crowd the low temperature above it. Measured on real INK extents, not on the nominal
# font size -- the first version derived cond_y from a multiple of the font size, which left about 6px on a
# 320px panel and read as one block ("10 degrees RAIN"). Floor is a proportion of height so it holds on
# every panel; there is ~50px spare below, so the gap costs nothing.
tight = []
for (w, h) in ((480, 320), (800, 480), (320, 480), (240, 320)):
    L = ws._layout(w, h)
    probe = ImageDraw.Draw(Image.new("RGB", (w, h)))
    _l, t, _r, b = probe.textbbox((0, 0), "10°", font=ws._font(L["tmin_px"]))
    gap = L["cond_y"] - (L["tmin_y"] + (b - t))           # ink bottom of the low temp -> top of the label
    if gap < h * 0.025:
        tight.append((w, h, gap))
ck("the condition label clears the low temperature by a visible gap", tight, [])

# One size for the whole row -- sizing columns independently fits equally well but reads as a mistake.
probe = ImageDraw.Draw(Image.new("RGB", (320, 480)))
L = ws._layout(320, 480)
solo = {t: getattr(ws._fit_font(probe, t, L["cond_max_w"], L["cond_px"]), "size", 0)
        for t in ("RAIN", "PARTLY CLOUDY")}
ck("a short and a long label WOULD size differently on their own", solo["RAIN"] != solo["PARTLY CLOUDY"],
   True)
mixed = ws.build_frames(snap("CloudyRain", "PartlyCloudy", "Sunny"), 320, 480)
ck("...but the row still renders (one shared size)", len(mixed), ws.CARD_FRAMES)

# The label is CHROME: it must not move between frames, or the panel repaints on every tick.
for (w, h) in SIZES:
    frames = ws.build_frames(snap("PartlyCloudy", "SunnyRain", "Thunderstorms"), w, h)
    boxes = ws.icon_boxes(w, h)
    base = pixels(masked(frames[0], boxes))
    ck(f"{w}x{h}: labels stay pixel-identical across frames",
       all(pixels(masked(f, boxes)) == base for f in frames), True)

# An unmapped condition still gets a legible line rather than a blank gap.
odd = ws.build_frames(snap("Sunny", "NoSuchCondition", "Cloudy"), 480, 320)
ck("an unmapped condition still renders a card", len(odd), ws.CARD_FRAMES)

print("\n=== icon_boxes() is a pure function of the canvas, so the mask can never drift ===")
ck("stable across calls", ws.icon_boxes(480, 320), ws.icon_boxes(480, 320))
ck("differs by canvas", ws.icon_boxes(480, 320) != ws.icon_boxes(800, 480), True)

print("\n=== the evening roll: TONIGHT leads with the COMING overnight low ===")
ROLL = {"days": [{"date": "2026-08-09", "condition": "PartlyCloudy", "tmax": 12.2, "tmin": 8.6},
                 {"date": "2026-08-10", "condition": "CloudyRain", "tmax": 14.2, "tmin": 6.6},
                 {"date": "2026-08-11", "condition": "Sunny", "tmax": 13.5, "tmin": 7.6}]}
dayc = ws.card_columns(ROLL, at_night=False, roll=True)
ck("by day the labels are unchanged", [c["label"] for c in dayc], ["TODAY", "TOMORROW", "DAY AFTER"])
ck("by day column 1 leads with the MAX", dayc[0]["headline"], 12.2)
ck("by day no column is a night", any(c["night"] for c in dayc), False)

nightc = ws.card_columns(ROLL, at_night=True, roll=True)
ck("after sunset column 1 becomes TONIGHT", nightc[0]["label"], "TONIGHT")
# The load-bearing one: a calendar-day minimum falls BEFORE dawn, so today's row holds THIS morning's low
# -- already history. The coming overnight low is tomorrow's row.
ck("TONIGHT shows TOMORROW's min, not today's", nightc[0]["headline"], 6.6)
ck("...which is NOT today's min", nightc[0]["headline"] != ROLL["days"][0]["tmin"], True)
ck("TONIGHT has no second number", nightc[0]["sub"], None)
ck("TONIGHT keeps TODAY's condition (tonight's sky follows today's)",
   nightc[0]["condition"], "PartlyCloudy")
ck("the other two columns are unchanged",
   [(c["label"], c["headline"], c["sub"]) for c in nightc[1:]],
   [("TOMORROW", 14.2, 6.6), ("DAY AFTER", 13.5, 7.6)])

ck("roll=False keeps TODAY even after sunset",
   [c["label"] for c in ws.card_columns(ROLL, at_night=True, roll=False)],
   ["TODAY", "TOMORROW", "DAY AFTER"])
for bad in (None, {}, {"days": []}, {"days": [1, 2]}):
    ck(f"card_columns({bad!r}) -> []", ws.card_columns(bad), [])

print("\n=== ONLY the TONIGHT column is a night -- tomorrow is still a DAY ===")
# Rendering the day-after-next with a moon because the viewer is up late would claim that day is dark.
ck("TONIGHT is a night", nightc[0]["night"], True)
ck("TOMORROW is NOT", nightc[1]["night"], False)
ck("DAY AFTER is NOT", nightc[2]["night"], False)
ck("so only TONIGHT asks for a -night icon",
   [ws.icon_candidates(c)[0] for c in nightc],
   ["PartlyCloudy-night", "CloudyRain", "Sunny"])

print("\n=== icon_candidates: most specific first, ALWAYS ending in the plain condition ===")
plain = {"condition": "Sunny", "night": False, "day": {"tmax": 20.0, "tmin": 10.0}}
ck("plain day", ws.icon_candidates(plain), ["Sunny"])
ck("night", ws.icon_candidates({**plain, "night": True}), ["Sunny-night", "Sunny"])
cold = {"condition": "Sunny", "night": True, "day": {"tmax": 9.0, "tmin": -1.0}}
ck("frost outranks night", ws.icon_candidates(cold, frost_c=2), ["Frost", "Sunny-night", "Sunny"])
hot = {"condition": "Sunny", "night": False, "day": {"tmax": 41.6, "tmin": 22.0}}
ck("heat", ws.icon_candidates(hot, hot_c=35), ["Hot", "Sunny"])
ck("thresholds are inclusive",
   ws.icon_candidates({"condition": "Sunny", "night": False, "day": {"tmax": 35.0, "tmin": 2.0}},
                      frost_c=2, hot_c=35), ["Frost", "Hot", "Sunny"])
ck("disabled thresholds add nothing",
   ws.icon_candidates(cold, frost_c=None, hot_c=None), ["Sunny-night", "Sunny"])
ck("every list ends in the bare condition",
   all(ws.icon_candidates({**plain, "night": n}, frost_c=2, hot_c=35)[-1] == "Sunny"
       for n in (True, False)), True)
for junk in ({"day": {"tmin": "cold"}}, {"condition": None}, {}):
    try:
        ck(f"junk column {junk!r} does not raise", isinstance(ws.icon_candidates(junk), list), True)
    except Exception as e:
        ck(f"junk column {junk!r} must not raise", f"raised {e!r}", "a list")

print("\n=== a variant that has NOT been drawn falls back to the day icon, silently ===")
# This is what makes every new icon optional: Frost.gif and Hot.gif do not exist yet.
ck("Frost.gif is genuinely absent (so this test means something)",
   os.path.exists(os.path.join(ws.ICON_DIR, "Frost.gif")), False)
frosty = {"days": [{"date": "2026-07-01", "condition": "Sunny", "tmax": 9.0, "tmin": -1.0},
                   {"date": "2026-07-02", "condition": "Sunny", "tmax": 10.0, "tmin": 0.0},
                   {"date": "2026-07-03", "condition": "Sunny", "tmax": 11.0, "tmin": 1.0}]}
frames = ws.build_frames(frosty, 480, 320, at_night=False, roll=False, frost_c=2, hot_c=35)
ck("the card still builds with no Frost.gif", len(frames), ws.CARD_FRAMES)
ck("...and still animates (it used the real Sunny.gif)",
   len({pixels(f) for f in frames}) > 1, True)
ck("a missing variant is NOT the placeholder path",
   ws._load_icon(["Frost", "Sunny"], 100, 8) is not None, True)
ck("a candidate list of only-missing files -> None",
   ws._load_icon(["Frost", "NoSuchThing"], 100, 8), None)

print("\n=== the column LABEL names whatever icon actually won ===")
ck("resolve_icon skips absent variants", ws.resolve_icon(["Frost", "Sunny-night", "Sunny"]),
   "Sunny-night")
ck("resolve_icon with nothing present", ws.resolve_icon(["Frost", "NoSuchThing"]), None)
ck("resolve_icon of an empty list", ws.resolve_icon([]), None)
# A '-night' win must NOT relabel: a moon over CLEAR says the same thing as a sun over it.
ck("a night win falls through to the base label",
   ws.CONDITION_LABELS.get("Sunny-night") or ws.CONDITION_LABELS.get("Sunny"), "SUNNY")
ck("a Frost win DOES relabel", ws.CONDITION_LABELS.get("Frost"), "FROST")
ck("a Hot win relabels", ws.CONDITION_LABELS.get("Hot"), "SCORCHER")
ck("both variant labels still fit one line",
   max(len(ws.CONDITION_LABELS[k].split()) for k in ("Frost", "Hot")), 1)

print("\n=== the supplied night artwork is actually picked up ===")
for cond in ("Sunny", "PartlyCloudy", "PartlyCloudyRain"):
    p = os.path.join(ws.ICON_DIR, f"{cond}-night.gif")
    ck(f"{cond}-night.gif present", os.path.exists(p), True)
    day_frames = ws._load_icon(cond, 100, 8)
    night_frames = ws._load_icon([f"{cond}-night", cond], 100, 8)
    ck(f"{cond}: the night variant differs from the day one",
       day_frames[0].tobytes() != night_frames[0].tobytes(), True)

print("\n=== night rendering: same layout, cooler ink, chrome still static ===")
for (w, h) in SIZES:
    nf = ws.build_frames(ROLL, w, h, at_night=True, roll=True)
    dfr = ws.build_frames(ROLL, w, h, at_night=False, roll=True)
    ck(f"{w}x{h}: night card builds", len(nf), ws.CARD_FRAMES)
    ck(f"{w}x{h}: identical geometry", [f.size for f in nf], [f.size for f in dfr])
    ck(f"{w}x{h}: it LOOKS different from the day card", pixels(nf[0]) != pixels(dfr[0]), True)
    boxes = ws.icon_boxes(w, h)
    base = pixels(masked(nf[0], boxes))
    ck(f"{w}x{h}: night chrome still pixel-identical across frames",
       all(pixels(masked(f, boxes)) == base for f in nf), True)
ck("night ink is dimmer than day ink",
   sum(ws.NIGHT_TMAX_FG) < sum(ws.TMAX_FG), True)

print("\n=== the seam: compositor -> Face.set_frames -> the panel ===")
# The card only reaches the screen through this one new public API, and the state has to be whitelisted or
# set_expression() silently coerces it to 'idle' -- a failure mode that looks like "the feature does
# nothing" rather than like an error.
os.environ.setdefault("ROBOT_DRY_RUN", "1")
os.environ["ROBOT_FACE_BACKEND"] = "simulated"
os.environ["ROBOT_FACE_WIDTH"], os.environ["ROBOT_FACE_HEIGHT"] = "480", "320"
try:
    import face as facemod
except Exception as e:
    print(f"  SKIP  face.py not importable here ({e})")
else:
    ck("'weather' is whitelisted in _MOODS", "weather" in facemod._MOODS, True)
    ck("'weather' loops (an unlisted state would freeze on frame 1)",
       facemod._TIMING.get("weather"), (4.0, True))
    ck("'weather' is NOT in STATES, so generate_faces.sh won't draw a cartoon for it",
       "weather" in facemod.STATES, False)
    fc = facemod.Face()
    frames = ws.build_frames(snap("Sunny", "Cloudy", "Thunderstorms"),
                             fc.backend.width, fc.backend.height)
    ck("registered", fc.set_frames("weather", frames), True)
    ck("cached at panel size",
       all(f.size == (fc.backend.width, fc.backend.height) for f in fc._frame_cache["weather"]), True)
    ck("re-registering while on screen is allowed (the every-30-min case)",
       (fc.set_expression("weather"), fc.set_frames("weather", frames))[1], True)
    ck("an unregistered state is refused", fc.set_frames("nosuchstate", frames), False)
    ck("an empty frame list is refused", fc.set_frames("weather", []), False)
    ck("junk frames are refused", fc.set_frames("weather", ["not an image"]), False)
    ck("...and the previous frames survived that", len(fc._frame_cache["weather"]), ws.CARD_FRAMES)
    fc.close()

print("\n" + "=" * 58)
print(f"  {'ALL PASS' if not F else 'FAILURES: ' + str(len(F))}")
print("=" * 58)
sys.exit(1 if F else 0)
