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

## Round budget

Count review rounds per slice. Default budget: **5**.

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
