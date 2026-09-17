# The watchdog, on the operating table

The watchdog is the part of ao that runs when nobody is looking, so every one of
its faults is a stall nobody notices. This page is the surgery: what it
guarantees, how it decides, every fault it has had, and the instruments for
looking inside a running one.

## The contract

The maximum version of this watchdog is not clever. It makes these promises and
nothing else:

1. **It never nudges an implementer that is working, waiting, or held.** Working
   is measured by live turns (not processes, not shells that mention the agent).
   Waiting is a standing request with nothing newer addressed to the implementer
   and nothing queued. Held is `.ao/hold`.
2. **It counts only agents.** A candidate process is a writer only when an agent
   binary is on its command line — as the program, a sibling binary, or a path
   component of its runtime. Terminals, editors, monitors, greps are not.
3. **It clears its own debris before it counts.** A turn it reaps dies by process
   group; what an earlier turn left behind (no terminal, dead group leader) is
   swept every cycle and never counted.
4. **It reads the outcome of everything it starts.** A wake or a nudge is
   detached, so the next cycle reads the log segment it wrote: a stale binary,
   an exhausted quota, a dead session each get their own consequence, and none
   is retried blindly.
5. **It wakes the architect into measured absence, once per condition, within
   quota.** Presence means a live interactive process for the configured
   architect binary at the configured project cwd — never transcript age, a
   remembered pid, lock file, or declaration. Headless and AO-helper trees do
   not count as human presence; a fresh process-start-validated AO helper tree
   registered specifically as `architect` prevents two watchdog helpers. Process
   exit releases both checks on the next tick, and dry-run reports the same
   suppression decision as a live cycle.
6. **It refills.** An empty queue with an idle implementer wakes the architect
   to refill, with or without a source bound, at most every thirty minutes.
7. **Every decision is traceable.** Each cycle records its measurements and the
   ordered verdicts to `~/.ao/cycles-<project>.jsonl`; `ao watchdog explain`
   shows the same for a dry cycle now.
8. **Alarms follow a policy**, not a mood — see below and [alarms.md](alarms.md).
9. **It proves it is alive.** A heartbeat every cycle; siblings check each
   other's; `ao doctor` shows the last tick. The doctor job pages a heartbeat that
   stopped, and gives a watchdog whose job came back with its own one cycle first.
10. **It comes back quietly.** A cycle that follows more than
    `watchdog.resume_gap_hours` without one is a resume: what stands is named once, in
    one notice, and nothing turns orange or red on time in which nothing ran — see
    [alarms.md](alarms.md#after-a-silence).

## The guard chain

Order matters: each guard sees only what the ones above left standing.

| # | guard | measures | verdict when it fires |
|---|---|---|---|
| −1 | hold | `.ao/hold` | stand down; red after four hours |
| 0 | working here | orphans swept, live turns counted by root | stand down while a turn is live; reap a turn silent past 3× idle, by process group |
| 1 | idle | the implementer's last write, a subagent's included, vs `--idle-minutes` | not idle yet: stand down |
| 2 | waiting on architect | newest implementer report is a request, inbox empty, queue empty | stand down and say so — a nudge cannot answer it |
| 2 | open work | inbox mail, product dirt outside coordination dirs, review newer than HEAD | nothing open: refill (2b) or stand down |
| 2b | refill | queued < threshold (source's, else 1), implementer idle, ≥30 min since last, configured architect process absent | wake the architect to refill |
| 3 | round budget | reviews since the slice began or was re-specified | over budget: anomaly, not a nudge |
| 4 | quota | implementer window / credits | no headroom: handoff once an hour, stand down |
| 4b | provider degraded | tail of the last nudge segment | 5xx/overload: back off |
| 4c | reports pending | implementer reports the architect has not processed | wake the architect (once per 15 min, into absence, within quota) |
| 5 | backoff | work fingerprint unchanged across nudges | escalate instead of nudging again |
| 6 | nudge | — | start one headless turn in its own session |

Anomalies (`anomalies()`) are computed before the chain and delivered as facts:
one file per condition, grouped per kind with a count.

## One agent, more than one queue

An implementer can hold work in two projects: a primary one and a secondary one named in
the primary's config, `"secondary": [{"root": "…", "name": "ao"}]`. Three rules follow
from the agent being one agent.

- **Presence is the agent's.** When the implementer's transcript in a secondary project
  moved within the idle window, it is working, whatever this tree's transcript says: the
  watchdog does not nudge it back, and `ao status` names the project it is in. On
  2026-09-07 the primary's watchdog nudged an agent that was working elsewhere.
- **An empty queue here is not an empty queue.** With nothing READY in the primary and no
  open work, the nudge names the secondary project's first READY item instead of leaving
  the agent to wait on blockers that belong to people.
- **A person's blocker reaches a person.** A blocked item marked `waiting: human` is
  delivered to the human channel directly on the next cycle and never held for an agent
  whose reachability was only assumed.

## Fault catalog

Every fault the watchdog has had, in the order it was found. "Test" names the
test that would fail if it came back.

| # | symptom | cause | fix | test | lesson |
|---|---|---|---|---|---|
| F1 | one concurrent writer reported, fifteen live | `ps` truncates long command lines | `pgrep -f` + cwd match | test_processes | #6 |
| F2 | fifteen turns accumulated | nudge remembered one pid | count every process with the repo as cwd | — | #6 |
| F3 | one zombie blocked every nudge for seven hours | a live process read as a live turn | reap turns silent past 3× idle | — | #8 |
| F4 | double nudge during a 5xx backoff | no memory of the last nudge's failure | pid guard + provider-degraded guard | — | #9 |
| F5 | spinning undetected | idle guard sees a busy transcript | progress ledger, `spinning()` | — | #11 |
| F6 | notification storm; "reported to the architect" that never was | no rate limit, wrong audience | keyed windows, audience routing, honest text | test_watchdog_cycle (storm) | #12 |
| F7 | architect never woken | gated on the implementer's pid and on the notify throttle | `arch_alive`, wake on unprocessed reports, `architect_present` | — | #13 |
| F8 | woken architect edited ao and built a runaway | it had Write/Edit; watchdog read its own anomalies as reports | tool allowlist, `ao note`, exclude own outbox | test_mail (anomalies) | #19 |
| F9 | `ao hold` killed the owner's live sessions | hold stopped every process in the tree | headless-only hold and reaper | — | #20 |
| F10 | implementer refused to write for 3.5 h over four "writers" | reaper killed the wrapper only; children orphaned; single-writer rule counted them | kill by process group; orphans by shape; `ao writers` | test_processes | #21 |
| F11 | eighty identical reports, eighty anomalies, forty failed wakes | stale `claude` first on PATH; wake output never read; own mail counted as open work; anomaly per file | `resolve_binary`; `wake_error`; filtered `open_work`; `waiting_on_architect`; report folding; grouped anomalies | test_wake, test_guards, test_mail | #22 |
| F12 | thirteen minutes of "1 turn already running" with the implementer idle | the architect's own monitor shell, cwd in the repo, mentioned the agent | `_is_agent_process` on the exact argument vector: binary on the command line, not a mention; sibling binaries must be executables | test_processes | #23, #25 |
| F13 | writer check timed out after 120 s | one `lsof` per candidate pid, hundreds of candidates | platform process API via ctypes (`procs.py`, psutil's method): whole table in 0.3 s | test_processes, test_procs | #25 |
| F14 | round budget kept firing after the slice was re-specified | rounds counted from the board's `since:` only | budget restarts at the latest scoped architect decision | test_guards | — |
| F15 | empty queue never triggered a refill | refill required a bound source | refill without a source when the queue is empty | — | — |
| F16 | architect at quota: wakes failed, and the desktop app resumed the session on its own | no notion of the architect's quota; a second resume path nobody modelled | `wake_error` kind `quota` → wait until reset, orange once; resume rule in the architect's standing instructions | test_wake | — |
| F17 | eleven hours of orange nobody saw | no channel beyond the desktop and an unconfigured bot | the ladder: orange → red (e-mail) after an hour; resolved notices; `ao doctor` warns on missing channels | test_guards (ladder) | — |
| F18 | a dead watchdog is silent | nothing watched the watcher | heartbeat per cycle, sibling check, `last tick` in doctor | test_watchdog_cycle | — |
| F19 | an old transcript caused a second live architect, while a fresh transcript or reused remembered pid kept a dead one "present" | transcript mtime/pid state was treated as liveness; refill lacked a guard; filtered PIDs broke ancestry; generic agent/Windows matching blurred role identity | exact configured launcher/runtime + cwd over full parent graph; interactive root only; cycle-safe helper exclusion; fresh process-start + `architect`-role duplicate guard; identical dry/live verdicts | test_processes, test_scenarios | — |
| F20 | the first cycle after a two-week stop would mail an unread request red, ring an open decision, announce a standing red as no longer raised, mail credits whose snooze had ended and tell the phone of a wake | every record kept its date, and every check measured age as if the watchdog had been watching | a cycle after `watchdog.resume_gap_hours` of silence is a resume: one notice names what stands, carried episodes close unannounced, and ages count from the resume | test_resume_quiet | — |
| F21 | rebuilt with credits spent and no architect to wake, the first day back after two weeks off would have sent 172 desktop and 172 phone notices and 11 mails about four conditions: credits mailed every six hours, a dead watchdog and the credits paged at load, "needs you" every ten minutes | the credits alarm was raised without the reset its reading named; the doctor judged a watchdog its job had just started and kept its own copy of the credits alarm; a window was all that limited a condition that never changed | the credits alarm is held until its reset; the doctor gives a watchdog that came back one cycle and raises the watchdog's own alarm; a condition that says what it is about rings once for it and mails on the ladder's schedule | test_notice_noise | — |
| F22 | with architect wakes switched off, a decision request nobody could act on was mailed twice on each red repeat - "needs you" under its anomaly, and "2 report(s) waiting and architect wakes are off" - and neither mail said which report waited or since when | the wake path alarmed every report no architect was woken for, the request and the watchdog's own report of its anomaly among them, beside the anomaly's alarm for the same request | a report an anomaly stands for is named by that anomaly's alarm, with since when and why no architect acts; `reports-no-wake` rings only for the reports no anomaly stands for, and an open decision's alarm never stands for a request | test_waiting_one_alarm | — |
| F23 | rebuilt in the same project, four conditions still repeated through a day: an open decision rang the desktop and the phone 23 times, an unseen request 15, a request an architect at the keyboard had not read was mailed under two keys on every red repeat, and a wake failing on a transport error sent the phone 95 "architect woken" lines | a window was all that limited the decision and the unseen request, and an hour of orange turned the unseen request red before its threshold; `present-pending` rang beside the anomaly; the "architect woken" line went out as each retry started, and every retry read the failure with a new time | an open decision is its own alarm, rung once for its question and not also "needs you"; `present-pending` names only the reports no anomaly stands for; an unseen request rings as it crosses a threshold and is red at its red threshold; a failed wake is named without its time or ids, and a retry is told only once it has not failed | test_noise_repeats | — |
| F24 | rebuilt with a review parked through a day, its alarm, whose window is a day, rang the desktop and the phone again each time its last ring was eight and a half hours old; with the notices ledger held to 64 KB, four times more in the day | a window check read the last 100 KB of the notices ledger, and the ledger's own bound can keep less than a window | when each key was last recorded and sent is folded beside the ledger as each notice is written, before anything trims the ledger, and a check reads that and the ledger lines written after the newest one folded | test_notice_window | — |
| F25 | a session quiet while its subagent worked read as idle and its turn as ended: in 179 of the 444 closed turns a subagent wrote in, across 59 stores that delegate, the runtime waiting for the subagent would have been reaped at the idle window while the subagent went on | the idle guard, the reap and the spin check read the session transcript alone, and a subagent writes to a transcript of its own | the implementer's last write and the spin check's growth include the subagent transcripts the adapter declares, and a turn has not ended while one was written after the session transcript; read by modification time and size, none opened | test_subagent_liveness | — |

## Scenarios: testing the decision, not the measurement

Unit tests prove each measurement; `tests/test_scenarios.py` proves the
*decision*. A `World` fabricates the process table, the transcript's age, the
mailbox, the board, the reviews and the quota, then runs one dry cycle and
reads the last decision line. Every fault in the catalog is a world the cycle
once misread, and each has a scenario now: orphans that looked like writers, a
shell that mentioned the agent, a request that looked like unread mail, an
architect at quota. The rule going forward: a fault gets its scenario before
its fix, and the scenario stays.

## The instruments

```bash
ao watchdog explain          # one dry cycle: every measurement, every verdict, in order
ao watchdog trace --last 30  # the recorded cycles: time, verdict, the facts that decided it
ao writers                   # who is writing (turns, not processes) and what is orphaned
ao alarms                    # live alarm episodes, their level and age
ao doctor                    # binaries, last wake, last tick, channels, live alarms
ao doctor --check            # the same, quiet, exit 1 on problems — runs every 15 min from its own launchd job
ao mail log                  # every message written and when it was consumed
```

Over MCP: `ao_watchdog {action: explain|trace}` returns the same, read-only.

When the watchdog "did nothing", run `explain`. The last decision line is the
verdict; the lines above it are the guards that let it through; the
measurements are what they saw. If a measurement is wrong — a writer that is
not an agent, mail that is not addressed to the implementer — the fault is in a
measurement function and there is a test file for each.

## Alarm policy

Best practice, applied:

- **Keyed.** Every alert has a stable key; the same condition never rings twice
  inside its window, and its history is one line per raise in `ao notices`.
- **Leveled by who acts.** Yellow: the architect (anomaly + wake). Orange: a
  person (desktop + Telegram). Red: a person, now (e-mail). See alarms.md.
- **Laddered.** An orange standing for `alarms.red_after_minutes` (60) rings
  red; red repeats at most every six hours. Some conditions are red at once
  (a hold standing four hours).
- **Told once.** A condition that stands and says what it is about rings the
  desktop and the phone once for what it says and then only climbs: red mails
  on its schedule, and a red with a known end - spent credits until their reset -
  is held until that end. Another request, a projection come true or another
  account is told again. With no quota to wake the architect, one handoff goes
  to the phone for the reports that wait, not one every hour. An open decision
  rings once for its question, an unseen request as it crosses each of its
  thresholds, and a failed wake once for what failed; the phone hears of a wake
  retried after a failure only once it has not failed.
- **One alarm for what waits.** A report an anomaly stands for is told by that
  anomaly's "needs you", which names it, since when it has waited and why no
  architect acts. With wakes switched off, `reports-no-wake` rings only for the
  reports no anomaly stands for, and with an architect at the keyboard
  `present-pending` does the same. An open decision is told by its own alarm,
  `decision-open:<id>`, and not as "needs you" as well.
- **Resolved.** When an episode goes quiet for two hours, the same channels
  hear it end — an alert with no "over" teaches people to keep worrying.
- **Storm-capped.** Twelve sent alerts in an hour and the rest are recorded
  only, with one notice saying so; red still mails.
- **Resumed.** After more than `watchdog.resume_gap_hours` without a cycle, what the
  cycle would ring and what the silence carried go out as one notice, to a person when
  any of it is a person's; an unseen request, an open decision or a hold from before the
  resume ages from the resume.
- **Actionable.** Every message names the project, what stands, since when, how
  many times, and the `ao` command that shows more.
- **Testable.** `ao alarms test --level red` rings every channel for real.
- **Self-monitored.** Heartbeats, sibling checks, `last tick`.
