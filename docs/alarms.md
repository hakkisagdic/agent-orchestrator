# Alarms: yellow, orange, red

Three levels, named by who has to act.

| level | who acts | channel | raised by |
|---|---|---|---|
| **yellow** | the architect | mailbox anomaly + architect wake | an implementer report, a stall, a round budget — anything the architect can judge |
| **orange** | the human | desktop notification + Telegram | a decision only a person can make, a quota, a hold, a failed wake, a stuck agent |
| **red** | the human, now | **e-mail** (plus the orange channels) | an orange condition standing for an hour, or one that will not clear on its own |

The distinction that matters is orange → red. Alert call sites declare who
must act; that explicit audience is authoritative and title wording or
localization never changes its route. Legacy calls that omit an audience retain
title inference for compatibility, and regression coverage preserves both sides
of that boundary. `ao watchdog explain`/`--dry-run` computes the same routing
and verdicts but never writes a notice or alarm episode and never invokes the
desktop, Telegram, e-mail, or dead-man-switch ping channels.

Orange assumes the person is
near a screen. On 2026-09-05 they were asleep: a queue sat empty for eleven
hours, forty architect wakes failed, and every alert went to a notification
centre nobody looked at. Red assumes nothing — it lands in the inbox people
open when they wake up.

## The ladder

Every alert is recorded under a key. The first time an orange key is raised
starts an episode; while it keeps being raised, the episode ages. At
`alarms.red_after_minutes` (default 60) the next raise rings red: one e-mail,
repeated at most every six hours while the condition stands. Two hours of
silence closes the episode, so a condition that comes back later starts fresh.
When the watchdog itself was the one silent, see [After a silence](#after-a-silence).

Some conditions ring red at once, because waiting cannot help:

- the implementer's credits are exhausted for the billing period
- the architect's wake keeps failing on the same binary
- a hold has stood for four hours

`ao alarms` lists the live episodes with their level and age. `ao alarms test
--level red` sends a real test through every channel.

## After a silence

A watchdog that did not run for a while comes back to records that kept their dates:
alarm episodes, snoozes, unseen requests, open decisions, deferred wakes. On 2026-09-17
an owner switched one project's watchdog and doctor jobs off for two weeks, because they
kept ringing about an implementer that had run out of credits. Rebuilt in a temporary
project, the first cycle back would have mailed an unread request red at once, rung an
open decision on the desktop and the phone, announced a red alarm as no longer raised
although it still stood, mailed the credits alarm whose snooze had ended in the silence,
and told the phone it had woken the architect; an hour later the decision would have
mailed as well. Every one of them was measured as if the watchdog had been watching.

So a cycle that follows more than `watchdog.resume_gap_hours` (default 2, the quiet after
which `alarms.reset_after_hours` ends an episode) without a cycle, read from the heartbeat
the last one left, is a **resume**:

- **One notice.** What the cycle would ring still moves its ladder, so one condition keeps
  one alarm, and is recorded but not sent. With what the silence carried — the alarm
  episodes standing when it began, snoozes that ended in it, unseen decision requests, open
  decisions, deferred work, a hold, an implementer left with nothing to do — it is named
  once, in one notice sent when the cycle ends. A handoff note is written, not sent, and a
  wake is told in the notice rather than on its own.
- **To whom.** Each condition keeps the audience its own check gives it, and the notice
  goes to the widest: a person when anything it names is a person's (a person can act on
  the architect's business; the architect cannot lift a hold, end a snooze or buy
  credits), or when no architect is going to read it; otherwise the architect, through
  its mailbox.
- **Never louder.** The notice uses the orange channels and never e-mail. It counts as
  the ring of every condition it names for that condition's own window, and a red it names
  counts as told, as a mailed red does: it mails again only if it still stands
  `alarms.red_repeat_hours` later, or not before its known end.
- **Clocks restart.** An episode the silence carried closes without an announcement: it
  did not end, it went unwatched. An unseen request, an open decision and a hold from
  before the resume age from the resume, so none turns orange or red on time in which
  nothing ran. Whatever starts after the resume climbs the ladder as it always has.

A snooze still standing stays off the notice. `ao watchdog explain` shows the notice a
resume would send and writes nothing. A silence under the threshold changes nothing.

## Channels

- Desktop: `osascript` notification, always on.
- Telegram: `ao telegram setup` — see [telegram.md](telegram.md).
- E-mail: `ao email setup --token … --to …` — formsubmit.co relays a JSON POST,
  no server; the token lives in `~/.ao/email.json` (0600), never in a repo.
  `ao doctor` warns when neither Telegram nor e-mail is configured: an orange
  alarm with no channel beyond the desktop is a silent alarm.

## Configuration

```json
"alarms": {"red_after_minutes": 60}
```

in `.ao/config.json`. Optional.

## When the alarm itself can die

Every channel above runs on the machine that is failing. `ao pings setup --url
…` adds the one that does not: the watchdog and the doctor job ping an external
check (healthchecks.io) every cycle, and that service e-mails you when the pings
stop. The credit burn rate (`ao doctor`) turns "the plan runs out before it
resets" into a red alarm days ahead rather than a silent stop on the day.
