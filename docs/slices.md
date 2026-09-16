# Slices

A **slice** is one closed acceptance boundary, worked by one actor, in one lane. It is the
unit everything else is counted against: rounds, cost, decisions, commits.

## An acceptance boundary is mandatory

`ao slice start` refuses a slice with no written boundary. Not a title — the conditions
under which the work is finished:

```yaml
slice: claim-admission
boundary:
  - claims acquired under the lifecycle writer lock
  - raw Epic 14 lease never leaves the module
  - settlement bound to the exact lease, claim and payload
gates: [typecheck, focused, full, diff-check]
non_goals:
  - no new Epic 14 public API
  - fixture-conformance only; no host qualification claim
```

The reason is not bureaucracy. A cloud agent cannot ask a cheap follow-up question, a local
one will invent an answer, and *you* will not remember in three days what "done" meant. The
boundary is also what the reviewer reviews against — without it, review drifts into taste.

## A boundary too big for one sentence is a file

A sentence stays in the board row as `acceptance: …`. Above `boundary.inline_max_chars`
(400 characters) the boundary is a file in the repository and the row carries only a
pointer to it, `boundary: docs/slices/B8a.md@1a2b3c4` - one source of truth, never two:

```markdown
# B8a — claim admission journal
## Invariant
A claim is journalled before it is admitted, and never after.
## Scenarios
1. A duplicate delivery is refused with the first claim's id.
## Paths
- src/claims/journal.ts
- src/claims/sequence.ts (new)
## Out of scope
- the public claim API
```

`ao review` reads the file at the commit the pointer names and hands it to the reviewer.
If the file has changed since, the change goes with it as a diff, so a boundary that moved
mid-slice is visible instead of silently becoming a different contract. Such a slice is
reviewed without `--boundary`.

The declared paths - the file's Paths section, or `paths:` on a row - are checked against
the repository whenever `ao board` or `ao doctor` reads a running or queued item: a path
that does not exist and is not marked `(new)`, a file the acceptance names outside the
declared paths, and the file defining a symbol the acceptance names in backticks when it
is outside them. It is prose matching, so it names what it found and blocks nothing. The
point is to find a boundary conflict at registration, when widening or splitting is
cheap, rather than three hours into the slice.

## Size is a tripwire that asks a question

`ao review` and `ao verify` measure a candidate by kind - product, tests, fixtures,
generated and pure deletion - and by path, never as one number: 400 lines of fixtures
are not 400 lines of concurrency. The guideline is `size.guideline_product_lines` (400)
and `size.guideline_paths` (5). Over it, nothing is refused: the boundary has to say why
the slice is one invariant that cannot be split without leaving a seam unreviewed -
`one slice: …` on the row, or a "Why one slice" section in its file - and that statement
goes to the reviewer to judge. Without one, the reviewer is asked whether it should be
split. Only far above, at `size.refuse_product_lines` (4000), where no review is
credible at any length, does `ao review` refuse before spawning.

Code that has passed its gates is never reshaped to satisfy a size number. On
2026-09-08 a candidate whose fixes were green measured 464 lines, and rewriting it to
reach 400 would have thrown away the verification it had. When the overshoot is within
`size.small_overshoot_pct` (25%) and the newest verification passed on this candidate,
`ao review` says so. The guideline relaxes once sectioned review (#26) lands, since the
timeout that motivated it is gone.

## States

```
specified → in-progress → in-review → changes-requested ─┐
                              │                          │
                              ▼                          │
                          approved → committed           │
                              ▲                          │
                              └──────────────────────────┘
```

Every transition is one line in `.ao/ledger/slices.jsonl`. That file is why a restarted
session knows what it was doing.

Two states people forget to model: **blocked** (waiting on a human decision or supplied
input — see escalation in [`protocol.md`](protocol.md)) and **abandoned**, which is a
legitimate outcome and should be recorded rather than quietly dropped.

## Before adding a mechanism, walk the ladder

Slices grow because every finding is answered by building something. B8 took twelve review
rounds that way: a late gate check produced a resume machine, a consume race produced a
callback held under two locks, and each new mechanism gave the next round a new hole to
find. It ended with a decision that said, in effect, *delete, do not add*.

So before writing a new mechanism, answer these in order and stop at the first yes:

1. **Does this need to exist at all?** A finding can be a boundary that was written wrong.
   Correcting the contract is a legitimate fix and usually the cheapest one.
2. **Does the repository already do it?** The same guarantee is often already enforced
   somewhere else in the chain.
3. **Does the standard library do it?** ao ships with no runtime dependencies; the answer
   is in the standard library or it is written by hand and owned by our tests.
4. **Does ordering solve it instead of machinery?** Several races here dissolved by fixing
   the order of two writes rather than adding a guard around them.
5. **Can it be deleted instead?** Removing the surface that produced the finding is a fix,
   and it is the only one that cannot itself be defective.
6. **Can it be one function with a table of golden values?** Parsers, projections and
   counters belong here, not in new subsystems.
7. **Only then:** the smallest mechanism that satisfies the invariant.

A slice that adds a mechanism records in its report which rung it stopped at and why the
ones above it did not apply. The ladder is borrowed from [ponytail](https://github.com/DietrichGebert/ponytail)
(MIT), whose measured claim is roughly half the code for the same task; nothing of it is
installed here — see [`upstream.md`](upstream.md).

## Round budget

Count review rounds per slice. Default budget: **5**.

Every completed prospective review records the running board item's structured slice ID
and reviewed boundary. `rounds()` uses that slice ID—not HEAD or candidate bytes—as the
accounting key: a review attributed to another slice can neither consume nor reset the
current slice's budget, even when both slices review the same HEAD. Unscoped legacy
artifacts are not charged to a slice because their owner cannot be established. Retrospective
reviews are outside the prospective fix/re-review budget, and `UNAVAILABLE` or `INVALID`
attempts are not rounds.

This exists because of a measured failure. One slice in the source run went through **nine
review rounds** — nine cycles of finding, fix, re-review — and nothing in the system
noticed. Each round was individually reasonable. The ninth was not.

On exceeding the budget, `ao` stops the slice and requires an explicit choice:

| Option | When it is the right one |
|---|---|
| **Re-specify** | The boundary was ambiguous, so every round discovers a new interpretation. |
| **Split** | The slice contains two problems and they keep trading places. |
| **Change actor or model family** | The same engine keeps producing the same blind spot. |
| **Accept and document** | The remaining findings are genuine platform limits, not defects. |
| **Extend the budget** | Rare, deliberate, and recorded with a reason. |

Notice what is not on that list: *try again*. If four rounds did not converge, a fifth of
the same is not a plan.

## Loop and deadlock detection

Rounds measure slow progress. These detect *no* progress, and they are mechanical because
human attention is exactly what fails here — in the source run the deadlock was spotted by
the user, not by the orchestrator watching for it.

**Finding fingerprint.** Normalise each finding to `(severity, rule-or-title, file)` and
hash it. If the same fingerprint appears in two consecutive reviews **with no file changes
between them**, that is not iteration — that is a loop.

**No-progress window.** Implementer active, repository unchanged, across two consecutive
turns.

**Unacknowledged mail.** A message present for two turns.

**Explicit refusal.** The implementer declines a directive citing a standing instruction.

Any of these fires the escalation path in [`protocol.md`](protocol.md): stop, do not retry,
ask the human once, carry the answer verbatim. The orchestrator is forbidden from
rephrasing and resending — that is the behaviour the detector exists to prevent.

## Cost per slice

Every turn's telemetry is attributed to the slice that was open ([`telemetry.md`](telemetry.md)):

```
claim-admission   9 rounds · 4h 12m · 2,431 credits · 3 decisions · 1 commit
```

That line changes behaviour in a way no per-turn number does. A slice that cost nine rounds
and two thousand credits is telling you something about the specification, not about the
engine — and you only see it once someone is counting.

## Commands

```bash
ao slice start claim-admission --boundary-file boundary.yml
ao slice status                  # state, rounds used, cost, open findings
ao slice block "waiting on Authenticode certificate"
ao slice abandon --why "superseded by the metadata authority slice"
ao slices --open
```
