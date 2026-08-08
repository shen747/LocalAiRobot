# brain/social.py -- Social Memory: greetings that know they have met you before.
#
# The gap this closes. The concierge already recognises people and already has a RAG, but the two never met:
# seeing someone at 8am and again at 4pm produced two independent random greetings, as though the morning
# had not happened. This module turns "who is this" into "who is this, when did I last see them, and what
# did they tell me" -- and composes a sentence from it.
#
# WHAT IT IS NOT. There is no LLM on this path. A greeting has to be spoken within a second or two of
# somebody walking up, and say() blocks through Piper + aplay, so every decision here is a dict lookup, an
# indexed SELECT and a random.choice. The learning half (brain/main.py's post-conversation extractor) is
# where a model is involved, and that runs on a daemon thread after everyone has stopped talking.
#
# COMPOSITION RULE: opener + AT MOST ONE follow-on clause. Never three. A greeting that says hello, asks
# about school AND warns about the rain is not warm, it is a robot reading a checklist -- and the reason
# the buckets below are ordered is so the most specific thing available wins that one slot.
#
# ON BY DEFAULT. That is only defensible because the degradation ladder is real and tested: no sighting
# history falls back to the weather greeting, no forecast to a time-of-day one, no notes to no callback,
# and any exception at all to the plain greeting. The worst outcome of a bug in this file is therefore a
# more ordinary hello, not a silent robot -- which is exactly the property that lets it ship on rather than
# behind a soak flag. ROBOT_SOCIAL_MEMORY=0 restores the previous behaviour byte for byte.

import logging
import os
import random
import time
from datetime import date, datetime, timedelta

import weather

log = logging.getLogger("robot.social")

_TRUE = {"1", "true", "yes", "on"}


def _env(key, default=""):
    return (os.environ.get(key, "") or default).strip()


def _envf(key, default):
    try:
        return float(os.environ.get(key, "") or default)
    except (TypeError, ValueError):
        return float(default)


def enabled():
    """Master switch, ON by default. ROBOT_SOCIAL_MEMORY=0 restores today's behaviour byte for byte.

    The code default matches the shipped template rather than being the safe-side 0, so the feature is on
    even for a robot whose robot.env predates these keys -- "on by default" that a missing key silently
    disables is not on by default. Every seam still degrades (no history -> the weather greeting, no
    weather -> time-of-day, a broken query -> the plain greeting), so the failure mode of being wrong
    about this is a plainer hello, not a broken one."""
    return _env("ROBOT_SOCIAL_MEMORY", "1").lower() in _TRUE


def curiosity_enabled():
    """The ask-what-they-do-for-a-living loop. Also on by default.

    Kept as its OWN key because it is the one part of Social Memory that makes the robot INITIATE rather
    than respond -- if anything here is going to feel intrusive it is this, and it should be switchable
    without giving up the memory itself."""
    return _env("ROBOT_SOCIAL_CURIOSITY", "1").lower() in _TRUE


# --- time bucketing --------------------------------------------------------------------------------------
# Buckets are computed on CALENDAR DAYS in the configured timezone, not on elapsed hours. "Earlier today"
# has to mean today: seen at 23:00 and again at 00:30 is a new day and a different greeting, even though
# only 90 minutes passed, and seen at 08:00 and 20:00 is still the same day at 12 hours apart.

FIRST, TODAY, YESTERDAY, THIS_WEEK, LONG_GAP = "first", "today", "yesterday", "week", "long"


def bucket(last_seen_ts, now_ts=None, tz=None):
    """Classify the gap since a previous sighting. `last_seen_ts` is a unix epoch, or None for never."""
    if not last_seen_ts:
        return FIRST
    try:
        zone = weather._tzinfo(tz)
        now_dt = datetime.fromtimestamp(now_ts if now_ts else time.time(), zone)
        then_dt = datetime.fromtimestamp(float(last_seen_ts), zone)
        days = (now_dt.date() - then_dt.date()).days
        if days <= 0:
            return TODAY
        if days == 1:
            return YESTERDAY
        return LONG_GAP if days >= max(1.0, _envf("ROBOT_SOCIAL_LONG_GAP_D", 14)) else THIS_WEEK
    except Exception as e:
        log.debug("social: bucket failed (%s)", e)
        return FIRST


def end_of_day_ts(now_ts=None, tz=None):
    """Midnight tonight in the configured zone, as a unix epoch -- the expiry stamped on a 'plan' note.

    A plan is about TODAY. Expiring it at the day boundary is what stops "how was school?" surfacing on a
    Sunday in July because somebody mentioned school once in March."""
    try:
        zone = weather._tzinfo(tz)
        now_dt = datetime.fromtimestamp(now_ts if now_ts else time.time(), zone)
        midnight = datetime.combine(now_dt.date() + timedelta(days=1),
                                    datetime.min.time(), tzinfo=zone)
        return midnight.timestamp()
    except Exception as e:
        log.debug("social: end_of_day failed (%s)", e)
        return (now_ts or time.time()) + 43200.0        # 12h: wrong, but bounded and never permanent


# --- Array 1: first-ever sighting ([Name]) ---
OPENERS_FIRST = [
    "Well -- [Name]! Lovely to finally meet you properly.",
    "[Name]! Good to put a face to the name at last.",
    "Ah, [Name] -- our first proper hello. Welcome!",
    "Hello, [Name]! I don't think we've met before.",
    "[Name]! Delighted to meet you.",
    "Well hello, [Name] -- a new face. Welcome!",
    "Ah, so you're [Name]. Lovely to meet you.",
    "[Name]! Nice to meet you at last.",
    "Hello there, [Name] -- first time I've seen you. Welcome!",
    "Well, [Name]! Pleased to meet you properly.",
    "[Name] -- lovely to meet you. Do make yourself at home.",
    "Ah, [Name]! A new face around here. Hello!",
    "Hello, [Name]! I've been looking forward to this.",
    "[Name]! Good to meet you finally.",
    "Well met, [Name] -- welcome!",
]

# --- Array 2: seen earlier the SAME day ([Name]) ---
OPENERS_TODAY = [
    "Nice to see you again, [Name]! How's your day been so far?",
    "Back again, [Name]! How's the day treating you?",
    "Hello again, [Name]! Everything going well?",
    "Ah, [Name] -- twice in one day. How's it going?",
    "Good to see you again, [Name]. How's your day been?",
    "Hello again, [Name]! How are things?",
    "[Name], back so soon! How's the day going?",
    "Twice in a day, [Name] -- I am honoured. How's it been?",
    "Hello again, [Name]. Day going alright?",
    "Ah, you're back, [Name]! How has it been?",
    "Nice to see you again so soon, [Name].",
    "Hello once more, [Name]! Everything alright?",
    "Back again, [Name] -- good. How's your day shaping up?",
    "There you are again, [Name]! How goes it?",
    "Good to have you back, [Name]. How's the day been?",
]

# --- Array 3: seen YESTERDAY ([Name]) ---
OPENERS_YESTERDAY = [
    "Good to see you again, [Name]!",
    "Hello again, [Name] -- good to see you.",
    "[Name]! Back again, I see.",
    "Nice to see you, [Name].",
    "Ah, [Name] -- good to see you again.",
    "Hello, [Name]! Good to have you by again.",
    "[Name]! Lovely to see you again.",
    "Welcome back, [Name].",
    "Good to see you, [Name] -- it's been a day.",
    "Hello again, [Name]! How are you?",
    "There you are, [Name]. Good to see you.",
    "[Name]! Nice to have you back.",
    "Ah, welcome back, [Name].",
    "Good to see you again so soon, [Name].",
    "Hello, [Name] -- back again. Lovely.",
]

# --- Array 4: seen within the last couple of weeks ([Name]) ---
OPENERS_THIS_WEEK = [
    "Good to see you again, [Name]!",
    "[Name]! Nice to see you back.",
    "Hello, [Name] -- good to see you.",
    "Ah, [Name]! Welcome back.",
    "Lovely to see you again, [Name].",
    "[Name]! Good to have you round again.",
    "Hello again, [Name]. How have you been?",
    "Welcome back, [Name] -- good to see you.",
    "Ah, there you are, [Name]. Nice to see you.",
    "[Name]! Been a few days. Good to see you.",
    "Hello, [Name]! Nice to have you back.",
    "Good to see you, [Name]. How have things been?",
    "[Name] -- welcome back!",
    "Ah, [Name]! Good to see you again.",
    "Hello again, [Name] -- it's been a little while.",
]

# --- Array 5: a long gap (>= ROBOT_SOCIAL_LONG_GAP_D) ([Name]) ---
OPENERS_LONG_GAP = [
    "It's been a while, [Name] -- good to see you!",
    "[Name]! Long time no see. How have you been?",
    "Well, [Name]! It's been ages. Lovely to see you.",
    "Ah, [Name] -- a face I haven't seen in a while. Welcome back!",
    "[Name]! Where have you been hiding? Good to see you.",
    "It has been a while, [Name]. Good to have you back.",
    "Well hello, [Name] -- it's been far too long!",
    "[Name]! Wonderful to see you after all this time.",
    "Ah, [Name], a proper stranger lately. Good to see you!",
    "It's been a good while, [Name] -- welcome back.",
    "[Name]! Lovely to see you again after so long.",
    "Well, look who it is -- [Name]! It's been ages.",
    "Ah, [Name]! I was beginning to wonder. Good to see you.",
    "Long time, [Name] -- very good to see you.",
    "[Name]! It's been a while. How have you been keeping?",
]

_OPENERS = {
    FIRST: OPENERS_FIRST,
    TODAY: OPENERS_TODAY,
    YESTERDAY: OPENERS_YESTERDAY,
    THIS_WEEK: OPENERS_THIS_WEEK,
    LONG_GAP: OPENERS_LONG_GAP,
}

# Buckets whose opener ALREADY ends in a question. They must not also get a plan callback or weather advice
# -- two questions in one greeting is an interrogation, and the composition rule is one follow-on at most.
_ASKS_ALREADY = {TODAY}

# --- Array 6: asking how a stated plan went ({plan} = a short noun phrase from the extractor) ---
PLAN_CALLBACKS = [
    "How was {plan}?",
    "How did {plan} go?",
    "How was your {plan}, then?",
    "So -- how was {plan}?",
    "Tell me, how did {plan} go?",
    "How did you get on at {plan}?",
    "And how was {plan} today?",
    "Did {plan} go alright?",
    "How was {plan} in the end?",
    "So how did {plan} turn out?",
]

# When the plan text does not slot cleanly into the templates above (the extractor guarantees a short noun
# phrase, but "guarantees" is doing a lot of work when a language model produced it), fall back to these.
PLAN_CALLBACKS_GENERIC = [
    "How did your day go?",
    "How has your day been?",
    "How did today treat you?",
    "How was your day, then?",
    "Did the day go alright?",
]

# --- Array 7: job-aware weather advice, keyed (job_keyword, weather_band) ---
# The bus-driver scenario: a job note plus today's forecast composes into one sentence that could only be
# said to that person on that day. Misses fall back to the plain weather advice, so an unlisted job costs
# nothing.
JOB_WEATHER = {
    ("driv", "wet"): [
        "I remember you're driving -- I hope the roads aren't too hard going in this rain.",
        "With this rain about, I hope the driving stays easy today.",
        "Rain on the way, and you behind a wheel -- do take it steady.",
    ],
    ("driv", "hot"): [
        "I remember you're driving -- keep some water in the cab in this heat.",
        "Hot one for driving today. Do keep cool.",
    ],
    ("driv", "chilly"): [
        "Cold one for an early start behind the wheel -- mind the frost.",
        "I remember you're driving; it's cold enough for a scraper this morning.",
    ],
    ("teach", "wet"): [
        "Rain today -- indoor lunch duty for you, I suspect.",
        "Wet one today; the classroom will be busy at break, I imagine.",
    ],
    ("teach", "hot"): [
        "Hot one today -- a classroom full of restless students, I'd guess.",
    ],
    ("nurs", "wet"): [
        "Wet one out there -- I hope the commute to the ward is kind.",
    ],
    ("nurs", "hot"): [
        "Hot day for a long shift -- do keep your water bottle filled.",
    ],
    ("garden", "wet"): [
        "Rain today -- your garden will be pleased, even if you aren't.",
    ],
    ("garden", "hot"): [
        "Hot one today -- the garden will want watering this evening.",
    ],
    ("build", "wet"): [
        "Rain forecast, and you on site -- I hope it holds off.",
    ],
    ("build", "hot"): [
        "Hot day on site today -- shade and water where you can.",
    ],
    ("cook", "hot"): [
        "Hot day AND a kitchen -- you have my sympathy.",
    ],
}


def _weather_band(snapshot):
    """The forecast reduced to one of 'wet' / 'hot' / 'chilly' / 'mild', for the job pool key."""
    try:
        day = snapshot["days"][0]
        cond = day.get("condition")
        if cond == "Thunderstorms" or cond in weather._WET:
            return "wet"
        tmax = float(day["tmax"])
        if tmax >= _envf("ROBOT_WEATHER_HOT_C", 30):
            return "hot"
        if tmax <= _envf("ROBOT_WEATHER_CHILLY_C", 15):
            return "chilly"
        return "mild"
    except Exception:
        return ""


def _job_advice(notes, snapshot):
    """A job-aware weather line, or "" -- the caller falls back to the plain weather advice."""
    band = _weather_band(snapshot)
    if not band:
        return ""
    job = next((text for kind, text, _ts in (notes or []) if kind == "job"), "")
    if not job:
        return ""
    low = job.lower()
    for (keyword, want_band), pool in JOB_WEATHER.items():
        if want_band == band and keyword in low:
            return random.choice(pool)
    return ""


def _plan_callback(notes, now_ts=None, tz=None):
    """A "how was X?" clause for a plan stated EARLIER TODAY, or "".

    Deliberately requires the plan to be older than a couple of hours: somebody who said "I'm off to
    school" ninety seconds ago has not been yet, and asking how it went is the kind of mistake that makes
    a robot feel broken rather than attentive."""
    try:
        now = now_ts if now_ts else time.time()
        for kind, text, ts in (notes or []):
            if kind != "plan" or not text:
                continue
            if (now - float(ts)) < _envf("ROBOT_SOCIAL_PLAN_MIN_AGE_S", 7200):
                continue                       # too soon to ask how it went
            if bucket(ts, now_ts=now, tz=tz) != TODAY:
                continue                       # only today's plans; older ones have expired anyway
            plan = str(text).strip().rstrip(".")
            # The templates read "How was {plan}?" -- fine for "school" or "your exam", wrong for a whole
            # sentence. Anything long or verb-shaped falls back to the generic form rather than producing
            # "How was I am going to the dentist?".
            if plan and len(plan.split()) <= 4:
                return random.choice(PLAN_CALLBACKS).format(plan=plan)
            return random.choice(PLAN_CALLBACKS_GENERIC)
    except Exception as e:
        log.debug("social: plan callback failed (%s)", e)
    return ""


def greeting(name, pronunciation=None, snapshot=None, last_seen=None, notes=None,
             clock=None, tz=None, now_ts=None):
    """The full Social-Memory greeting: opener (by sighting bucket) + AT MOST one follow-on clause.

    `last_seen` is memory.last_sighting()'s (ts, greeted) or None; `notes` is memory.notes_for(). Both are
    passed in rather than fetched here so this stays a pure function -- the caller owns the database and
    the tests own neither.

    Follow-on precedence, most specific first:
      1. nothing, if the opener already asked a question (the 'seen earlier today' bucket does);
      2. "how was school?", if they told us a plan earlier today;
      3. job-aware weather, if we know what they do and the forecast is notable;
      4. plain weather advice -- i.e. exactly what weather.greeting() would have said.
    """
    spoken = (pronunciation or name or "").strip() or "friend"
    try:
        b = bucket(last_seen[0] if last_seen else None, now_ts=now_ts, tz=tz)
        line = random.choice(_OPENERS[b]).replace("[Name]", spoken)
        if b in _ASKS_ALREADY:
            return line
        tail = _plan_callback(notes, now_ts=now_ts, tz=tz)
        if not tail:
            tail = _job_advice(notes, snapshot)
        if not tail:
            tail = weather.advice(snapshot)
        return f"{line} {tail}" if tail else line
    except Exception as e:
        # The whole point of Social Memory is a nicer hello; it must never be the reason there isn't one.
        log.debug("social: greeting failed (%s) -- falling back to the weather greeting", e)
        return weather.greeting(name, pronunciation, snapshot=snapshot, clock=clock)


def context_note(name, last_seen=None, notes=None, now_ts=None, tz=None):
    """The one-sentence-group prepend that tells the LLM who it is talking to, or "".

    This is the piece that makes mid-conversation memory work: with it the model can ask "how was school?"
    or "did you have to work in this rain?" unprompted, because it actually knows. Same discipline as the
    weather note -- short, never raises, rebuilt each turn from data already in hand."""
    try:
        name = (name or "").strip()
        if not name:
            return ""
        bits = [f"You are talking to {name}."]
        if last_seen:
            when = {TODAY: "earlier today", YESTERDAY: "yesterday",
                    THIS_WEEK: "a few days ago", LONG_GAP: "quite a while ago"}.get(
                        bucket(last_seen[0], now_ts=now_ts, tz=tz))
            if when:
                bits.append(f"You last saw them {when}.")
        known = [f"{k}: {t}" for k, t, _ts in (notes or []) if k in ("job", "like", "note")][:3]
        if known:
            bits.append("You know -- " + "; ".join(known) + ".")
        plans = [t for k, t, _ts in (notes or []) if k == "plan"][:1]
        if plans:
            bits.append(f"Earlier today they mentioned: {plans[0]}.")
        return "(" + " ".join(bits) + ")"
    except Exception as e:
        log.debug("social: context_note failed (%s)", e)
        return ""


# --- learning: the post-conversation extractor -----------------------------------------------------------
# This is the only part of Social Memory that involves a model, and it runs AFTER everyone has stopped
# talking, on a daemon thread. Nothing on the greet path or inside a turn waits for it, so a slow or failed
# extraction costs a chat's worth of learning and nothing else.

# Kinds we are willing to store, and whether they expire. Anything else the model invents is dropped:
# an open vocabulary here would let one odd reply create a table full of one-off categories that no
# consumer ever reads.
NOTE_KINDS = {"job": False, "like": False, "note": False, "plan": True}

EXTRACT_PROMPT = (
    "You extract durable facts from a short conversation between a household robot and a person.\n"
    "Person's name: {name}\n\n"
    "Conversation:\n{transcript}\n\n"
    "Return JSON ONLY: {{\"notes\": [{{\"kind\": \"job|like|note|plan\", \"text\": \"...\", "
    "\"transient\": true|false}}]}}\n"
    "Rules:\n"
    "- kind 'job' = what they do for a living. 'like' = a stable interest. 'note' = another lasting fact.\n"
    "- kind 'plan' = something happening TODAY only (going to school, a dentist appointment); "
    "set transient true for these and false for everything else.\n"
    "- text must be a SHORT noun phrase in the third person: \"drives a bus\", \"school\", "
    "\"gardening\". Never a whole sentence, never a question.\n"
    "- Only include something the person actually stated about THEMSELVES. If nothing qualifies, "
    "return {{\"notes\": []}}. Do not guess."
)

_MAX_TEXT_WORDS = 8


def parse_extraction(obj, kinds=None):
    """A model's reply -> [(kind, text, transient)], dropping everything that isn't usable.

    Separated from the LLM call so it can be tested against hostile input without a model, which matters:
    this is the one place where generated text becomes stored state, and every row it accepts will later be
    spoken aloud to the person it is about. The rules are deliberately mean -- unknown kind, empty text, a
    whole sentence, a question, a non-dict -- all dropped silently."""
    allowed = kinds or NOTE_KINDS
    out = []
    try:
        notes = (obj or {}).get("notes")
        if not isinstance(notes, list):
            return []
        for row in notes[:6]:                       # a chat cannot legitimately teach more than a handful
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "").strip().lower()
            # Order matters: the trailing stop comes off BEFORE the quotes, or '"drives a bus".' loses its
            # opening quote and keeps the closing one.
            text = str(row.get("text") or "").strip().rstrip(".").strip('"').strip()
            if kind not in allowed or not text:
                continue
            if len(text.split()) > _MAX_TEXT_WORDS or "?" in text:
                continue                            # a sentence or a question, not a noun phrase
            transient = bool(row.get("transient", allowed.get(kind, False)))
            out.append((kind, text, transient))
    except Exception as e:
        log.debug("social: parse_extraction failed (%s)", e)
        return []
    return out


def format_transcript(turns, max_turns=12, max_chars=1200):
    """The in-RAM turn buffer -> the text handed to the extractor. Bounded at both ends: a long chat must
    not blow the model's context, and the END of a conversation is where people say what they are doing."""
    lines = []
    for role, text in list(turns or [])[-max_turns:]:
        text = str(text or "").strip().replace("\n", " ")
        if text:
            lines.append(f"{'Person' if role == 'user' else 'Robot'}: {text}")
    out = "\n".join(lines)
    return out[-max_chars:] if len(out) > max_chars else out


def curiosity_directive(name):
    """The nudge that makes Jarvis ask what someone does for a living, in its own words.

    A directive rather than a canned question on purpose: an interjected "WHAT IS YOUR JOB?" mid-chat is
    exactly the robot-shaped behaviour this feature exists to avoid. The model folds it into whatever it
    was already saying, and the post-conversation extractor picks the answer up."""
    return (f"(If it fits naturally in the conversation, ask {name} what they do for a living -- "
            f"once, in passing, not as an interrogation.)")


def curiosity_due(notes, kind="job", cooldown_d=None):
    """True when it is worth asking about `kind`: we don't know it, and we haven't recently asked.

    The marker note is written whether or not the person answers, so a reluctant visitor is asked once a
    month rather than every single conversation."""
    try:
        if any(k == kind for k, _t, _ts in (notes or [])):
            return False                       # already known -- nothing to be curious about
        cooldown = (cooldown_d if cooldown_d is not None
                    else _envf("ROBOT_SOCIAL_CURIOSITY_COOLDOWN_D", 30)) * 86400.0
        marker = f"asked-{kind}"
        for k, t, ts in (notes or []):
            if k == "curiosity" and t == marker and (time.time() - float(ts)) < cooldown:
                return False
        return True
    except Exception as e:
        log.debug("social: curiosity_due failed (%s)", e)
        return False
