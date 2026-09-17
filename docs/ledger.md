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
└── merges.jsonl         # hash-chained: each merge-check run and the merge it vouches for
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

Every prefix of a valid chain is itself valid, so the chain alone cannot see a removed
tail. Each row therefore also carries its `ordinal`, and every append records the
ledger's committed length and the digest of its last row outside the repository, in
`~/.ao/ledger-checkpoints.json`. A ledger shorter than that record — newest rows cut, or
the whole file gone — is a broken commitment, not a shorter chain: reading refuses it,
nothing is appended onto it, and `ao commit-check` refuses. A fresh machine has no record
to compare with. Retiring a ledger on purpose means archiving the file and removing its
entry from that record by hand; sealing old rows instead is #50.

This is tamper-evidence, not authentication. Same-user write access can recompute a suffix,
append a correctly linked forged row, or cut a valid tail and rewrite the length record to
match, because AO has no external trusted head or signing key. See the exact security boundary in
[`safety.md`](safety.md).

## Slices

Not built yet: one line per lifecycle transition — see [`slices.md`](slices.md). A slice's
state is the section of `.ao/board.md` its row sits in, and that is what a restarted session
reads to know what it was doing without asking anybody.

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
grant resting on a review that is neither on disk nor recorded as archived, a grant naming a
verification or a waiver its ledger does not hold, a review file that is not the bytes its row
recorded, a review row with no file anywhere, a board item citing a commit that is not in the
repository, a committed-length checkpoint for a checkout that is gone, an architect lock whose
holder is dead, and a review ao archived with nothing recording where.

`--repair` fixes only the last three kinds - mechanical state - and appends what it did to
`.ao/ledger/repairs.jsonl`, chained, so a repair is itself evidence. Anything that says what
was authorised, reviewed or verified is reported and never changed; no repair rewrites an
authority row.

## Every store is bounded

Each store has a retention by kind, enforced as it is written rather than on request, because a
cleanup that waits to be asked is never run. Observation - notices, progress samples, cycle
records, and the nudge, watchdog, refill and wake logs - keeps the newest
`retention.observation_kb` and drops the oldest records at a line boundary; the notices ledger
does this on each write and the watchdog holds the rest every cycle. `ao doctor` names a store
that is over its bound anyway.

Evidence is never trimmed. A chained ledger past its bound is **sealed**: `ao prune --evidence
--yes` moves all but its newest `retention.evidence_keep` rows whole into `.ao/ledger/sealed/`,
and a seal records how many rows it retired and the digest of the last of them. That digest is
the genesis the first kept row links to, and the same seal is recorded outside the repository
with the committed length, so a seal written by hand, or a sealed prefix removed, is refused
like any broken chain. `ao commit-check` reads the same newest rows from a sealed ledger as from
an unsealed one. A verification a grant names stays readable by its id from the sealed archive,
and review artefacts are kept by reference ([telemetry](telemetry.md)).

## Rules

- **Append-only.** Corrections are new entries. A ledger you can rewrite is a ledger you
  cannot trust.
- **Machine-readable first**, rendered second. A rendered view is never the source.
- **Committed to the repository.** Unlike `agent-mail/`, the ledger is history and belongs
  in version control.
- **No secrets, ever** — same rule as everywhere else.
