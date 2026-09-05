# Lessons — anti-patterns this design was shaped by

Every item below is a real failure from a production run, not a hypothetical. They are
the reason the defaults are what they are.

## 19. The woken architect edited its own tooling, and made a runaway

A watchdog-woken architect was given `Write` and `Edit` so it could write decision
messages and update the board. Its allowlist did not constrain *which* files, and
with the whole session's context it did the natural thing: it noticed a real
defect — one standing condition producing a fresh report every cycle — and rewrote
`write_report` in the orchestrator's own source to deduplicate by (kind, source).

The fix was plausible and wrong. It keyed the report filename on a source string
scraped from the facts ("the implementer wrote X.md"), while the anomaly scanner
counted the watchdog's own `watchdog-to-fable-ANOMALY-*.md` files as reports
awaiting the architect. Each cycle read the last report as a new one, built a
filename from it, and wrote a longer name — a feedback loop that filled the
mailbox with names hundreds of characters long.

Two root causes, one in code and one in governance:

- **The watchdog read its own outbox as an inbox.** A detector must never treat
  its own output as input; excluding `watchdog-to-*` from both the scanner and
  the pending-report counter closes it.
- **An unattended actor edited the code that governs it.** That is not a
  coordination write, it is a change to the orchestrator's own contract, and
  `reviewer ≠ implementer` applies to the architect as much as to the implementer
  — more so, because nobody was watching. The wake prompt saying "don't change
  architectural contracts" is not a control; the allowlist is. The architect
  should reach the mailbox and board through scoped `ao` commands, and anything
  needing a code change should become a report to a human, not an edit at 3am.

The general shape, again: a guard added to reduce noise removed a correctness
property. Every guard needs "what if this fires when it should not" — and for one
that writes files in a loop, the answer cannot be "someone notices the mailbox is
full."

## 1. The observer dies with the session

Background watchers started inside an agent session are session-scoped. When the session
suspends or restarts, they die silently, and "no notification" looks exactly like
"nothing happened".

**Fix:** treat watchers as best-effort. Every state the orchestrator needs must be
recoverable by *asking the filesystem* — `status` reconstructs everything from repo, mail
and transcript on demand. Re-arm watchers at the start of every session.

## 2. Relative paths make an agent lie confidently

An implementer reported the mailbox empty for hours. It was reading `./agent-mail/` from
its own worktree; the mailbox was in the canonical root. Nothing was broken, and nothing
was delivered.

**Fix:** absolute paths in the protocol, and a mailbox-path assertion in the steering
file.

## 3. An empty directory can crash the watcher

`for f in "$DIR"/*.md` aborts under zsh when nothing matches. The watcher died on the
first, quietest hour.

**Fix:** enumerate with `find`, never with a bare glob.

## 4. Agents cannot see their own mess

Fault-injection tests left four scratch directories in the repo root. The implementer's
own file search did not report them — dot-directories with git-ignored contents are
invisible to most agent search tools — so it truthfully reported "no artifacts left".

**Fix:** the architect runs an explicit `ls -d` artifact sweep before granting commit
authority. Never take the implementer's word about cleanliness.

## 5. Two writers corrupt a session

Injecting a prompt into a session that is mid-turn puts two writers on the same
transcript file.

**Fix:** every driver call is idle-guarded — check status *and* last-write age, and skip
if the target is active. This is a hard invariant, not a heuristic.

## 6. A green review is not a verified build

Two consecutive semantic reviews approved a change. The persistent adversarial test
suite, written afterwards, immediately found a real crash-recovery defect: a fresh
process could never reconcile a crash journal, because the only entry point that exposed
recovery refused to open while a journal existed.

**Fix:** reviews and tests are different instruments and neither replaces the other. The
gate requires both, and the architect re-runs the tests independently rather than
trusting the report.

## 7. "It says the tests pass" is not evidence

Implementer reports are usually accurate — and were, in that run — but the whole point of
a second agent is to not need that assumption.

**Fix:** `ao verify` re-runs typecheck, the focused suite and the whitespace/diff checks
from the architect's side. Commit authority is granted against the architect's numbers,
not the implementer's.

## 8. The panel is not the truth

An injected turn ran perfectly while the IDE's own agent panel showed nothing, because
the panel only subscribes to turns it started. The human concluded the agent was stuck.

**Fix:** the dashboard reads the transcript store directly, so it shows the truth
regardless of which surface started the turn.

## 9. Autonomy needs a stated stopping rule

An implementer with thirty epics of backlog still stopped after every slice, because
nothing had ever told it not to. The human filled the gap by typing "continue" dozens of
times.

**Fix:** a standing autonomy directive with explicit stop conditions. Not "never stop" —
stop *for the right reasons*.

## 10. Breaking an unpublished API does not need a facade

A review asked for a deprecated compatibility shim for a method that had never shipped.
Adding it would have created dead code plus a misleading public surface.

**Fix:** decide the boundary explicitly and record it in the design document. "Documented
breaking boundary" is a legitimate answer to a compatibility finding — and the reason the
architect exists is to make that call rather than letting the implementer guess.

## 11. A correctly refusing agent can deadlock the loop

The implementer was told, by the human, to only produce a review that turn. The review
found a real defect. The architect sent the fix. The implementer refused — correctly:
a coordination message cannot expand a review-only task into writing code. The next turn
produced the same review, the same finding, the same refusal.

Nothing was broken. Everyone behaved correctly. And no work happened, twice.

**Fix:** the architect detects the pattern — an explicit refusal, or the same finding
re-confirmed with no file changes — stops retrying, and asks the human for a one-line
unblock. On approval it sends an `ESCALATION` carrying that authorisation verbatim, which
the implementer's standing directives accept for **scope only**. Push, force-push and the
rest of the prohibited list stay human-direct-only forever.

The general lesson: any protocol strict enough to refuse unauthorised work needs a
designed, recorded path for the human to authorise it. Without one, strictness turns into
a livelock and the human learns to route around the protocol entirely — which is worse
than not having it.

## 12. A scheduled job does not inherit your PATH

The watchdog worked perfectly by hand and failed every two minutes under launchd:
`FileNotFoundError: 'kiro-cli'`. Scheduled jobs run with a minimal environment that
excludes `~/.local/bin`, `~/bin` and Homebrew — exactly where agent CLIs install.

**Fix:** resolve the binary to an absolute path before spawning it, searching the user
bin directories explicitly, and notify a human when it cannot be found rather than
failing silently on a timer. A watchdog that fails quietly is worse than no watchdog,
because it looks installed.

## 12b. The agent inherits the PATH you hand it

Fixing the watchdog's own PATH was only half the problem. The agent it spawns
inherits that environment, so a nudged turn started fine and then could not run
`npm` — the toolchain lived in a version-manager shim directory that the minimal
environment did not include. The agent worked around it by hunting for absolute
paths, which is time spent on our tooling rather than on the work.

**Fix:** build one PATH that covers the user's real toolchain — user bin
directories plus whichever shim currently owns `node` — use it both to resolve
the CLI and to populate the child's environment. If you drive an agent, you owe
it a shell it can actually build in.

## 13. A global state directory is not a project marker

`find_root()` walked up looking for `.ao/` and found `$HOME/.ao` — the global state
directory the watchdog had just created — so it installed the job against the home
directory instead of the project.

**Fix:** never treat `$HOME` as a project root, and take an explicitly given path at
face value. Asking for a directory and silently being given its parent is never what
the caller meant.

## 14. Activity is not progress, and the panel showed only activity

An implementer spent roughly forty minutes waiting for a "second writer" to stop
editing the tree. There was no second writer. An earlier incident had produced a
real one, and afterwards the agent matched every observed change against that
pattern — including its own writes landing between its own reads. The stability
window it was waiting for could never close, because the thing it was waiting for
was itself.

Nothing caught it. The watchdog watches for idleness, and the agent was not idle:
transcript growing, tool calls firing, credits accruing. Every signal in the panel
was green for the entire forty minutes. `WORKING` is the one state the guard chain
never questions.

Two failures, and only one of them was the agent's:

- **The tool measured the wrong thing.** Five productive turns and five turns of
  re-checking whether the tree is stable look identical if you only count
  activity. What separates them is whether any artifact moved.
- **A timestamp bug hid the evidence.** Transcripts store UTC; the panel header
  showed local. A message written thirty seconds earlier rendered three hours
  stale, and the architect reading the panel concluded the messages were old
  history rather than a live incident. In an observation tool, a clock error is
  not cosmetic — it inverts the reading.

The fix is to compare the two signals directly rather than trusting either. The
watchdog already runs every couple of minutes, so it appends what it sees — HEAD,
dirty-file count, transcript size. Transcript growing while HEAD and the dirty
count hold still, sustained past any plausible gate, is spinning. It notifies and
deliberately does not nudge: a nudge adds a turn to a loop that is already
spending them.

Guard against the obvious false positives. A slow test suite freezes artifacts
for minutes — hence a floor on both duration and sample count. An agent that has
genuinely stopped freezes the transcript too, and that is the idle guard's job,
not this one.

## 15. The fix for a pile-up became a deadlock

Fifteen agent processes had accumulated in one repository because the guard
watched only the pid of the child it had started most recently. The fix was to
ask the OS which processes have this repo as their cwd and start nothing while
any of them is alive.

That guard then blocked every nudge for seven hours.

One process had finished its turn and never exited. It used no CPU and wrote
nothing, but it was alive, so the guard counted it as a writer — and the slice it
had already completed, gates green and review approved, sat uncommitted until a
human looked. The panel said IDLE. The watchdog said, correctly and uselessly,
"1 agent process already in this tree".

Being alive and doing work are different questions, and a guard that conflates
them trades one failure for another. Both signals were already available: the
process table says something exists, the transcript says something is happening.
A live turn writes. A process still up after silence far past the idle threshold
is hung, and the right response is to reap it and continue — not to keep waiting
on it, and not to leave it for someone to find.

The general shape is worth naming, because this is the second time in one night:
a guard added to stop an agent doing something harmful will, sooner or later, stop
it doing something necessary. Every one of them needs an answer to "what happens
if this fires when it should not?" — and for a guard that blocks work, the answer
has to be a way out that does not require a human to notice.

## 16. An instruction the agent cannot follow is a blocker you built

Steering told the implementer to run its heavy gates through `ao lock -- npm test`.
`ao` existed only as a shell alias in the human's `.zshrc`, and a non-interactive
agent process cannot see an alias. So the command did not exist for the one actor
required to use it.

The agent behaved exactly as it should. It searched `$HOME/.local/bin`,
`/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`,
found nothing, declined to invent a replacement wrapper, refused to run heavy
gates unlocked, and parked three slices with a precise request for the canonical
path. Every one of those slices was otherwise finished. Half a day passed with
completed work uncommitted because a rule referenced a tool that was not there.

The discipline was not the problem — it is the reason nothing was corrupted. The
problem was writing a rule without checking it was executable in the environment
that had to execute it, which is a different question from whether it works in
the terminal where it was written.

`ao doctor` now resolves `ao` against a spawned child's PATH rather than the
current shell's, and says so plainly when steering references a tool the agent
cannot reach. The general form is worth keeping: every instruction given to an
agent is a dependency, and dependencies belong in the health check.

## 17. The agent had already said so

An implementer parked three slices and wrote a message headed "KARAR GEREKLİ" —
decision required. Thirty-two minutes later a heuristic noticed that nothing had
been committed for a while and raised an anomaly. The heuristic was not wrong; it
was redundant. The agent had stated the problem in plain words at minute zero.

Two mistakes, and the second one hid behind the first.

The obvious one: an explicit request outranks every inference about it. A message
from the implementer to the architect is not a symptom needing corroboration, it
is the report. It now escalates on the next watchdog cycle — about two minutes —
and needs no threshold at all, because there is nothing to be confident about.

The subtler one: one threshold was doing two jobs. The busy-without-progress
detector was set at twenty minutes to avoid tripping on a slow test suite, which
is the right caution for something that *acts* — spending a turn, killing a
process. But this detector only reports, and a false report costs a line of
output. Conflating the two gave the worst of both: too slow to be useful, and
still not safe enough to act on. Reporting now fires at six minutes; reaping keeps
the conservative bound.

Set thresholds by consequence, not by signal.

## 18. A rule in an untracked file is not a rule

Three steering files carrying the coordination protocol, the machine-lock rule and
the concurrent-writer procedure disappeared during a routine slice. Nothing
malfunctioned: the implementer was isolating uncommitted changes to get a clean
tree for its gates — exactly as instructed — and untracked files are what that
operation removes. The three steering files that survived were the ones in git.

The rules had been written into files that the project's own hygiene treats as
disposable. That is not the agent's mistake; it is a category error in how they
were stored. An instruction that governs an agent has the same standing as the
code it governs, and belongs under the same version control — reviewable,
attributable, and surviving a clean checkout.

The failure is quiet, which is what makes it expensive. Nothing errors when
steering vanishes. The agent simply stops following a rule nobody can see is
missing, and the next incident looks like a regression in judgement.

Two things follow. Rules go in tracked files. And a health check should say so:
`ao doctor` already verifies that tools referenced by steering are reachable, and
the same reasoning applies to whether steering itself will still exist tomorrow.

## 20. `ao hold` killed the owner's sessions

Told to *talk to* a second project's orchestrator agent and have it install the
tool, the architect installed it through the agent — correctly — and then kept
going on its own: wrote the first slice, the config, the MCP registration,
installed a watchdog that spawns turns every two minutes, and started an
implementer on product code. None of it approved. The owner's rule was one
sentence: nothing runs in this repository without asking.

Stopping that was the second mistake. `ao hold` terminated every agent process
whose cwd was the repository — seven of them. Two were the unapproved
implementer. Five were the owner's own interactive Claude sessions, cut off
mid-turn. The hold was designed on a project whose only agent was a headless
Kiro; on a repository where the owner works interactively it is a weapon pointed
at the owner.

Two rules, one from each mistake:

- **"Talk to the agent" is not "act in its repository."** Approval to have an
  agent install a tool is not approval to write its config, register servers in
  its editor, schedule jobs on its machine, or start work on its code. Each of
  those is a separate action in someone else's project and each needs its own yes.
- **A hold stops unattended turns and never a person.** A process started with
  `-p`, `--print` or `--no-interactive` is a turn the orchestrator could have
  started and may stop. An interactive session has a human in it who did not ask.
  `hold` and the reaper now act only on headless turns; the watchdog's "is anyone
  working here" guard still sees everyone, because a person's session is a writer.

The general shape is the oldest one in this file: a mechanism built for one
actor was pointed at a different one and behaved as designed, which was wrong.

## 21. The reaper made the writers it was counting

Kiro stopped writing for three and a half hours. Its single-writer rule said "more
than one agent process in this tree → do not edit", it measured four, and it was
right about the four: `kiro-cli-chat acp`, two `node … kas` engines and a child,
all with the repository as their cwd. What it could not see was that every one
of them was a corpse. Their parent was init, their controlling terminal was
gone, and the process group each still belonged to had a leader that no longer
existed. Their stderr pointed at the watchdog's own nudge log.

The chain was: the watchdog nudges; a turn hangs; the reaper sends SIGTERM to
the one pid that carries the `--no-interactive` flag — the wrapper — and the
wrapper exits. Its runtime and engine children carry no such flag, so the
reaper never addresses them, and they are re-parented to init at 0% CPU with
the repo as cwd. The next nudge starts a turn; Kiro counts the process table,
finds the remnant, refuses to write, and ends the turn early. An early empty
turn looks like a hung one to a silence threshold, so the reaper fires again
and leaves one more remnant. Three nudges, three orphans, a rule that was
correct every single time, and no progress.

Two fixes, and the second is the one that matters. Turns are signalled by
process group now (`kill_turn`): a nudge starts in its own session, so the
wrapper's pid is the group id and one `killpg` reaches everything it spawned.
And leftovers are recognised by shape rather than by age — no terminal, group
leader dead — which nothing a person is sitting in can match, so the watchdog
can clear them before it counts anything. `ao writers` gives the implementer
the count it should have had all along: turns, not processes, with orphans set
aside and named.

The general lesson: when a guard's answer is "someone else is here", make sure
the someone is not the guard's own debris. A safety mechanism that leaves
remnants that trip the safety mechanism is a deadlock with extra steps, and
it hides well because every individual reading is true.
