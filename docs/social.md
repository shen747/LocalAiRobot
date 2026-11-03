# Social memory

Greetings that remember. Seeing Ramesh at 8 am and again at 4 pm gets "nice to see you again" rather than a
second hello from scratch; someone who said "I'm off to school" in the morning gets asked how it went; and a
job it learned in an earlier chat can join the weather advice. It works with [Concierge mode](concierge.md),
for people it recognises.

It's on by default. `ROBOT_SOCIAL_MEMORY=0` turns it off, and the robot greets exactly as it did without it.

## What it says

| Situation | Example |
|---|---|
| first meeting | "Ruh-mesh! Nice to meet you at last." |
| seen earlier today | "There you are again, Ruh-mesh! How goes it?" |
| yesterday or this week | "Hello, Ruh-mesh. Back again. Lovely." |
| away for `ROBOT_SOCIAL_LONG_GAP_D` (14) days or more | "It's been a while, Ruh-mesh. Good to see you!" |
| mentioned a plan this morning | "...how was school?" |
| known job and a wet forecast | "...I remember you're driving. I hope the roads aren't too hard in this rain." |

A greeting adds at most one extra phrase: a question about a plan first, then job-aware weather, then plain
weather advice. During a conversation the model is told who it's talking to, so it can follow up naturally.

## How it learns

When a conversation ends, and only if the person spoke at least twice, one model call reads the transcript and
picks out short notes: a job, things they like, today's plans. Notes that don't look like short facts are
thrown away, and plans expire at midnight. This call goes where your conversations go: to Gemini if you use
it, otherwise to the local model. Nothing else leaves the robot. If the call fails, that chat simply teaches it
nothing.

With `ROBOT_SOCIAL_CURIOSITY=1`, after `ROBOT_SOCIAL_CURIOSITY_AFTER` (3) exchanges with someone whose job it
doesn't know, it asks, in its own words, then waits `ROBOT_SOCIAL_CURIOSITY_COOLDOWN_D` (30) days before asking
again.

Greetings themselves never wait on a model: they come from a database lookup in milliseconds. Everything is kept
in `~/.localairobot/memory.db`.

## Watching it work

```bash
journalctl -u robot.service -f | grep -a 'social:'
```

```
social: greeting 'Ramesh' as a 'today' sighting (2 note(s) known)
social: learned job='drives a bus' about 'Ramesh'
```

The first line shows it using history, the second shows it learning.

## Seeing and deleting what it knows

It says these things aloud to the person they're about, so they're easy to check:

| Command | Shows or does |
|---|---|
| `./scripts/rag.sh --sightings Ramesh` | when it last saw them |
| `./scripts/rag.sh --notes Ramesh` | what they've told it, and when each note expires |
| `./scripts/rag.sh --forget-notes Ramesh` | deletes all their notes |
| `./scripts/rag.sh --forget-notes Ramesh job` | deletes one kind: `job`, `like`, `note`, `plan` or `curiosity` |

Got something wrong? Delete that note and it learns again from the next conversation.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_SOCIAL_MEMORY` | `1` | on or off |
| `ROBOT_SOCIAL_LONG_GAP_D` | `14` | days away before "it's been a while" |
| `ROBOT_SOCIAL_PLAN_MIN_AGE_S` | `7200` | how long before it asks how a plan went |
| `ROBOT_SOCIAL_CURIOSITY` | `1` | asks what people do |
| `ROBOT_MEMORY_RETAIN_SIGHTINGS` | `2000` | sightings kept |
| `ROBOT_MEMORY_RETAIN_NOTES` | `50` | notes kept per person |

If anything is missing (no history, no notes, no forecast) it falls back to a plainer greeting, so the worst a
bug here can do is make the hello less personal.
