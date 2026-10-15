# Slices

A **slice** is one closed acceptance boundary, worked by one actor, in one lane. It is the
unit everything else is counted against: rounds, cost, decisions, commits.

## An acceptance boundary is mandatory

`ao source import` queues no tracker item without a written boundary; it holds the item in
the inbox. Not a title — the conditions under which the work is finished:

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

A slice's state is the section of `.ao/board.md` its row sits in - inbox, queued, running,
blocked, verified, done or rejected - and that is what a restarted session reads back. The
stages above are read onto it: specified is `queued`, in progress is `running`, the review
stages inside it are the verdicts the review ledger holds for the slice, and committed is
`done`.

*In ao since slice SLICE-LIFECYCLE: every move ao makes between those sections is a line in
`.ao/ledger/slices.jsonl` - which slice, from which section to which, who moved it, when and
why - chained like the other evidence ([ledger.md](ledger.md#slices)). ao makes a move with
`ao slice start`, `ao slice block` and `ao slice abandon`; an item enters the board with
`ao board add` or `ao source import`; and the watchdog parks a slice on quota and gives it
back. A row moved by hand has no line, and where ao recorded an earlier move of it
`ao slice status` and `ao slices` name the disagreement - the board holds it under one section
and the last move ao recorded put it under another - and list a row removed by hand from that
last move. Landing is still a hand move: the playbook moves a slice to `done` after
`ao commit`.*

*A move is decided and made on one reading of the board. ao's writers hold the board's lock,
`.ao/board.md.lock`, from their reading to their write, so two of them take turns - the
watchdog takes it only to write, since its decision can wait on keyflip - and each makes its
moves on the text it judged, never on a fresh reading: an item judged READY on one reading and
moved on the next would be started on the record although the implementer had marked it
`waiting:` in between, as the review of this slice found. The implementer's hand edits take no
lock, so ao compares the board with what it read before it replaces it - once before anything
is recorded, and again right before the new board is renamed into place - and a board that
changed meanwhile moves nothing: the command says the board changed while ao read it and to
run it again, and the watchdog decides again on its next cycle. What stays open is the instant
between that last comparison and the rename, a third of a millisecond on a laptop's disk: an
edit landing in it is replaced and lost. No plain file closes it - an editor takes no lock,
and a rename replaces whatever the name holds at that moment - and the board is the file the
implementer edits by hand.*

*The line is written before the board, so a ledger that cannot take it leaves the board as it
was. A board that cannot be written after it - a full disk, a permission, a rename refused, an
edit that landed while ao wrote - gets a second line voiding the first and saying why, so the
ledger never holds a move the board did not make, and a retry is recorded once
([ledger.md](ledger.md#slices)). A process stopped between the two writes leaves no such line,
and `ao slice status` says the board never took ao's last recorded move of the slice, or a hand
moved it back.*

Two states people forget to model: **blocked** (waiting on a human decision or supplied
input — see escalation in [`protocol.md`](protocol.md)) and **abandoned**, which is a
legitimate outcome and should be recorded rather than quietly dropped. *In ao since slice
SLICE-LIFECYCLE: `ao slice block "…"` parks the running slice under `blocked` with the reason
as its `needs:`, and `ao slice abandon --why "…"` moves a slice to `rejected` with the reason
as its `abandoned:` note. `rejected` is the section the board already keeps for work that
will not land: its review artefacts may be archived and its worktree retired, and the row
stays, with its reason, where a restarted session reads it.*

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

## Outcomes, measured

A process change is judged against the slices before and after it, not against anecdote:
on 2026-09-07 the round budget, the size guideline and the review pipeline were all
re-decided in one day on the only figures available, an agent's own estimate of where its
hours went. `ao stats` reads what ao already recorded - nothing is typed by an agent - for
every slice that landed with a grant: the verdicts in the order they came (a
re-specification keeps that history), rounds, time from ready to landed, product lines by
kind, and whether a defect was found later in what it landed, by a retrospective review
that asked for changes or a board item marked `fixes:` it. `--all` spans every registered
project; `--since` and `--until` cut the window a change is compared across, each a date such
as `2026-09-10` or a span back from now such as `7d`.

A grant counts only when a commit reachable from HEAD carries its `Ao-Grant` trailer;
ready-to-landed time ends at that commit's committer timestamp. Abandoned grants are
left out. An unreadable ledger is named with its project under `--all`, and proposal
evidence names the failure too. The median averages the two middle values for an even
sample; p90 uses nearest rank, so ten observations take the ninth value.

## Commands

A slice is a row on `.ao/board.md` and moves between its sections as it is worked:

```bash
ao board                         # every item by state, what may start now first
ao board ready                   # exactly the queued items whose needs: have landed
ao stats --slices                # one line per landed slice: rounds, time, size, later defects
```

A dependency is satisfied only by a `done` or `verified` id with no graph problem. An id
listed both queued and done cannot make a dependent READY: resolve the duplicate first
(TRIAGE-3-LOW).

*In ao since slice SLICE-LIFECYCLE: commands that start, block and abandon a slice, each move
a line in the slice ledger, and two that read where slices stand.*

```bash
ao slice start claim-admission --boundary-file docs/slices/claim-admission.md
ao slice status                  # state and since when, its moves, rounds used, its newest review
ao slice block "waiting on Authenticode certificate" --waiting human
ao slice abandon --why "superseded by the metadata authority slice"
ao slices --open                 # every open slice, one line each
```

| command | moves | whose |
|---|---|---|
| `ao slice start <id>` | queued → running, blocked → running | the implementer's for a READY item, or a slice it blocked itself; the architect's or a person's for any queued or blocked item |
| `ao slice block "…" [--slice <id>] [--waiting <who>]` | running → blocked | anyone's |
| `ao slice abandon --why "…" [--slice <id>]` | any open section → rejected | the architect's or a person's |
| `ao slice status [<id>]` · `ao slices [--open]` | none: they read | anyone's |

The implementer settles alone which READY item comes next, so it starts one; anything else
chooses scope. A turn ao started for the implementer (`AO_ROLE`) is refused an item that is
not READY - `ao slice start` names why, as `ao lane start` does - a block someone else made,
a boundary file and an abandon, and nothing is written. `ao doctor` names an implementer's
grant that admits `ao slice abandon` (`actor-grant`), as it names one that admits
`ao decide --scope`; the grants ao ships admit `ao slice start`, `ao slice block` and
`ao slice status` to the implementer, and every `ao slice` command to the architect.

A start writes `since:`, and one the architect or a person makes of an item that is not
READY records why it was not. A block's reason becomes the row's `needs:`; a `needs:` the
slice carried is kept in `held-needs:` and given back when it resumes, as the watchdog's
quota park keeps it, and a slice the watchdog parked is given back by the watchdog when the
limit resets, or by the architect or a person. `--boundary-file` points a slice at a file
committed in the repository as it stands, `boundary: <path>@<commit>`, in place of its
acceptance sentence, which the ledger keeps. `--slice` names the slice when more than one is
running, or another than the one that is. Abandoning a running or blocked slice sends no
mail: the command reminds whoever drops it to tell the implementer with `ao note`.

`ao slice status` says since when the slice is in its section and who moved it there, every
move ao recorded of it, the rounds it has used against the budget while it is open, and its
newest review that reached a verdict, with the four counts and, when it asked for changes,
the findings its file lists - read only while the file holds the bytes the review ledger
recorded. A slice reviewed only before the review ledger has its newest review read from its
file as it stands, as its rounds are counted, and the status says that no row records it.
`ao slices` lists every slice by state, with since when; `--open` leaves out done and
rejected. A move ao recorded whose board write failed is in the status's moves, marked as one
that did not happen, and nowhere else.

## A split is a pure move, proven mechanically

`lib.py` and `cli.py` hold most of the code, so almost every slice touches one of them and two
lanes meet in the same file (#44). They are split into **parts** in `src/ao/parts/`: a part is a
contiguous run of a module's own definitions, moved out byte for byte and loaded where it stood
by `_part("name", globals())`. It runs in the module's namespace, so every name stays where
callers, tests and monkeypatches look for it — a split moves text and never behaviour, and the
suite passes without edits.

One namespace written in a dozen files has one of each name: where two of a module's files define
the same name, the one loaded later replaces the other for every caller of both, and each file
reads right alone. `tests/test_parts_one_name.py` reads each module and its parts as the loader
runs them and fails when two files bind a name to two things, or one file writes a function or a
class twice (PARTS-ONE-NAME).

A split slice is marked `move-only` on its board line. `ao commit-ok` then refuses the candidate
unless `ao split-check` finds a pure move:

- every definition that leaves a module arrives, byte for byte, in the very file a top-level
  `_part` call of that module runs (`parts/<name>.py` beside it): a rename, or a part another
  module loads, runs in another namespace and is no move;
- no definition is edited in place, lost or added, no other top-level statement changes in any
  file, and no module loses a `_part` call it made;
- each module, read with its parts where they load, is the text it was, line for line but its blank
  lines: a docstring, a comment added, dropped or carried to another place, a docstring a new part
  brings, is no move. A move-only slice lands without a review, and such text steers whoever reads
  the code next (SPLIT-DOCSTRING);
- put in place where the module loads it, each part the candidate adds gives back the module's old
  statements in their old order, loads of existing parts included: a part loaded where its
  definitions did not run, two statements swapped, or a definition moved across a statement runs
  differently and is no move;
- every part the candidate adds is loaded by a top-level `_part` call, which a mention of one in a
  docstring is not; a comment after the call does not hide it;
- the names a module's loads call through - `_part`, or `A` in `A._part` - are bound to ao's loader
  alone, in the module's file and in every part it loads, whether the candidate touched it or not;
  and none of those files reaches a namespace or an object's internals by what names nothing it
  binds. Reflection there is read as a closed world: `globals()`, `vars()`, `locals()`, `exec`,
  `eval`, `compile` and `__import__`, on whatever object they are reached from; `getattr` and its
  kin by a name computed at run time, by one of those names, or handed on; every dunder but the
  few that reach nothing (`__name__`, `__file__`, `__init__` and the like); a frame, `modules`,
  `import_module`, `CodeType` or `FunctionType`; and the modules that import by a name, unpickle
  or evaluate a string (`builtins`, `importlib`, `inspect`, `pickle`, `typing`, `operator` and
  others). At run time a module whose parts ao loads refuses to rebind a name that holds ao's lib
  or its loader, however the module is reached.

The proof reads what a candidate changes and what runs where its parts load; it does not judge what
code does. Code that already landed and runs code of its own choosing by other means - writes a
part's file before it loads, starts a process - was reviewed when it landed, as
[the threat model](threat-model.md) has it.

The grant records that proof: the slice that declared the move and how many definitions moved,
only when its authorising waiver or review names that slice and no other move-only slice is
running. Otherwise it records no proof and says why; another running slice's declaration
cannot stand for the candidate's own.
A split that lands under a review waiver with that record is not reviewed afterwards: `ao catchup`
runs the same proof on the commit that landed, from the parent it landed on, only when the
recorded proof names the waiver's slice, and closes the waiver on it with no reviewer; the
closing record carries the range, the grant and what moved where. When what landed is not a
pure move the waiver stays open, and the reason is printed. The proof reads Python definitions
and nothing else, so a candidate that also changes another file
is granted as before but records no proof, and its waiver is reviewed like any other - as is the
waiver of every split granted before grants recorded the proof. A person who knows such a split
only moved code says so with `ao catchup --move-only <slices> --by <name>`: the same proof runs on
what each named slice landed, and its waiver closes only where the proof holds, with the statement
on the record beside it. A split that also changed a document does not close that way, since the
proof does not read the document; its waiver waits for a review.

What a reviewer reads of a split is its seams — which names a part uses from the rest of its
module, and which the rest uses from it — not thousands of moved lines.
