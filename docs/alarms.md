# Alarms: yellow, orange, red

Three levels, named by who has to act.

| level | who acts | channel | raised by |
|---|---|---|---|
| **yellow** | the architect | mailbox anomaly + architect wake | an implementer report, a stall, a round budget — anything the architect can judge |
| **orange** | the human | desktop notification + Telegram | a decision only a person can make, a quota, a hold, a failed wake, a stuck agent |
| **red** | the human, now | **e-mail** (plus the orange channels) | an orange condition standing for an hour, or one that will not clear on its own |

The distinction that matters is orange → red. Orange assumes the person is
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

Some conditions ring red at once, because waiting cannot help:

- the implementer's credits are exhausted for the billing period
- the architect's wake keeps failing on the same binary
- a hold has stood for four hours

`ao alarms` lists the live episodes with their level and age. `ao alarms test
--level red` sends a real test through every channel.

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
