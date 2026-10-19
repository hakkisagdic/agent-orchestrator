# The ledger

Delivery-by-deletion is right for messages and destructive for decisions. A mailbox entry
disappears the moment it is understood — which is exactly what you want for "do this next"
and exactly what you do not want for "and here is why we chose it".

In the run this project came from, roughly fifteen architectural decisions were issued as
mail: brand by registry membership rather than `instanceof`; authority must be
non-extractable; an unknown outcome is a third state, never a failure; guard timers with an
epoch counter. Every one of them was deleted on delivery. The rules survived in the design
document. **The reasoning did not**, and it had to be re-derived more than once.

The ledger is the fix: an append-only record that outlives the messages.

```
.ao/ledger/
├── decisions.jsonl      # why we chose things
├── verifications.jsonl  # what was actually measured, and by whom (hash-chained)
├── authority.jsonl      # hash-chained commit grants and refusals
├── reviews.jsonl        # hash-chained: each review artefact, its bytes and verdict
├── waivers.jsonl        # hash-chained: a person's waiver of a gate for a slice, and its closing
├── opt-ins.jsonl        # hash-chained: a person's opt-in into a weaker review tier, with login and terminal;
│                        #   only rows this machine's checkpoint store records ao appending count
├── merges.jsonl         # hash-chained: each merge-check run and the merge it vouches for
└── slices.jsonl         # hash-chained: each move of a slice between the board's sections
```

## Decisions

```jsonc
{"id":"D-014","at":"2026-09-03T20:30:00Z","slice":"claim-admission",
 "question":"A wrapper around the authentic factory can launder a branded lease into a structural clone.",
 "decision":"Authority must be non-extractable: return an opaque handle, keep the real object module-private, validate at the point of use.",
 "rationale":"Branding only protects up to the first wrapper. This is the third variant of the same attack family, so close the class rather than the instance.",
 "rejected":[{"option":"Brand the returned lease harder","why":"Extractable objects can always be re-wrapped."}],
 "landed_in":["design.md#donor-authority"],
 "supersedes":null,"superseded_by":null}
```

Two fields carry most of the value and are the two people skip:

- **`rejected`** — six weeks later, the obvious question is "why not just do X?", and X is
  usually something that was already considered and killed for a reason. Write it down or
  relitigate it.
- **`landed_in`** — the spec carries the *rule*; the ledger carries the *why*. Linking them
  keeps the spec short without making it mysterious.

`supersedes` / `superseded_by` make revision explicit. Never edit a decision in place: a
changed mind is a new entry pointing at the old one.

## Verifications

Commit authority means nothing if it rests on "I saw the tests pass" in a chat log.

```jsonc
{"id":"V-041","at":"…","slice":"claim-admission","by":"self",
 "gates":[{"name":"typecheck","exit":0},
          {"name":"focused","result":"62/62"},
          {"name":"full","result":"330/330"},
          {"name":"diff-check","exit":0},
          {"name":"artifact-sweep","result":"clean"}],
 "reviews":["2026-09-03-211056"],
 "granted":"commit","commit":"…"}
```

The rule that makes this worth writing: **commit authority is granted against a
verification id, not against a report.** Afterwards, "why was this allowed to land" has a
row, with the numbers, and the name of whoever measured them.

*In ao since slice LEADS-GATE: an id `ao verify` writes is `V-` and the millisecond it was minted,
above the newest id in the ledger. It was the second, and two runs in one second, or a run after the
clock stepped back, recorded two verifications under one id, which a grant, a commit's
`Ao-Verification` trailer and a report each name.*

## Authority chain

`.ao/ledger/authority.jsonl` is stricter than the other ledgers because its newest grant
is executable authority. Every committed JSON object carries `previous`: `null` on the
genesis row and, after that, the `sha256:` digest of the complete preceding object. The
digest is computed from canonical JSON (sorted keys, fixed separators, ASCII escapes)
under the `ao-authority-row-v1` domain. Grants and refusals share the same chain.

The append operation repairs only an uncommitted partial tail, validates the complete
existing chain, selects the predecessor and writes the successor while holding one
cross-platform ledger lock, then fsyncs before returning. `ao commit-check` validates the
whole chain before looking for a grant. Missing links, semantic edits, middle deletion,
reordering and wrong-predecessor appends therefore fail closed. A non-empty legacy ledger
without `previous` fields also fails closed; AO never rewrites historical authority in
place. Preserve/archive such a ledger explicitly and obtain fresh verification, review
and authority in a new ledger rather than silently treating old rows as chained.

*In ao since slice WINDOWS-LOCK-WRITE: a lock file is made with one byte, written by whichever
process finds it empty. Windows locks are mandatory for writes, and four processes appending to
a new ledger at once failed there: one wrote the byte after another had locked it. The refused
write is let go, since the file is no longer empty, and nothing is left to write when the lock's
handle closes.*

Every prefix of a valid chain is itself valid, so the chain alone cannot see a removed
tail. Each row therefore also carries its `ordinal`, and every append records the
ledger's committed length and the digest of its last row outside the repository, in
`~/.ao/ledger-checkpoints.json`. A ledger shorter than that record — newest rows cut, or
the whole file gone — is a broken commitment, not a shorter chain: reading refuses it,
nothing is appended onto it, and `ao commit-check` refuses. A fresh machine has no record
to compare with. Retiring a ledger on purpose means archiving the file and removing its
entry from that record by hand; sealing old rows instead is #50.

*In ao since slice LEDGER-HISTORY: the record is replaced whole as ao's other whole files are - under a
name no file had, synced, renamed, and its folder synced after the rename. It was renamed into place
with no sync of its folder, so a power cut could take back the record of an append that had returned,
and a row then cut from the ledger matched the length recorded before it.*

This is tamper-evidence, not authentication. Same-user write access can recompute a suffix,
append a correctly linked forged row, or cut a valid tail and rewrite the length record to
match, because AO has no external trusted head or signing key. See the exact security boundary in
[`safety.md`](safety.md).

## Plan baselines

`.ao/ledger/plans.jsonl` records each item's admitted plan digest. New rows are chained
under `ao-plan-row-v1`, with the committed length recorded outside the tree as for authority.
A complete row without a nonempty string `item` and `digest` is corruption: verification
and commit authority refuse with "plan baselines cannot be read". Edited chained digests
and deleted committed rows are refused too.

Existing unchained rows are accepted only as a legacy prefix, as for waivers; the first
new append links to its last row. That prefix was not authenticated retroactively.
Re-admitting an item appends `event: readmitted` and `replaces` naming its previous digest.
A duplicate item without that record cannot silently replace the baseline. Appends select
the previous baseline under the ledger lock, so concurrent admissions remain ordered.

## Slices

A slice's state is the section of `.ao/board.md` its row sits in, and that is what a
restarted session reads to know what it was doing without asking anybody. *In ao since slice
SLICE-LIFECYCLE: `.ao/ledger/slices.jsonl` says how it got there, one line per move ao makes
between the board's sections - see [`slices.md`](slices.md#states) for which moves those are:*

```jsonc
{"previous":"sha256:…","ordinal":7,"slice":"claim-admission","from":"running","to":"blocked",
 "by":"implementer","at":1791830400,"why":"waiting on Authenticode certificate",
 "title":"claim admission","notes":{"needs":"waiting on Authenticode certificate","waiting":"human"}}
```

`from` is null for an item that enters the board, and its line carries the notes it entered
with; every line carries the slice's title, which says what it was once its row is gone. `by`
is the role of a turn ao started, `person` for a caller it did not start, or `watchdog`.
`notes` are the notes the move set (null: removed) and `replaced` what they held before it, so
a boundary or a reason a move overwrote stays on the record. The line is chained under the
`ao-slice-row-v1` domain and its secrets are masked as in every ledger. A move within a
section changes notes and is no transition; a move by hand has no line, and where ao recorded
an earlier move of the slice, `ao slice status` and `ao slices` name the disagreement.

A move is decided on one reading of the board and made on that reading, under the board's lock
and compared with the board as it stands before the board is replaced
([slices.md](slices.md#states)): a board that changed while ao decided moves nothing and
records nothing. The line is written before the board, and a chained ledger is never cut back,
so a board that cannot be written after it - a full disk, a permission, a rename refused, a
hand edit that landed while ao wrote - gets a line that voids it:

```jsonc
{"previous":"sha256:…","ordinal":8,"slice":"claim-admission","from":"running","to":"blocked",
 "by":"implementer","at":1791830401,"voids":7,"title":"claim admission",
 "why":"the board could not be written: PermissionError: Permission denied"}
```

`voids` is the ordinal of the line it voids, and `why` says what stopped the write, with no
path in it. Whatever reads the ledger - since when a slice is where it is, who blocked it,
`ao slices` - leaves the voided move and the line voiding it out of the moves the slice made, and
`ao slice status` shows it in the slice's history, marked as a move that did not happen. A
retry is recorded once, as a move of its own. A process stopped between the line and the board
leaves no line voiding it: `ao slice status` then says the board never took ao's last recorded
move of the slice, or a hand moved it back.

## Commands

```bash
ao decide "…" --why "…" --scope claim-admission   # append a decision, mailed to the implementer
ao decide --list                                   # the decisions recorded, newest last
ao recall authority                                # decided, answered or found, in every project
```

Not built yet: one entry shown with its links, and `INDEX.md` rendered from the ledger;
nothing writes `INDEX.md` today.

<!-- not built: ao has no ledger command, and nothing renders INDEX.md -->
```bash
ao ledger show D-014                               # one entry with its links
ao ledger render                                   # regenerate INDEX.md
```

`ao verify` writes its own record; you never hand-author a verification.

## The stores are checked against each other

The board, the ledgers, the review artefacts and git refs are four stores that must agree.
`ao doctor --consistency` cross-checks them and names every disagreement with both sides: a
grant resting on a review that is neither on disk nor present at its archive pointer, a grant naming a
verification or a waiver its ledger does not hold, a review file that is not the bytes its row
recorded, a review row with no file anywhere, a board item citing a commit that is not in the
repository, a committed-length checkpoint for a checkout that is gone, an architect lock whose
holder is dead, and a review ao archived with nothing recording where.

Archived copies are opened and checked against the review row's digest: a pointer alone
vouches for no bytes. `--repair` fixes only the last three kinds - mechanical state - after
reading the chained `.ao/ledger/repairs.jsonl` and appending an `event: intent` row before each
action. An intent records an attempted repair, not proof that an interrupted action finished.
Unreadable repair history prevents all repairs. Lock repair compares the scanned holder
again under the same sidecar lock that acquisition and release use; a retaken or live lock
is left alone (TRIAGE-1-MEDIUM). Anything that says what
was authorised, reviewed or verified is reported and never changed; no repair rewrites an
authority row.

## Every store is bounded

Each store has a retention by kind, enforced as it is written rather than on request, because a
cleanup that waits to be asked is never run. Observation - notices, progress samples, cycle
records, and the nudge, watchdog, refill and wake logs (the wake's is `escalate-<key>.log`) - keeps the newest
`retention.observation_kb` and drops the oldest records at a line boundary; the notices ledger
does this on each write and the watchdog holds the rest every cycle. A log a turn or a wake the
watchdog started is still writing is left whole until that process ends, and is bounded in a later
cycle. `ao doctor` names a store that is over its bound anyway. What a trim takes from the notices still counts in an alarm's
window: each notice is folded first into `.ao/ledger/notice-times.json`, when each key was last
recorded and sent, which keeps a week and at most 500 keys ([alarms](alarms.md)).

Evidence is never trimmed. `ao doctor` names authority and verification ledgers whose live rows
exceed `retention.evidence_keep`, with their row count and the command to seal them. Archived rows
do not count against that bound. Plans are kept whole and never sealed.
The authority and verification ledgers past their bound are **sealed**: `ao prune --evidence
--yes` moves all but their newest `retention.evidence_keep` rows whole into `.ao/ledger/sealed/`,
and a seal records how many rows it retired and the digest of the last of them. That digest is
the genesis the first kept row links to, and the same seal is recorded outside the repository
with the committed length, so a seal written by hand, or a sealed prefix removed, is refused
like any broken chain. `ao commit-check` reads the same newest rows from a sealed ledger as from
an unsealed one. A verification a grant names stays readable by its id from the sealed archive,
and review artefacts are kept by reference ([telemetry](telemetry.md)).

*In ao since slice LEDGER-HISTORY: a seal records its intent - the rows it retires, the digest of the
last of them, its archives, and the length and newest digest the ledger had - in
`~/.ao/ledger-checkpoints.json`, under that record's lock, before it rewrites the ledger, and a seal cut
short is finished or undone from that record alone. The intent was a `.pending` file beside the ledger,
which the implementer can write: a ledger cut to its newest rows, beside one naming the cut rows as
sealed, was accepted and the record outside the repository rewritten to match, and so was a chain of
the writer's own with any genesis, whose newest grant then read as the newest decision. A `.pending`
there now names a seal this machine did not begin: the ledger is not read while it stands, and it stays
where it is for a person to read and move away. The archives are held to their seal - one chain ending
at its digest, with exactly the rows it retired - and every reader of what was ever granted or verified
reads them and the live rows as one chain: the candidates a waiver authorised, the waivers `ao catchup`
reviews, the trees and the first grant the landed-tree check measures from ([safety](safety.md#3-commit-authority-is-separated)),
the reviews grants rest on, `ao backup`, `ao restore` and `ao doctor --consistency`. Read from the live
rows alone, a waiver whose grant a seal retired stood in for a review of other bytes, and catch-up closed
it unreviewed as unused. The newest decision is still read from the live rows. Measured here with 10,000
authority rows sealed to the 500 kept live, the whole history reads in about 50 ms, and in 114 ms on a
machine that did not seal it, where the archives' chain is walked link by link; the live rows read in
7 ms.*

## Rules

- **Append-only.** Corrections are new entries. A ledger you can rewrite is a ledger you
  cannot trust.
- **Machine-readable first**, rendered second. A rendered view is never the source.
- **Committed to the repository.** Unlike `agent-mail/`, the ledger is history and belongs
  in version control.
- **No secrets, ever** — same rule as everywhere else.
