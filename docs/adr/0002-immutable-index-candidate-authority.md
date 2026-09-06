# ADR 0002: Immutable index candidates bind commit authority

**Status:** Accepted

**Date:** 2026-07-24

## Context

Commit authority must describe the exact bytes proposed for a commit. The worktree,
a dirty flag, or a live diff can change between verification, review, grant and the
commit hook. HEAD alone identifies the base, not the staged result, and retrospective
review cannot protect a commit that already landed.

## Decision

Turn Git's active index into an immutable candidate with `git write-tree`, honoring
`GIT_INDEX_FILE`. Bind candidate format, HEAD, the resulting tree object and raw
no-renames status through a length-framed SHA-256 digest. Persist the tree, status
digest, sorted changed paths and count with that candidate.

Render review and verification diffs from the persisted HEAD/tree object pair, not
from mutable worktree bytes. Scope paths are normalized repository-relative literals;
path traversal is rejected, and Git receives literal pathspecs. Refuse a scoped
candidate with staged paths outside the scope. Also refuse authority while unstaged
or untracked product changes, staged coordination files, or other worktree drift make
the candidate non-isolated.

Bind every authority stage to the same candidate:

- verification measures before and after gates and records success only when the
  isolated index is unchanged;
- prospective review records the candidate, literal scope and immutable diff digest,
  then rejects index mutation during review;
- grant revalidates matching verification and independent review evidence before
  durably recording the candidate and scope; and
- `ao commit-check`, including the optional pre-commit hook boundary, revalidates the
  live active index against that grant immediately before Git proceeds.

## Consequences

The authorization unit is the staged candidate rather than a repository snapshot.
Temporary Git indexes and path-limited commits can be measured correctly. Verification,
review and grant evidence become comparable without retaining a mutable diff. Measuring
a candidate may leave an unreachable tree object for normal Git garbage collection,
but does not mutate the index or worktree.

## Rejected alternatives

- **Worktree snapshots or a dirty bit:** mix staged and unstaged state and do not name
  the proposed commit bytes.
- **HEAD-only binding:** names only the base commit, so multiple staged results collide.
- **A live mutable diff:** can change after evidence is produced and makes later review
  reconstruction unreliable.
- **Retrospective authorization:** can document landed work but cannot authorize it
  before the commit boundary.

## Limitations

- This authority model requires a valid Git repository and index; it is not a generic
  filesystem transaction mechanism.
- The pre-commit enforcement boundary is optional. AO detects index mutation whenever
  it revalidates, but cannot prevent a final mutation after the last check when the hook
  is not installed or another commit path bypasses it.
- Candidate isolation detects worktree drift at measurement boundaries; it does not
  freeze the filesystem between those boundaries.
- A grant authorizes only the exact commit candidate. It never authorizes `push`, PR
  creation, publication or any other repository operation.

## References/Supersession

- Implementation: candidate and evidence logic in [`src/ao/lib.py`](../../src/ao/lib.py)
  and command boundaries in [`src/ao/cli.py`](../../src/ao/cli.py)
- Evidence: [`tests/test_candidate_authority.py`](../../tests/test_candidate_authority.py),
  [`tests/test_commit_authority.py`](../../tests/test_commit_authority.py), and
  [`tests/test_review_scope.py`](../../tests/test_review_scope.py)
- Background: [safety model](../safety.md) and [features](../features.md)
- Landed in commit `56ea557` (`feat: bind authority to staged candidates`)
- Supersedes: none
