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
| 1b | usage limit | the implementer's newest words, when its harness wrote them in the model's place; the last nudge, when it died within seconds; each in the words its adapter declares | stopped on its limit: park the implementer's running items until the reset, or one retry where none was named, then resume the session once — see [below](#a-slice-parked-on-quota) |
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

A nudge, a wake and a refill tell their turn what to do in the project's `language`
([configuration.md](configuration.md)). The nudge names `## DECISION REQUIRED` for a slice parked on a
decision and the wake `## URGENT` for a message that must reach the implementer - `## KARAR GEREKLİ`
and `## ACİL` in Turkish - and the headings of both languages are read in every project. A Turkish
project's turns are told what they always were, byte for byte.

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

## A slice parked on quota

*In ao since slice QUOTA-PARK: a usage limit that stops the implementer mid-slice parks the
slice; it used to read as an agent ignoring its nudges. Two readings say the implementer stopped
on its limit, and the newer decides: its transcript, when its newest words are a reply its
harness wrote in the model's place (`transcript.messages.harness_replies` in the adapter), and
the last nudge, when it died within seconds with the limit in its output. Each is read for the
words the implementer's own adapter declares its harness stops on (`quota` → `stops`,
[adapters.md](adapters.md#accounts-windows-and-installs-are-declared-not-coded)); an adapter that
declares none is never parked, and the model's own words are never read for it. The reset is
the one the harness records beside its words (`quota` → `resets_at`), else the one its words
name, read against when they were written and within the limit's own hours. The implementer's
running items - those no `role:` gives to another actor - move to `blocked` with
`needs: quota (resets 26 Sep 04:30)`, an alarm tells a person, and no nudge is sent while they
wait. When the reset has passed they move back to `running`, with any `needs:` they carried
there, and the cycle goes on to the nudge, which resumes the same session through the adapter's
`resume` command; a stop is parked, and resumed after, once.*

A limit whose words name no reset parks with `needs: quota (retry 26 Sep 23:10)`: one nudge
tries again `watchdog.quota_retry_hours` after the stop, or within the limit's own hours where
those are fewer, and a nudge that dies on the limit again parks the slice again in the same
cycle, so a reset nobody named costs one nudge a retry and never becomes a loop. Such a park
used to hold with no end: only a newer stop or a transcript that went on could end it, and while
it held nothing ran a turn that could bring either. A newer stop that names its reset gives the
park that time.

A park ends before its time on evidence that the limit is gone. A transcript that went on past
the stop - a person resumed the session, the model answered, in however few words - ends it,
whatever reset it named. With `keyflip.rotation` on, a park asks keyflip each cycle to rotate a
spent window, as the nudge it stands in front of would, and an account with headroom ends it at
once; the rotation used to wait behind the park until the reset. A dry cycle neither rotates nor
writes.

The board is the park's face and the watchdog's state its memory, and the board is read back when
the memory is lost: items blocked on quota with no park in the state - a state file torn or
deleted - are parked again on the stop read now, and resumed when nothing read says the limit
stands.
`ao board` shows the parked items and when they come back, and `ao watchdog explain` names the
park as the verdict. An item someone takes off `blocked` ends the park, and nothing is moved back
for it. The nudges that died on the limit before it was read no longer count toward "agent
stuck": a park that ends forgets the backoff they ran up, a nudge that dies on the limit is told
as the park and not as "nudge failed", and the architect is not woken to judge it as a failed
restart. The park's alarm is raised every cycle it stands and climbs the ladder
([alarms.md](alarms.md)). The quota guard (4) still reads the provider's window through keyflip
where it is installed; a park needs no keyflip.

*In ao since slice QUOTA-PARK-2: a parked item keeps the `needs:` it had before the park on its board line,
as `held-needs:`, and gets it back when the park ends, where only the watchdog's state kept it: a park the
state lost - torn or deleted - and read back from the board resumed its items without it. A park made
again on a fresh stop after the state lost it keeps the `held-needs:` its items' lines hold.*

## The architect at its usage limit

*In ao since slice ARCHITECT-WAKE-QUOTA: a wake that stopped on the architect's usage limit is not
started again before that limit resets, read the way the implementer's quota handling reads it,
and the one notice a report wake's stop rings says when the wakes start again, with the date when
that is a day or more away.*

A wake is detached, so the next cycle reads what it wrote: a line such as `You've hit your weekly
limit · resets 4am` is a usage-limit stop. A report wake's stop holds every wake of the architect
until the reset, and a refill's holds the refills. The block ends at the later of two readings:

- **What the stop names.** A reset it states - `resets in 4h 43m`, `resets Sep 14 at 4am`,
  `resets 4am` - read against the time the wake wrote it and within the limit the stop names; one
  `architect.quota_window_hours` after the stop when it states none. A weekly or monthly limit
  lasts its period, so a clock time already past when it was written is the next day's. Any other
  limit lasts `architect.quota_window_hours`, and a clock time past by more than that is the time
  that has gone (#40). Read with that window, a weekly limit that named only a clock time ended its
  block at once, and the architect could be woken again within minutes, days before its week was
  over.
- **What the machine reads of the provider.** The implementer's quota gate and an account rotation
  read keyflip's window for a provider, kept in `~/.ao/quota.json` by the quota command the adapters
  declare. The wake reads the same reading, for the provider its adapter's `quota` block names
  (`"quota": {"provider": "claude"}`). When that window is spent, at `quota.block_percent`, the reset
  it states - `resets in 3d 4h` for a week - holds the wake as well. A window with headroom stopped
  nothing, and its reset is not read.

With no reset known - a stop that names none, and no spent window read - the block ends one
`architect.quota_window_hours` after the stop, as it always did: a reading only ever moves the end
later.

*In ao since slice ARCHITECT-WAKE-QUOTA-2: a weekly or monthly limit that names no reset held each wake
one short window, so the architect was woken again and again before its period was over. Each wake in a
row that stops on one now waits twice as long as the one before - one window, then two, then four - and
never longer than the period the stop names; a stop that names its reset breaks the run.*

A report wake's stop is told in one notice, `architect at quota`: "wakes are held until …", with
the clock time when that is within a day and the date as well when it is further away. It rings
once, climbs the ladder and is held quiet until that end ([alarms.md](alarms.md)), and it is told
again only when the end moves, as when a stop on the five-hour window is followed by the week's.
`ao doctor` names the same end, and a refill that waits names its own in the cycle's trace.

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
| F26 | rehearsing the catch-up planned for 2026-10-01, a decision request nobody had been shown and no architect could act on was mailed eight times in a day, four times under its anomaly's "needs you" and four under its own unseen alarm | the unseen check rang every request by its age, whatever other alarm to a person already told it | a request an alarm to a person tells is told by that alarm alone, and a resume notice names it once; its own ladder counts from the last cycle such an alarm told it | test_catchup_polish | — |
| F27 | in the same rehearsal, with the jobs back six hours after the plan reset and the usage unreadable, the resume notice named the credits as standing | the notice named each episode the silence carried and each snooze that ended in it, whatever its own known end | what the silence carried is not named once its known end has passed: an episode's own, or the reset the implementer's last reading named | test_catchup_polish | — |
| F28 | installed from a package whose console scripts were not on PATH, launchd ran a missing file every two minutes while `ao watchdog install` said "installed" | install fell back to a clone's `scripts/ao-watchdog` and `bin/ao` whether or not there was a clone | each job names the console script on PATH, the clone's script, or `python -m ao.watchdog` from an interpreter that imports an installed ao; install refuses when there is none, and exits 1 when `schtasks` fails | test_safe_remove | — |
| F29 | after `ao remove --yes` the watchdog, doctor and telegram jobs ran on against the removed project, and the removal had taken the registry and other projects' logs with it | remove ran `python -m ao watchdog uninstall` without reading the result, and from a clone that interpreter has no ao; it never removed the telegram poller; it deleted every file in `~/.ao` whose name held the key | the jobs are removed in the removal's own process and checked gone before any state goes; then exactly the project's own files and its registry row | test_safe_remove | — |
| F30 | an implementer that stopped on its usage limit mid-slice read as stuck: every nudge died on the same limit until the backoff ran out and a person was told "agent stuck", and nothing resumed the slice when the limit reset | a usage limit was read only in an architect wake's log, in one harness's words; a nudge that died on one was a failure like any other, and the implementer's own transcript was not read for one | the stop is read from the harness's own reply or the nudge that died on it, in the words its adapter declares: the implementer's running items are parked `blocked`, `needs: quota (resets …)`, under an alarm that climbs the ladder, never nudged, and resumed once in the same session at the reset, at one retry where none was named, when the transcript goes on past the stop, or when keyflip rotates to headroom | test_quota_park | — |
| F31 | a weekly limit whose stop named only a clock time could be woken again within minutes, days before its week was over, and a block's end was told as "04:00" whatever its day | the stop's reset was read within the five-hour window whatever limit it named, the spent window the machine reads for the provider was not read, and the end was told by its clock alone | a reset is read within the period the stop names; a spent window keyflip reports for the architect's provider holds the wake until the reset it states; the notice names the date beyond a day, and is told again when the end moves | test_architect_wake_quota | — |
| F32 | a cycle cut off between starting the architect and recording it left a headless architect no record named, and the next cycle woke a second into the same session; a cycle killed while writing its state left it torn, and the markers that a park was resumed or a report handed were gone | the process started before anything said so, and the state was written over in place | the journal (`~/.ao/journal-<key>.db`, SQLite, written ahead and synced): a wake or refill is claimed under its session and reports before its process starts, with a lease, and its pid and start are written the moment it has them; a process the journal names holds its step past the lease while its start reads the same, and the next cycle registers it; a process of the architect's own harness already resuming the session is a wake under way; a journal that cannot be written leaves wakes to the process scan and tells a person; the state is replaced whole | test_journal | JOURNAL |

## Scenarios: testing the decision, not the measurement

Unit tests prove each measurement; `tests/test_scenarios.py` proves the
*decision*. A `World` fabricates the process table, the transcript's age, the
mailbox, the board, the reviews and the quota, then runs one dry cycle and
reads the last decision line. Every fault in the catalog is a world the cycle
once misread, and each has a scenario now: orphans that looked like writers, a
shell that mentioned the agent, a request that looked like unread mail, an
architect at quota. The rule going forward: a fault gets its scenario before
its fix, and the scenario stays.

## Installing and removing it

`ao watchdog install` schedules two jobs, and each names a program the scheduler can start:
the `ao-watchdog` and `ao` console scripts when they are on PATH; else a clone's
`scripts/ao-watchdog` and `bin/ao`, run by the interpreter that ran the install; else that
interpreter running `python -m ao.watchdog` and `python -m ao`, when it imports ao from its
own site directories, as a `pip install --user` whose scripts are not on PATH leaves it. When
there is none, install refuses and writes nothing: a job naming a missing file fails every
two minutes and says nothing. Windows tasks are resolved the same way, and install exits 1
when `schtasks` cannot create one.

On Linux the two jobs are systemd user units in `~/.config/systemd/user/`, and nothing needs
root. Each job is a oneshot service that runs it once and a timer that starts the service:
`ao-watchdog-<project>` at once and then every `--interval` seconds, as the launchd job's
RunAtLoad and StartInterval start it, and `ao-doctor-<project>` every fifteen minutes. A timer
fires to the second, not anywhere in the minute systemd allows by default. Install writes the
four files and asks `systemctl --user`, by argument vector and never through a shell, to
reload, enable and restart the timers; it exits 1 with what systemd said when a step fails or
either timer is not active afterwards. The services keep `KillMode=process`: the watchdog
starts each nudge in a session of its own and exits, and systemd's default would kill the nudge
with the unit. An `--interval` below one second is refused, since it would start each cycle as
the last one ends. The output goes to `~/.ao/watchdog-<project>.log` and
`~/.ao/doctor-<project>.log`, as on macOS; a systemd older than 240 cannot append to a file,
ignores that line and keeps the output in its journal. A user's manager runs only while that
user is logged in unless lingering is on: install and `ao watchdog status` say so when it is
off, and `loginctl enable-linger` keeps the timers running after a logout.
`ao watchdog uninstall` disables and stops both timers, removes their files and the link
`enable` made, and checks each timer is gone.

Where no user systemd answers - a container, WSL without systemd, a `su` or `sudo -u` shell
that cannot reach the user's manager - install writes nothing, says why, and prints the two
crontab lines that would run the same jobs, and the command for one watchdog cycle by hand. A
manager the shell cannot reach may still be running timers installed from a login session:
uninstall and `ao remove --yes` leave those as they are and name them, so that a session that
reaches the manager can take them off.

*In ao since slice LINUX-SCHEDULER: `ao watchdog install`, `ao watchdog status` and
`ao watchdog uninstall` on Linux, through systemd user units where there was a launchd job
Linux does not have. `ao remove --yes` takes the timers off in its own process and checks them
gone, `ao doctor` shows the watchdog running from its timer, and the doctor's one cycle of
grace for a watchdog that came back is read from the timer's interval.*

`ao remove --yes` takes the jobs off in its own process — the watchdog, the doctor and the
telegram poller on macOS, the two timers on Linux, the two tasks on Windows — and checks each
is gone. While one is left, the removal stops with the project's state intact and names the job. Then it removes
exactly the project's own files in `~/.ao/`: the names in one table the code that writes them
shares, such as `heartbeat-<project>`, `watchdog-<project>.log` and `push-<project>.ok`, under
the project's key and nothing else, so another project whose name contains it, and the
machine's own files, are not touched. It takes the project's row out of `~/.ao/projects.json`
under the registry's lock, and the registry stays for every other project. What ao archived
for the project under `~/.ao/archive/<project>/` is kept. The dry run lists each job, file and
row by name, as the removal finds them, and a removal that leaves any of them exits 1.

`ao uninstall --yes` takes every job off the machine the same way, one at a time and each checked
gone: on macOS each launchd job whose label is in ao's namespace, the jobs of projects removed or
never registered among them, on Linux each user unit in ao's namespace in `~/.config/systemd/user/`,
the same way, and on Windows the tasks of the projects the registry knows. The
heartbeats of those projects go with the jobs, since one left behind reads as a dead watchdog
([getting started](getting-started.md#5-update-and-uninstall)).

## The instruments

```bash
ao watchdog explain          # one dry cycle: every measurement, every verdict, in order
ao watchdog trace --last 30  # the recorded cycles: time, verdict, the facts that decided it
ao writers                   # who is writing (turns, not processes) and what is orphaned
ao alarms                    # live alarm episodes, their level and age
ao doctor                    # binaries, last wake, last tick, channels, live alarms
ao doctor --check            # the same, quiet, exit 1 on problems — runs every 15 min from its own launchd job or systemd timer
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
  is held until that end. Another request, a projection come true, another
  account or a later end of the architect's usage limit is told again. With no
  quota to wake the architect, one handoff goes to the phone for the reports
  that wait, not one every hour. An open decision
  rings once for its question, an unseen request as it crosses each of its
  thresholds, and a failed wake once for what failed; the phone hears of a wake
  retried after a failure only once it has not failed.
- **One alarm for what waits.** A report an anomaly stands for is told by that
  anomaly's "needs you", which names it, since when it has waited and why no
  architect acts. With wakes switched off, `reports-no-wake` rings only for the
  reports no anomaly stands for, and with an architect at the keyboard
  `present-pending` does the same. An open decision is told by its own alarm,
  `decision-open:<id>`, and not as "needs you" as well. An unseen request any of these
  tells does not ring under its own key beside it.
- **Resolved.** When an episode goes quiet for two hours, the same channels
  hear it end — an alert with no "over" teaches people to keep worrying.
- **Storm-capped.** Twelve sent alerts in an hour and the rest are recorded
  only, with one notice saying so; red still mails.
- **Resumed.** After more than `watchdog.resume_gap_hours` without a cycle, what the
  cycle would ring and what the silence carried go out as one notice, to a person when
  any of it is a person's; an unseen request, an open decision or a hold from before the
  resume ages from the resume, and what the silence carried is not named once its own
  known end has passed.
- **Actionable.** Every message names the project, what stands, since when, how
  many times, and the `ao` command that shows more.
- **Testable.** `ao alarms test --level red` rings every channel for real.
- **Self-monitored.** Heartbeats, sibling checks, `last tick`.
