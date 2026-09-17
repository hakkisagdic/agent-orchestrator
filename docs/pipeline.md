# The review pipeline — never wait on a reviewer

Today `ao review` blocks. The implementer runs it, then sits there: on 2026-09-07 one
review took 11m48s and returned APPROVED, and the next was killed at the 900s ceiling
after burning ~15 minutes of model work that was then discarded. In both cases the
implementer did nothing while it ran, and in the second case the work was lost entirely.

Three things are wrong, and they are separable:

1. **The reviewer is synchronous.** One slice at a time, and the implementer idles.
2. **A review is one indivisible call.** Cut it off at any point and everything is lost.
3. **A candidate can be too big to review at all**, and nothing says so until the kill.

This document specifies the target design. It is written as invariants so each piece can
land as its own slice with its own tests.

## What must not change

The safety model is not up for negotiation, and asynchrony is exactly where it would
quietly erode:

- **A grant is bound to the exact candidate that was reviewed.** Not to a branch, not to
  a slice name, not to "the latest index".
- **The reviewer is not the author.** A different session, different context.
- **A review that did not produce a verdict is not a round.** Timeouts and crashes never
  consume budget and never authorise anything.

Asynchrony breaks the first invariant in an obvious way if you are careless: the
implementer submits slice A, moves on, changes the index, and when A's APPROVED comes
back the index no longer holds what was reviewed. The fix is to pin the candidate as a
git object at submit time and to keep one slice per worktree.

## 1 — Submit and collect

*In ao since slice PIPELINE-SUBMIT (#27): `ao review submit`, `ao reviews`, `ao review collect`,
`ao commit-ok --review`, and `review.max_inflight`. The pinned tree is kept as a private index
file under `.ao/reviews/`, so the running review reads that tree whatever the live index holds.*

```bash
ao review submit --boundary '…'      # returns R-1788… immediately
ao reviews                            # what is in flight, with elapsed and progress
ao review collect --any               # the first finished one; blocks only if you ask it to
ao review collect R-1788…             # a specific one
```

At submit time ao records, in `.ao/reviews/R-<id>.json`:

| field | why |
|---|---|
| `tree` (`git write-tree`) | the candidate as a durable git object — survives any later index change |
| `head`, `changed_paths`, `diff_digest`, `status_digest` | the existing candidate evidence, unchanged |
| `worktree` | which working copy this candidate belongs to |
| `boundary`, `sections` | what the reviewer was asked, split as in §2 |
| `state` | `running` / `finished` / `timeout` / `failed`, with `pid` and `started_at` |

**Invariant S1.** `ao commit-ok --review R-<id>` grants only if `git write-tree` in the
current worktree equals the recorded `tree`. A candidate that drifted is refused with the
two digests printed; the implementer either restores it or re-reviews. Nothing about
"the same slice" or "the same branch" is accepted as a substitute.

**Invariant S2.** One slice, one worktree, one index. Parallel slices never share a
working copy, so a pinned tree cannot be disturbed by the next slice's edits. This is the
existing `<implementer>/<slice>` branch-per-slice rule, now load-bearing.

**Invariant S3.** At most `review.max_inflight` reviews run at once (default 2). Submit
beyond that is refused, naming what to collect first. The reviewer shares the human's
model quota with the architect; unbounded fan-out spends someone else's window.

## 2 — Sectioned reviews, so a cut-off costs one section

*In ao since slice REVIEW-SECTIONS (#26, #78): sections come from the slice's lenses or the
boundary's numbered scenarios; each answer is appended to `.ao/reviews/sections/<key>.jsonl`
before the next is asked, a re-run of `ao review` on the same candidate asks only what is
unanswered, and the verdict is computed from the sections' counts.*

*In ao since slice LANGUAGE-PROMPTS: the prompt, the headings it asks for and the markers ao writes
into it are in the project's `language` ([configuration.md](configuration.md)) - `--- CANDIDATE DIFF ---`,
`--- CONTEXT (read-only; not under review) ---` and `--- THIS SECTION'S QUESTION ---` in English,
`--- ADAY DIFF ---`, `--- BAĞLAM (salt okunur; incelemenin konusu değil) ---` and
`--- BU BÖLÜMÜN SORUSU ---` in Turkish. A section's answer is read the same in both
([protocol.md](protocol.md#verdicts)), and the journal's key holds no word of the prompt. A Turkish
project is handed the prompt it always was, byte for byte, so a review cut off there resumes; one now
written in English resumes as well, unless its prompt leaves room beside the diff for a different set
of a waived range's commit messages, and then its sections are asked again.*

A review of eight scenarios is eight questions, not one. ao splits the prompt into
**sections** — from the numbered scenarios in the boundary, or failing that from the
candidate's file groups — and runs them as separate bounded calls, appending each result
to `.ao/reviews/sections/<key>.jsonl` as it lands.

**Invariant P1.** Every finished section is durable before the next one starts. Kill the
process, reboot the machine, hit the ceiling: what was answered stays answered.

**Invariant P2.** Running `ao review` again on the same candidate, boundary and reviewers asks
only the sections with no recorded result. A resumed review is the same review — same
candidate, same sections, one round.

**Invariant P3.** The verdict is computed, not asked for: any section reporting BLOCKER
or HIGH makes the review `NEEDS_CHANGES`; all sections clean makes it `APPROVED`; a
review with unanswered sections has no verdict at all and is not a round.

**Invariant P4.** Each section completion writes a heartbeat line: elapsed, sections done
of total, reviewer id, child pid. `ao reviews` shows it. Silence is a symptom, and today
there is no way to tell a working reviewer from a dead one.

## 2b — Lenses: a review is a set of declared questions

Twelve finder lenses over this repository found 110 distinct defect sites on 2026-09-08,
and 91% of them were found by exactly one lens. A single general review is one lens, and
the other findings are simply missed. A lens is a failure mode with the question it asks:
`correctness`, `concurrency`, `clock`, `durability`, `subprocess`, `portability`, `secrets`,
`authority` and `tests`.

A slice names its lenses on its board row, `lenses: correctness, clock, durability`; `+x` adds
one to the defaults and `-x` waives one, and both are recorded in the review's evidence with
the lenses actually asked. With `review.lenses` set to `auto`, the defaults come from what the
candidate touches - a timestamp brings `clock`, a file write `durability`, a spawned process
`subprocess`. The default is `declared`: every lens is a separate reviewer call, and a
reviewer's quota is someone's window. Each lens is a section, so a lens pass is bounded and
resumable like any other, and the verdict is per lens in the artefact: a clean correctness
pass no longer implies a clean concurrency pass.

## 3 — No deadline on thinking; a deadline on silence

*In ao since slice SILENCE-DEADLINE (#25): a reviewer whose process group spends no CPU for
`review.stall_minutes` (10) is killed as stalled - a streaming answer spends CPU, a hung call
spends none - and a stalled section is journaled unanswered with its partial output, so a
re-run asks it again. One ruling against the text below: `review_timeout` stays each call's
ceiling. It is the bound where CPU cannot be read, and the chain budget (#100) that a later
decision set rests on it; a project that wants long thinking raises it. The whole review
still has no deadline: it is as many bounded calls as it has sections, and the size guideline
that Z3 describes became a question rather than a refusal (#34).*

Once nobody is waiting, a wall-clock cap on the whole review has no purpose left. It was
never a quality control — it was a way to stop a blocked implementer waiting forever, and
§1 removes the waiting. So:

**Invariant Z0.** A review has no total deadline. It runs until every section is
answered, however long that takes. `--timeout` as a cap on the whole review is gone.

What still needs a deadline is *silence*, not thought. A section that is producing output
is working; a section that has produced nothing for a long time is stuck, and killing it
is the only way to find out.

**Invariant Z1.** A section is killed only when it **stalls** — no output and no
heartbeat for a stall window (ten minutes proposed; not a setting until this lands) — never for
having taken a long time.
A stalled section is recorded as unanswered with its partial output kept as evidence; the
rest of the review is untouched and running `ao review` again retries just that one.

**Invariant Z2.** Total work is bounded by structure, not by a clock: a review has a
finite number of sections, each bounded by the stall detector. `ao reviews` shows
cumulative elapsed and, where the adapter reports it, spend — so an expensive review is
visible while it runs rather than after the bill.

**Invariant Z3.** Before spawning anything, ao measures the candidate. Over the slice
budget (≤5 paths, ≤400 changed lines) it is refused with the measurement and the budget
side by side. This is now a *quality* rule, not a scheduling one: a 5,592-line candidate
under one boundary gets a shallow review no matter how long it is given. The answer to a
too-large candidate is to split it — never to wait longer, and never to raise a limit.

## 4 — How the implementer works once reviews are asynchronous

The loop the owner asked for, stated as a rule the implementer follows every time it is
free:

1. If any review has finished → **handle it first.** Land it (APPROVED, 0 BLOCKER/HIGH) or
   fold its findings into that slice's worktree. Finished work outranks new work; the
   point of the pipeline is throughput of *landed* slices, not of started ones.
2. Else if a slice is code-complete with green gates and `inflight < max` → **submit it**
   and immediately take the next READY item.
3. Else if `inflight == max` → **keep developing** the current slice. Never block on a
   reviewer.
4. Else → take the next READY item.

Concretely, with `max_inflight = 2`: develop A → submit A → develop B → submit B → develop
C; when A returns, stop C and finish A; submit C when a slot frees; when B returns, finish
B; and so on. At most two reviewer calls are ever in flight, at most three slices are ever
open, and the implementer is never idle waiting for a verdict.

**Invariant W1.** An open slice whose review has returned is never left unattended while
new work starts. `ao status` names every slice with a returned-but-unhandled review, and
the watchdog treats "finished review, nobody handling it" as an anomaly.

**Invariant W2.** Parking is still free. A slice whose review budget is exhausted parks
with a decision request and does not hold a slot.

## Landing order

| slice | what it is | depends on |
|---|---|---|
| A | no total deadline, stall detection, size precondition (Z0–Z3) | — |
| B | sectioned execution with a durable journal and heartbeat (P1–P4) | A |
| C | submit/collect/resume with tree-pinned grants (S1–S3) | B |
| D | implementer pipeline rule and the unattended-review anomaly (W1, W2) | C |

A and B are worth having even if C never lands: they turn a lost 15 minutes into a
resumable one. C is what stops the implementer idling. D is what stops the pipeline from
filling up with reviewed-but-unlanded work.
