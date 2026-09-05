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
5. **It wakes the architect into absence, once per condition, within quota.**
   A present architect is not woken; a paused one (quota) is not woken until the
   reset; a wake that failed on a binary is not retried on that binary.
6. **It refills.** An empty queue with an idle implementer wakes the architect
   to refill, with or without a source bound, at most every thirty minutes.
7. **Every decision is traceable.** Each cycle records its measurements and the
   ordered verdicts to `~/.ao/cycles-<project>.jsonl`; `ao watchdog explain`
   shows the same for a dry cycle now.
8. **Alarms follow a policy**, not a mood — see below and [alarms.md](alarms.md).
9. **It proves it is alive.** A heartbeat every cycle; siblings check each
   other's; `ao doctor` shows the last tick.

## The guard chain

Order matters: each guard sees only what the ones above left standing.

| # | guard | measures | verdict when it fires |
|---|---|---|---|
| −1 | hold | `.ao/hold` | stand down; red after four hours |
| 0 | working here | orphans swept, live turns counted by root | stand down while a turn is live; reap a turn silent past 3× idle, by process group |
| 1 | idle | transcript age vs `--idle-minutes` | not idle yet: stand down |
| 2 | waiting on architect | newest implementer report is a request, inbox empty, queue empty | stand down and say so — a nudge cannot answer it |
| 2 | open work | inbox mail, product dirt outside coordination dirs, review newer than HEAD | nothing open: refill (2b) or stand down |
| 2b | refill | queued < threshold (source's, else 1), implementer idle, ≥30 min since last | wake the architect to refill |
| 3 | round budget | reviews since the slice began or was re-specified | over budget: anomaly, not a nudge |
| 4 | quota | implementer window / credits | no headroom: handoff once an hour, stand down |
| 4b | provider degraded | tail of the last nudge segment | 5xx/overload: back off |
| 4c | reports pending | implementer reports the architect has not processed | wake the architect (once per 15 min, into absence, within quota) |
| 5 | backoff | work fingerprint unchanged across nudges | escalate instead of nudging again |
| 6 | nudge | — | start one headless turn in its own session |

Anomalies (`anomalies()`) are computed before the chain and delivered as facts:
one file per condition, grouped per kind with a count.

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
| F12 | thirteen minutes of "1 turn already running" with the implementer idle | the architect's own monitor shell, cwd in the repo, mentioned the agent | `_is_agent_process`: binary on the command line, not a mention | test_processes | #23 |
| F13 | writer check timed out after 120 s | one `lsof` per candidate pid, hundreds of candidates | one `lsof` for all | test_processes | — |
| F14 | round budget kept firing after the slice was re-specified | rounds counted from the board's `since:` only | budget restarts at the latest scoped architect decision | test_guards | — |
| F15 | empty queue never triggered a refill | refill required a bound source | refill without a source when the queue is empty | — | — |
| F16 | architect at quota: wakes failed, and the desktop app resumed the session on its own | no notion of the architect's quota; a second resume path nobody modelled | `wake_error` kind `quota` → wait until reset, orange once; resume rule in the architect's standing instructions | test_wake | — |
| F17 | eleven hours of orange nobody saw | no channel beyond the desktop and an unconfigured bot | the ladder: orange → red (e-mail) after an hour; resolved notices; `ao doctor` warns on missing channels | test_guards (ladder) | — |
| F18 | a dead watchdog is silent | nothing watched the watcher | heartbeat per cycle, sibling check, `last tick` in doctor | test_watchdog_cycle | — |

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
- **Resolved.** When an episode goes quiet for two hours, the same channels
  hear it end — an alert with no "over" teaches people to keep worrying.
- **Storm-capped.** Twelve sent alerts in an hour and the rest are recorded
  only, with one notice saying so; red still mails.
- **Actionable.** Every message names the project, what stands, since when, how
  many times, and the `ao` command that shows more.
- **Testable.** `ao alarms test --level red` rings every channel for real.
- **Self-monitored.** Heartbeats, sibling checks, `last tick`.
