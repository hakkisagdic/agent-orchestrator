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
├── verifications.jsonl  # what was actually measured, and by whom
├── slices.jsonl         # slice lifecycle transitions
└── INDEX.md             # rendered, human-readable, regenerated on write
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

## Slices

One line per lifecycle transition — see [`slices.md`](slices.md). This is what lets a
restarted session know what it was doing without asking anybody.

## Commands

```bash
ao decide "…" --slice claim-admission --rejected "…"   # append a decision
ao decisions [--slice X] [--grep authority]            # search
ao ledger show D-014                                   # one entry with its links
ao ledger render                                       # regenerate INDEX.md
```

`ao verify` writes its own record; you never hand-author a verification.

## Rules

- **Append-only.** Corrections are new entries. A ledger you can rewrite is a ledger you
  cannot trust.
- **Machine-readable first**, rendered second. `INDEX.md` is a view, never the source.
- **Committed to the repository.** Unlike `agent-mail/`, the ledger is history and belongs
  in version control.
- **No secrets, ever** — same rule as everywhere else.
