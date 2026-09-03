# Lessons — anti-patterns this design was shaped by

Every item below is a real failure from a production run, not a hypothetical. They are
the reason the defaults are what they are.

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
