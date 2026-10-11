# Evidence

What ao's own ledgers show about its guarantee on ao's own repository, measured rather than stated.
Every number here is a line `tools/evidence.py` printed for main at `0fefd2d`, with the ledgers as they
stood on 2026-10-11 at 01:46 UTC; [the last section](#make-each-number-again) says how to make each one
again. Dates are UTC.

## Every commit since the first grant had one

The authority ledger's first grant, `C-1789484982068606000`, is from 2026-09-15. Since then main's
first-parent line holds 390 commits and no merge, and the tree of every one of them - 390 of 390 - is a
tree a grant bound. That is the check `ao doctor` and the watchdog run, and a commit no grant bound is what
they name.

150 of those commits carry the Ao- trailers `ao commit` writes: every commit from `288165e`, the one that
introduced them on 2026-09-30, on. All 150 match the grant they name - the commit's tree is the tree the
grant bound, and the grant, its verification and its review or waiver are the ledger's - and every grant
they name is in the ledger. Anyone with a clone can check the trailers, which need no ledger:
`ao commit-check --range main --since-first-trailer` checks them by tree alone, and printed
`TRAILERS MATCH  150 commit(s)` at `0fefd2d`.

The authority ledger holds 392 rows: 391 grants and one refusal. A grant rests on the review it was
granted on (79), on a review waiver a person opened (279), or on neither (33). Those 33 are all from
2026-09-15 and 16, before a grant row recorded the waiver it stood on; review waivers for that work were
open from 2026-09-15.

## Reviews

The review ledger began on 2026-09-25. It holds 451 reviews up to 2026-10-11, and each review's artefact
holds exactly the bytes the ledger recorded for it.

| reviewed | reviews | APPROVED | NEEDS_CHANGES | INVALID | UNAVAILABLE |
|---|---|---|---|---|---|
| a staged candidate, before it could land | 113 | 80 | 22 | 3 | 8 |
| a landed range, after a waiver (catch-up) | 338 | 197 | 104 | 33 | 4 |
| all | 451 | 277 | 126 | 36 | 12 |

INVALID is an answer ao could not read as a verdict, and UNAVAILABLE a review no reviewer could be
reached for: neither is a verdict, a round or an approval ([protocol](protocol.md#verdicts)). The 403
reviews with a verdict, by the family of the model that wrote them:

| reviewer family | reviews | APPROVED | NEEDS_CHANGES |
|---|---|---|---|
| openai | 150 | 68 | 82 |
| alibaba | 133 | 109 | 24 |
| deepseek | 65 | 61 | 4 |
| google | 44 | 31 | 13 |
| moonshot | 6 | 3 | 3 |
| zhipu | 5 | 5 | 0 |

They counted 8 BLOCKER findings, in 7 reviews, and 157 HIGH, 176 MEDIUM and 218 LOW. A BLOCKER or a
HIGH makes a review NEEDS_CHANGES.

**Independence.** 438 of the 451 reviews stand in the tier "another model family", as ao decided it when
each ran ([review tiers](roles.md#review-tiers)); none stands in the same-family tier and none is a
person's. The other 13 have no tier: the 12 UNAVAILABLE, and one APPROVED review of 2026-09-25 recorded
"not established", since no implementer was configured to compare its reviewer with. No review is by the
anthropic family: 387 of the 390 commits name Claude as their co-author, and the other 3 name no model.

Of the 338 reviews of a landed range, 199 record the family that wrote the range - anthropic for 195, and
for 4 a family a person named as unknown, each reviewed by openai - and none of them was reviewed by that
family. The other 139, all run on 2026-09-30 and 10-01, before catch-up recorded an author's family,
record none. Every commit in their ranges names Claude as its co-author and no other model, and none was
reviewed by anthropic; but who wrote the code before it was committed is in no ledger, so for those 139
the ledgers do not show the reviewer's independence the way they show it for the rest.

## Waivers

A person opened 313 review waivers between 2026-09-15 and 2026-10-06. 309 of them stood under two
decisions the owner recorded in each: from 2026-09-15 the implementer was out of credits until
2026-10-01, and from 2026-10-01 everything landed for a complete release, review deferred rather than
skipped; the other 4 each stood for one slice. All 313 are closed, the last on 2026-10-07, and none is
open:

- 298 on a catch-up review of the range that landed under them, by a model of another family:
  194 APPROVED, and 104 NEEDS_CHANGES, each closed noting that a fix slice is needed;
- 14 on a move proof: the waived slice only moved code, and ao ran the proof again on what landed
  ([safety](safety.md));
- 1 because nothing landed under it before the next waiver was opened.

## What a review by another family caught

Each of these is a review artefact the review ledger names, kept with the ledgers in the checkout ao is
developed in; the public repository carries neither, since `.ao/` and the reviews are a checkout's own.
The first was found in code that had already landed under a waiver; each of the others stopped a staged
candidate, and the slice landed once a later review of it approved, in the commit named.

- `2026-10-01-202856-15cd19e.md`, a catch-up review (openai) of GOVERNANCE-BACKUP: BLOCKER, restoring a
  backup wrote wherever its manifest's paths pointed, outside the backup and the checkout; HIGH, an empty
  manifest restored as a valid state. Fixed that evening by `3d78eaf`, which restores only inside the
  checkout and fails a restore that puts nothing back.
- `2026-09-28-172312-856080b.md`, UPDATE-UNINSTALL-2 (alibaba): BLOCKER, the candidate's changelog line
  said `ao remove` gained the new check of whether an MCP entry is ao's, but only `ao uninstall` called it.
  Landed as `dd5bc4a`, where both do.
- `2026-10-08-171910-3c163f9.md`, SPLIT-CHECK-12 (google): BLOCKER, the move proof let a part reach a
  builtin it refuses by naming it in a string. Landed as `e2597c7`, which reads a part's names as a closed
  world.
- `2026-10-11-010857-3699ec4.md`, HARNESS-CONFIG (google): two BLOCKERs, a committed change to harness
  configuration went unseen in a repository with no grants, and the check trusted git's index flags,
  which an agent can set to hide a change. Landed as `0336074`.
- `2026-10-11-043400-0336074.md`, WINDOWS-NO-CWD-EXEC (google): two HIGH, `ao init` and
  `ao a2a-mcp config` fell back on the bare name ao was started by, which resolves to a file an agent can
  leave in the repository. Landed as `f2428ff`, which drops both fallbacks.

## Make each number again

In the checkout ao is developed in, which holds `.ao/` and the reviews:

```bash
python3 tools/evidence.py              # every number above, one line a claim
python3 tools/evidence.py --json       # the same as one JSON document
```

The tool only reads. It reads the ledgers without taking their locks, so it neither waits on nor writes
beside a checkout ao is working in, and validates each chain with ao's own check; it fails, naming the
ledger, when one does not validate. To measure a checkout in use as it stood at one moment, copy
`.ao/ledger/`, `.ao/config.json` and the reviews folder into a folder of their own and pass
`--state <that folder> --rev <commit>`, as was done here for `0fefd2d`.

What each line rests on:

- **the commits** - main's first-parent line after the head the first grant was built on, each commit's
  tree against the trees the grants in `.ao/ledger/authority.jsonl` bound;
- **the trailers** - every commit from the first that carries Ao- trailers on, held to the grant they name
  by the functions `ao commit-check --range` uses;
- **the reviews** - `.ao/ledger/reviews.jsonl`, each row's artefact read for its bytes and for the evidence
  line ao writes into it: the reviewer's family, the counts and the tier;
- **the waivers** - the opening and closing rows of `.ao/ledger/waivers.jsonl`.

In any clone, where `.ao/` is not:

```bash
python3 tools/evidence.py                              # the trailers, checked by tree alone
ao commit-check --range main --since-first-trailer     # the same check, as CI runs it
```

The ledgers are tamper-evident, not proof of who wrote a row ([safety](safety.md)): a hash chain shows a
row changed, inserted or cut, and cannot show that a process with the same user's access did not write a
consistent one. The numbers move as work lands; the tool measures the checkout it is given.
