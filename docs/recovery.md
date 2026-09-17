# Recovery and catch-up

Orchestrator sessions end. They hit context limits, get restarted, get closed by accident.
In the source run the background watchers died **five times** — and each time the cost was
not the watcher, it was the twenty minutes of manually re-reading transcripts to answer
"what happened while I was gone?"

That question should cost one command.

## The design rule

**Every piece of state the orchestrator needs must be reconstructible from disk.** Nothing
important may live only in the orchestrator's context, and nothing may depend on a watcher
having been alive.

Watchers are an optimisation for latency, never a source of truth. This is why the ledger,
the event log and the slice states are files: not for tidiness, but so that a fresh session
is equivalent to a continuing one.

## Catch-up

```bash
ao since last          # everything since this orchestrator last looked
ao since 2h            # or a window: 30m, 2h, 1d, today, yesterday, 2026-09-17
ao since HEAD~5        # or since a commit, named by any git ref
ao since 1d --no-mark  # a look that does not reset "last"
```

Output is a digest, not a log dump:

```text
since 2h 11m ago

  0 commit

  3 review
    NEEDS_CHANGES  2026-09-03-190412-4f2c1a9.md
    NEEDS_CHANGES  2026-09-03-202914-4f2c1a9.md
    NEEDS_CHANGES  2026-09-03-211056-4f2c1a9.md

  1 decision
    D-1788462310  Timer guard: an epoch counter or a generation id?  → epoch counter

  2 message in the mailbox now
    20260903-2104-implementer-to-architect-REPORT-claim-admission.md
    20260903-2112-architect-to-implementer-DECISION-timer-epoch-guard.md

  2 alert sent
    acme-api: over budget: round 8/5 — re-specify, split or change actor
    acme-api: agent spinning: 42m busy, nothing committed or changed — needs re-specifying

  board now: running 1 · blocked 1 · queued 4 · done 12
```

The alerts are the point. A digest that only reports activity makes you read it; the alerts
raised while you were away — a slice over its round budget, an agent busy and producing
nothing — tell you what to *do*.

## Briefing a fresh session

```bash
ao handoff --no-send
```

Writes into the mailbox, and prints, what a successor needs from the board, the repository
and the transcript: what the implementer is doing and when it last wrote, HEAD and what is
uncommitted or unpushed, the newest review's verdict, every open decision with its exact
reply syntax, what is blocked and on what, what is running, and what is next in the queue.

The note is written in the project's `language` ([configuration.md](configuration.md)), and so is the
reason the watchdog hands off with. The phone is sent everything above `## What a successor can do`,
which a Turkish note heads `## Devralan ne yapabilir`; either heading is found in any project.

Paste it into a new session, or let an MCP client read the same state through `ao_status`,
`ao_board` and `ao_decisions` — see [`mcp.md`](mcp.md). Either way the recovery is seconds,
not an archaeology session.

## Restart checklist

No single command performs it, and knowing it matters more than a command would. `ao doctor`,
`ao mail list`, `ao doctor --consistency`, `ao since last` and `ao status` answer its steps in
order:

1. Re-arm watchers — they are always dead after a restart.
2. Read the mailbox — messages may have arrived while nothing was listening.
3. Reconcile: does the ledger's idea of the open slice match the repository?
4. Check for a loop or deadlock that developed during the gap
   ([`slices.md`](slices.md)) — gaps are exactly when they form unnoticed.
5. Re-read the newest review artifact; do not trust a remembered verdict.

Step 5 is not paranoia. A remembered verdict is a verdict that was true at some point, and
the whole reason this system exists is to stop treating "it was true earlier" as evidence.
- `ao writers` — how many live turns are in the tree. Expect 0 or 1. Orphans (no
  terminal, dead group leader) are listed separately and are not writers;
  `ao writers --clean` stops them. A single-writer rule that counts processes
  instead of running this once refused to write for hours over three corpses.
- `ao doctor` — the `architect bin` row shows which `claude` a wake will run and
  its version; `last wake` shows whether the previous wake failed and on what.
  A wake that dies with "does not support this model" was running a stale copy;
  ao now resolves the newest one, but the stale copy is still worth removing.


## The governance survives the disk

On 2026-09-07 the whole control plane of a project - authority, the board, the backlog, every
decision - existed on one laptop; the code was safe on a remote and the decisions that
authorised it were not. `ao backup` writes the governance to a destination the project names:

```bash
ao backup --to ~/Backups/ao         # a directory: <project>/<stamp>/ with a manifest of digests
ao backup --to ref                  # refs/ao/backup/latest, a commit made without the index
ao backup --to remote:governance    # that ref pushed, only if the host says the remote is private
```

It covers config, authority, board, backlog, gates and sources, decisions and parked work,
every ledger with its sealed archives, the mailbox, and the review artefacts a grant rests on;
locks and temporary files are not governance. Each backup is recorded in
`.ao/ledger/backups.jsonl`, and `ao doctor` says how old the newest one is, or that there is
none. A remote is refused unless the host confirms the repository is private, because a push
to a public product remote would publish the coordination history.

`ao restore <directory>` puts the files back, restoring only those whose bytes match the digest
they were backed up with and naming the rest, then checks that the authority chain and the
board validate.

## What is deliberately not recovered

The orchestrator's *reasoning* in a dead session is gone, and that is fine — the ledger has
the conclusions and the rejected alternatives, which is what the next session needs. Do not
try to serialise a chain of thought; write the decision down instead.

## When the centre runs out of quota

Delivery does not depend on the centre. Anomaly reports are written before the
quota gate, and the transport is HTTP and files — no model anywhere in it. So a
pending question still reaches a phone when the architect is dead, which is the
property that matters and it holds by construction rather than by care.

What stops is *deciding*. That is the intended fallback: the human decides, from
a phone, in one tap if the question was posed with options.

But "out of quota" and nothing else is not actionable. So exhaustion now writes a
handoff — what is running, what is blocked and on what, which decisions are open
with their exact reply syntax, what is next in the queue, how many credits remain
and when they reset:

```bash
ao handoff --reason "…"      # also runs automatically on exhaustion, hourly at most
```

It lands in the mailbox and on the phone. Twice in one day here the state that
would have unblocked a run existed only inside a conversation nobody else could
reach; this is that state on disk, where a human, a fresh architect or tomorrow's
session can pick it up.

Nothing about push, PRs or closing an epic transfers in a handoff. Those are not
the architect's to give.
