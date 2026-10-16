# A review answered three times and collected none — the 08:49 stop of 2026-10-16

A measurement note, not an audit of ao. It records what was seen of one landing review
that answered nothing and of the spy that was kept under the reading path it failed on,
so that whoever finds the UNAVAILABLE artifact it left — `semantic-review/2026-10-16-084920-bbe7492.md`,
which says of itself "No review took place. This file is not a round." — reads what was
measured, and so that no fix is invented for a failure the counter under it has not shown.

Everything below is a reading of the working copy on the day of the stop and the days
right after it. Counts age.

## The stop

At 08:49 on 2026-10-16 a landing chain ran `ao review` on its staged candidate
(WARP-ADAPTER, which a later run that day landed). Three configured reviewer routes ran in
turn and each exited 0 — after 10m41s, 6m17s and 1m17s — and then the step that follows an
exit, reading what a reviewer answered and recording it, failed for all three at once:
`reviewer invocation failed (ModuleNotFoundError)`. The verdict written was REVIEWER
UNAVAILABLE, the chain stopped (`STOP WARP-ADAPTER: not approved`), and the artifact it
left where a round would have gone is the one this note is about. It stays where it was
written.

## What the readings show

The exit code is not the failure. Every route answered 0, and the failure is in the
collection that follows the exit, not in any one route's behavior: it answered the same
way for all three.

The code that reads a reviewer's answer and records it is byte-identical to the code the
reviews around the stop ran: between the commit the stopped chain ran (bbe7492) and the
commit this note is written on (55cdb07) none of the modules a review reads and records an
answer with differs by a byte — the review command with its stream reader, its answer
record and the line that named this failure (`src/ao/parts/cli_review.py`), the ACP binding
(`src/ao/acp.py`) and the process helpers (`src/ao/procs.py`) — and nothing touched the
first of them after 03:19 that morning or the other two for days before.

And the path collected answers the whole day around the stop: `semantic-review/` holds
sixteen records for 2026-10-16 — thirteen APPROVED, two NEEDS_CHANGES, and this stop — and
the other fifteen, nine before the stop and six after it, each ended in a recorded verdict,
read from its reviewer's answer. Whatever failed the one run left no mark a diff can show
and did not repeat itself that day.

## The spy

To watch the path without changing it, a small `sitecustomize` module was kept on
`PYTHONPATH` wherever a review was run. Python imports it before anything else, and it
wraps `builtins.__import__` so that every failed import is appended to a log file with its
traceback and then re-raised: an import that fails under the spy fails the same way it
fails without it, and the log says which module, where. Since 2026-10-16 it also writes
one line per process it is armed into — the time, the process id, the command line and the
working directory — so a run said to be under the spy is proven from the log rather than
claimed.

The log holds readings that are not the failure too, each attributed by its traceback:
platform probes (`nt`, `_winapi`), an encodings probe of a codec name that does not exist
made by the suite's UTF-8 test, and `setproctitle` probes from suite workers. An entry
that matters has the shape of the failure: a module of `ao` named as the failed import,
with `ao` frames in its traceback. None has been seen.

## The counter

The stop's acceptance is a counter, not a fix. The failure is closed as not seen only
through ten armed review runs of this candidate, each leaving its arm line in the spy's
log; a run that fails with the shape above reopens the diagnosis and owes a fix that fails
red first. A failure the counter cannot show owes no fix, and the UNAVAILABLE artifact
stays where it was written. The per-run lines of the counter are kept with the working
record that accompanies this slice, outside the tree.

## What the counter found

Ten armed review runs of this note were made on the day it was written, in four working
copies of the tree, between 17:46 and 18:02, each with the spy on `PYTHONPATH`. The spy's
log holds their ten arm lines — the time, the process id, `-m review` with the boundary it
was given, and the working directory — and each of the ten runs read its reviewer's answer
and recorded a verdict: nine APPROVED and one NEEDS_CHANGES, none ending REVIEWER
UNAVAILABLE. Three of the ten did not have a readable answer at a reviewer's first turn —
the reading said so and asked once more — and each was read at the second asking: the
collection, its asking-again branch included, answered every time.

No entry in the log has the shape of the failure: the failed imports recorded under the
counter are platform probes (`nt`, `_winapi`) alone, none of them naming a module of `ao`.
So the stop is closed as not seen — the fifteen reviews the day collected around it each
ended in a recorded verdict, and the ten armed runs of this note watched the path it
failed on without failing it — and by this note's acceptance it owes no fix. The
UNAVAILABLE artifact stays where it was written; the raw run lines, with the spy's log as
copied at record time, stay with the record that accompanies this slice, outside the tree,
and what could be moved into reach stands below.

## What a copy of the tree can check

A reader who holds only this tree can take the readings above no further than their
words. What could be moved into reach was moved:

- The three files the stop's code ran in carry one sha256 each — the same value at the
  stopped commit (bbe7492), at the commit this note is written on (55cdb07) and in this
  tree — so any copy can hash the three paths and find the bytes these readings were read
  from:

  | path | sha256 |
  |------|--------|
  | `src/ao/parts/cli_review.py` | `6ecb60d4e95acc018023e2cf6f6d1d8d1de7c088ec6a897b49d717010f694fc6` |
  | `src/ao/acp.py` | `41264decaa0a3527eef2b5eaab7fe6b2191d357576a24496c9fbfdbf7b39bf73` |
  | `src/ao/procs.py` | `66c79d130ac84387ece854422f0be696365ae2802f2754ed1455826291f347d8` |

- The line that named the failure, `reviewer invocation failed ({type(exc).__name__})`,
  stands in `src/ao/parts/cli_review.py` at 1603 and 1737.
- The counter's transcript — the ten askings, their working copies, the verdicts read and
  the records left:

  | asked | working copy | read | verdict | record left |
  |-------|--------------|------|---------|-------------|
  | 17:46:09 | the first | 17:53:10 | APPROVED · MEDIUM 1 (asked once more) | 2026-10-16-175310-55cdb07.md |
  | 17:53:10 | the first | 17:56:06 | APPROVED | 2026-10-16-175606-55cdb07.md |
  | 17:56:06 | the first | 18:00:56 | APPROVED (asked once more) | 2026-10-16-180056-55cdb07.md |
  | 17:46:09 | the second | 17:47:30 | NEEDS_CHANGES · HIGH 1 | 2026-10-16-174730-55cdb07.md |
  | 17:47:30 | the second | 17:51:16 | APPROVED (asked once more) | 2026-10-16-175116-55cdb07.md |
  | 17:51:16 | the second | 18:02:07 | APPROVED | 2026-10-16-180207-55cdb07.md |
  | 17:46:09 | the third | 17:47:04 | APPROVED | 2026-10-16-174704-55cdb07.md |
  | 17:47:04 | the third | 17:48:12 | APPROVED | 2026-10-16-174812-55cdb07.md |
  | 17:46:09 | the fourth | 17:48:54 | APPROVED | 2026-10-16-174854-55cdb07.md |
  | 17:48:54 | the fourth | 17:49:47 | APPROVED | 2026-10-16-174947-55cdb07.md |

Nothing carried here is a new measurement: each value or name was already in this note's
sentences, moved from prose a reader must take on its word into a form a reader can check
against the tree. The raw arm lines — times, process ids, machine paths — were not moved;
their placement stands as the sections above say.

What this note says of the code can be checked in its tree: both commits stand in this
tree's history, and `git diff bbe7492..55cdb07` shows nothing under the three paths named
above — and a copy that does not carry that history checks the same three files against
the hashes above.
