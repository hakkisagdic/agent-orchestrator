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
ao since 2h            # or a window
ao since --slice claim-admission
```

Output is a digest, not a log dump:

```
Since 19:04 (2h 11m) — slice: claim-admission, in-review, round 8/5 ⚠ over budget

  reviews    3   → NEEDS_CHANGES ×2, APPROVED ×1 (latest 21:10:56)
  findings   1 open: HIGH canonical lease extractor bypass (unchanged since 20:29 ⚠ loop)
  commits    0   · working tree: 8 files modified
  mail       2 sent, 2 acknowledged · 1 report received
  decisions  D-014 non-extractable authority, D-015 timer epoch guard
  cost       612 credits · context 74% ⚠
  agent      active, last write 12s ago
```

The warnings are the point. A digest that only reports activity makes you read it; one that
flags the over-budget round count, the unchanged finding and the context pressure tells you
what to *do*.

## Briefing a fresh session

```bash
ao brief
```

Reconstructs the working context from the ledger, the repository and the transcript tails:
the open slice and its acceptance boundary, decisions that apply to it, the last
verification and what it granted, open findings, what is blocked and on whom, and what the
orchestrator was about to do next.

Paste it into a new session, or let the MCP surface fetch it — see
[`surfaces.md`](surfaces.md). Either way the recovery is seconds, not an archaeology
session.

## Restart checklist

`ao doctor --resume` performs it, but knowing it matters more than the command:

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
