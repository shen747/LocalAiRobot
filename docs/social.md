# Social Memory — greetings that know they've met you before

The concierge already recognises people, and already has a RAG. Social Memory introduces them to each
other: seeing Ramesh at 8am and again at 4pm should produce *"nice to see you **again** — how's your day
been so far?"*, not a second context-free hello. Someone who said *"I'm off to school"* in the morning gets
asked how it went. A job learned in an earlier chat composes with today's forecast.

**On by default.** `ROBOT_SOCIAL_MEMORY=0` restores the previous behaviour byte for byte — every greeting
and every prompt exactly as it was before this feature existed.

> **Two things to know before leaving it on.**
> 1. **One extra model call** at the end of a conversation where the person actually said something
>    (two turns or more — a greeting plus "hello" is skipped, so a passer-by costs nothing). It runs on a
>    daemon thread; nothing waits on it. **It routes exactly like conversation does**: `gemini` or `mixed`
>    *with* a `ROBOT_GEMINI_API_KEY` go remote and are billable; **no key, or `local`, stays on the local
>    model.** Under `mixed`, a remote failure falls back to local rather than losing the chat.
> 2. **It remembers what people tell you** — their job, today's plans — as short notes in the local RAG db.
>    Nothing leaves the robot except that one extraction prompt. [Reading and deleting](#inspecting-and-forgetting)
>    what it has learned is two `sqlite3` commands.

---

## What it does

| Situation | What Jarvis says |
|---|---|
| First time ever | *"Ruh-mesh! Nice to meet you at last."* |
| Seen earlier **today** | *"There you are again, Ruh-mesh! How goes it?"* |
| Yesterday / this week | *"Hello, Ruh-mesh — back again. Lovely."* |
| Not for ≥ `LONG_GAP_D` days | *"It's been a while, Ruh-mesh — good to see you!"* |
| They mentioned a plan this morning | *"…how was school?"* |
| Known job + wet forecast | *"…I remember you're driving — I hope the roads aren't too hard in this rain."* |

And mid-conversation, the model is told who it is talking to, so it can follow up naturally without being
asked to.

### One follow-on clause, never two

A greeting is an **opener plus at most one** extra clause. Precedence, most specific first:

1. **nothing** — if the opener already asked a question (the *seen earlier today* pool does);
2. **the plan callback** — *"how was school?"*;
3. **job-aware weather** — *"I remember you're driving…"*;
4. **plain weather advice** — exactly what it says today.

A hello that greets you, asks about school *and* warns about rain isn't warm, it's a robot reading a
checklist.

---

## How it works

```
motion wake -> recognised as "Ramesh"
     |
     |  last_sighting("Ramesh")   <- read BEFORE recording, or you compare them against themselves
     |  notes_for("Ramesh")
     |  record_sighting(...)
     v
social.greeting()  ->  opener by sighting bucket + one clause      [no LLM, ~1ms]
     |
     v
conversation ... every turn gets "(You are talking to Ramesh. You last saw them earlier today.
                                   You know -- job: drives a bus. Earlier today they mentioned: school.)"
     |
     v
conversation ends -> daemon thread "social-extract" -> ONE model call -> person_notes rows
```

**No LLM on the greet path.** `say()` blocks through Piper and aplay, so a greeting has to be composed in
milliseconds — every decision is a dict lookup, an indexed `SELECT` and a `random.choice`. The one model
call is the extractor, and it runs after everyone has stopped talking.

### Two new tables, deliberately not in `facts`

```sql
sightings     (ts, name, score, greeted)
person_notes  (ts, name, kind, text, expires_ts)   -- kind: job | like | note | plan | curiosity
```

The RAG's `facts` table is **untouched**. That is not tidiness: `facts` retrieval is BM25 relevance-first
and never pruned, so a conversation-learned row would let *yesterday's* note outrank today's whenever its
wording scored better — stale data spoken as current — and the table would grow forever. These two are
looked up by `(name, kind)` at known moments rather than ranked against an utterance, so that failure mode
cannot occur by construction. Both are capped by the existing prune pass.

### Learning

At conversation end — and only if the person said something in **two or more turns**, so a greeting plus
*"hello"* never costs a call — the in-RAM transcript goes to one model call that returns JSON:

```json
{"notes": [{"kind": "job", "text": "drives a bus", "transient": false},
           {"kind": "plan", "text": "school", "transient": true}]}
```

Every row is filtered before it becomes state — unknown `kind`, empty text, a whole sentence, a question,
a non-dict, more than six rows — all dropped silently. This is the one place where generated text becomes
something the robot will later say aloud *to the person it is about*, so the rules are deliberately mean.
`transient` rows expire at **midnight** in `ROBOT_WEATHER_TZ`; nothing lingers to next week.

A failed extraction is always safe: the feature just didn't learn from that chat.

### Curiosity (`ROBOT_SOCIAL_CURIOSITY=1`)

After `CURIOSITY_AFTER` exchanges with someone whose job it doesn't know, one directive is added to the
next turn's context: *"(If it fits naturally, ask Ramesh what they do for a living.)"* The model asks in its
own voice, mid-flow — not as a canned interjection — and the extractor picks up the answer. The marker is
written **whether or not they answer**, so nobody is interrogated monthly.

Gated separately from the master switch because it makes the robot *initiate* rather than respond, which
is a bigger behavioural step than remembering.

---

## Config

| Key | Default | Meaning |
|---|---|---|
| `ROBOT_SOCIAL_MEMORY` | `1` | Master switch. `0` = the behaviour before this feature existed, byte for byte. |
| `ROBOT_SOCIAL_LONG_GAP_D` | `14` | Days before the *"it's been a while!"* opener. |
| `ROBOT_SOCIAL_PLAN_MIN_AGE_S` | `7200` | How long a plan must sit before it's asked about. Someone who said *"I'm off to school"* 90 seconds ago hasn't been yet. |
| `ROBOT_SOCIAL_CURIOSITY` | `1` | The ask-their-job loop. Its own key because it makes the robot **initiate**. |
| `ROBOT_SOCIAL_CURIOSITY_AFTER` | `3` | Exchanges to wait first. `1` would make it an interview. |
| `ROBOT_SOCIAL_CURIOSITY_COOLDOWN_D` | `30` | Days before re-asking an unanswered question. |
| `ROBOT_MEMORY_RETAIN_SIGHTINGS` | `2000` | Count-based, swept by the existing prune. |
| `ROBOT_MEMORY_RETAIN_NOTES` | `50` | Stable notes **per person** — one chatty visitor can't evict everyone else. |

---

## Watching it work

It is already on, so there is nothing to enable — just watch:

```bash
journalctl -u robot.service -f | grep -a 'social:'
```

You want lines like:

```
social: greeting 'Ramesh' as a 'today' sighting (2 note(s) known)
social: learned job='drives a bus' about 'Ramesh'
social: last 60 min -- 4 greeting(s) placed by sighting history, 1 thing(s) learned
```

The first line proves it is using history rather than picking at random; the second proves it is learning.
An hour with wakes but neither line means the flag is off or nobody was recognised by name.

To turn any of it off:

```bash
sed -i 's|^ROBOT_SOCIAL_CURIOSITY=.*|ROBOT_SOCIAL_CURIOSITY=0|' config/robot.env   # stop it asking
sed -i 's|^ROBOT_SOCIAL_MEMORY=.*|ROBOT_SOCIAL_MEMORY=0|' config/robot.env         # stop all of it
./scripts/run_me.sh --reload
```

## Inspecting and forgetting

Everything it has learned is two queries away:

```bash
sqlite3 ~/.localairobot/memory.db \
  "SELECT name, kind, text, datetime(ts,'unixepoch','localtime') FROM person_notes ORDER BY ts DESC;"
sqlite3 ~/.localairobot/memory.db \
  "SELECT name, COUNT(*), datetime(MAX(ts),'unixepoch','localtime') FROM sightings GROUP BY name;"
```

Forget something it got wrong:

```bash
sqlite3 ~/.localairobot/memory.db "DELETE FROM person_notes WHERE name='Ramesh' AND kind='job';"
```

## What degrades, and to what

| Missing | Result |
|---|---|
| No sighting history | The plain weather greeting |
| No weather either | Time-of-day opener alone |
| No notes | No callback clause |
| A broken query | Warning, then the plain greeting |
| Extractor fails / model down | Nothing stored; that chat taught nothing |
| `ROBOT_SOCIAL_MEMORY=0` | Nothing runs at all |

That ladder is why this ships **on** rather than behind a soak flag: the worst outcome of a bug in
`social.py` is a plainer hello, not a silent robot.

Every seam falls back to the layer below it. Nothing new is held under `audio_lock`, `turn_lock` or
`model_lock` during a turn, and the greet path gains two indexed `SELECT`s and one `INSERT`.
