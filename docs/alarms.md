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

Some of what an alarm says follows the project's `language` ([configuration.md](configuration.md)),
and a project whose `language` is `tr` reads it in Turkish: the architect at quota, a failed wake,
`ao alarms test`, the lines a red mail adds below an alarm's text, and the phone's "architect woken"
and "architect done" lines. An alarm's key never follows the language, and neither does its audience:
a project that changes its language raises the episode that stands, and nothing rings again for it.

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

A key that rang does not ring the desktop and the phone again inside its window, and the
whole window counts, however little of it the notices ledger still holds. As each notice is
recorded, when its key was last recorded and last sent - and each key a resume notice names -
is folded into `.ao/ledger/notice-times.json`, and nothing trims the ledger before that fold:
not its own bound, `retention.observation_kb`, not the watchdog's cycle and not `ao prune`. A
check reads that file and the ledger lines written after the newest one folded, and writes
nothing. The file lets a key go a week after it was last recorded, and the oldest past 500
keys; a window that reaches back to a key it let go is read from the ledger. The check used to
read the last 100 KB of the ledger. Rebuilt in a temporary project with a review parked
through the day, those 100 KB held eight and a half hours, and the review, whose window is a
day, rang the desktop and the phone twice more; with the ledger held to 64 KB, four times more.

A condition that stands and can say what it is about rings the desktop and the phone
once for what it says: "needs you" about the request that waits, the credits alarm
about the account that ran out. It keeps climbing - it turns red and mails, and mails
again while it stands - but it does not ring the desktop and the phone again until it
says something else: another request waits, a projected run-out comes true, another
account runs out. Something new is told again, on the desktop and the phone once their
window allows, and by mail at once when the alarm is already red. Rebuilt in a
temporary project, a "needs you" about one request no architect could be woken for
rang every ten minutes: 143 times in a day on the desktop, and as many on the phone.

What waits for an architect nobody will wake is one alarm as well. An anomaly that reaches
a person - a decision request, a report while work is open, a review nobody collected - is
its "needs you", and it says what waits and why no architect acts: the reports it stands
for and since when they have waited, by the stamp their names carry, or else what it
measured. With architect wakes switched off, the mailbox check rings `reports-no-wake`
only for the reports no such anomaly stands for: a report that asks nothing while no work
is open, a lead, the watchdog's report of a condition that has ended. An open decision is
told by its own alarm (below), so a request behind it is the one its anomaly's "needs you"
names. Rebuilt in the same project, the request was mailed twice on each red repeat, once
under each key, and neither mail said which report waited. A snooze on the anomaly's alarm
keeps its reports off every channel; a snooze on `reports-no-wake` holds the reports it
still names.

An architect at the keyboard is told the same way. Its session acts only when someone
prompts it, so an anomaly reaches a person and its "needs you" says so, and
`present-pending` - "reports wait for the architect" - names only the reports no anomaly
stands for, with since when, rung once for the newest of them and again when another
waits. It used to ring beside "needs you" about the same request, and both were mailed on
every red repeat.

An open decision is one alarm, `decision-open:<id>`: it names the decision, its question
and how to answer it, rings the desktop and the phone once, turns red after its hour and
mails on red's schedule until it is answered. Another decision is another alarm. It reaches
a person whatever the architect does: `decisions.human_after_minutes` after it was asked,
or at once when no architect will act on it, in place of a "needs you" about the same
decision. Rebuilt in a temporary project, one decision nobody answered rang the desktop and
the phone every hour, 23 times in a day, and was mailed under two keys.

A decision request nobody has been shown rings as it crosses each of its thresholds (#30):
the architect is told at `mail.unseen_yellow_minutes`, the desktop and the phone at
`mail.unseen_orange_minutes`, and at `mail.unseen_red_minutes` it turns red, rings them again
and is mailed, then mails on red's schedule while nobody is shown it. Its red is that
threshold, not an hour of orange. Rebuilt, one unseen request rang 15 times on each in a day
and was first mailed two hours before its red threshold.

A request that an alarm to a person already tells - a "needs you" no architect will act on,
`reports-no-wake` or `present-pending` - is one condition on that alarm's ladder: its own
does not ring beside it, and a resume notice names it once. When no such alarm tells it any
more, because an architect can act on it again, its own ladder counts from the last cycle one
did, so a request a person has been told of for hours does not ring red at once. Rehearsing
the catch-up planned for 2026-10-01, a request nobody had been shown and no architect could
act on was mailed eight times in a day, four times under each key.

A wake that keeps failing is one condition too. `architect-wake-failed` says the failure's
kind, binary and words, without the time it was read at or an id that changes on every
attempt, so the same failure rings the desktop and the phone once and mails on red's
schedule whether the watchdog or the doctor raises it; a failure of another kind, binary or
wording is told again. The phone's "architect woken" line goes out as a wake starts unless
the wake before it failed within the last day. Such a retry is told once a later cycle reads
that it did not fail - it has ended, or has run for fifteen minutes, with no failure in its
log - and a retry that fails is not told at all. Rebuilt, a day of 529s sent the phone 95
"architect woken" lines about an architect nobody woke.

Some conditions ring red at once, because waiting cannot help:

- the implementer's credits are exhausted for the billing period, on the account its own
  adapter declares; an implementer whose adapter declares none ao can read has no credits alarm
- the architect's wake keeps failing on the same binary
- a hold has stood for four hours

A red with a known end is mailed once and then held until that end, instead of every
`alarms.red_repeat_hours`: the architect's usage window until its reset, "needs you"
while the architect is at quota until that quota comes back, and credits that ran out,
or are projected to run out before their reset, until the reset the reading names. A
reset is a known end only in the seconds the provider gives; one that cannot be read
that way holds nothing, and the alarm repeats as any red does. Without it an exhausted
plan whose reset was ten days away was mailed every six hours. A spent reading whose own
reset has passed says nothing of the plan after it, and the scheduled check pages nothing
on it: until a fresh reading is taken the credits are unknown, and a usage check that
cannot read them is reported as such.

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
  wake is told in the notice rather than on its own. What the silence carried is not named
  once its own known end has passed: an episode held until an end, and the credits alarm once
  the reset the implementer's last reading named has passed, since that reading says nothing
  of the plan after it. What the cycle itself raises was measured now, and is named. Rehearsed
  for 2026-10-01 with the jobs back six hours after the plan reset, the notice named the
  credits as standing.
- **To whom.** Each condition keeps the audience its own check gives it, and the notice
  goes to the widest: a person when anything it names is a person's (a person can act on
  the architect's business; the architect cannot lift a hold, end a snooze or buy
  credits), or when no architect is going to read it; otherwise the architect, through
  its mailbox.
- **Never louder.** The notice uses the orange channels and never e-mail. It counts as
  the ring of every condition it names for that condition's own window, or, for one that
  says what it is about, until it says something else, and a red it names
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
- Telegram: `ao telegram setup` — see [telegram.md](telegram.md). An alarm's text is sent
  as written, so an underscore in a report's name cannot make the phone refuse the message
  as broken markup.
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
resets" into a red alarm days ahead rather than a silent stop on the day. It projects only
the samples the implementer's own adapter took: another harness's readings in the same
ledger are not its account, and neither is a sample that names no adapter
([telemetry.md](telemetry.md), "Whose account").

The doctor job, `ao doctor --check --notify` every fifteen minutes, is the watchdog's
backstop, and it pages nothing twice:

- **One alarm whoever sees it.** What the watchdog raises itself - credits that ran out, an
  architect wake that failed - the doctor raises under the watchdog's own alarm, at its
  level and known end. One snooze keeps both off the channels and one mail tells it. It
  used to ring a second alarm whenever the watchdog's had gone quiet, which it also did
  after a silence, under a snooze and while a reading could not be taken.
- **Not dead on arrival.** Both jobs run when they are loaded, and the doctor can run first.
  It records each of its runs in `~/.ao/doctor-<project>.json`, and a run that finds its
  last one more than twenty minutes ago was stopped too: the jobs were switched off, or
  the machine was off or asleep. Then the watchdog has one of its cycles, the
  StartInterval of its launchd job, before the doctor pages it dead or pages what that
  first cycle raises or names. A watchdog whose heartbeat had already stopped when the
  doctor last ran is paged at once, one that does not come back is paged at the
  doctor's next run, and a doctor that cannot write its record gives no cycle.
