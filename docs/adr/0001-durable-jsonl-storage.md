# ADR 0001: Durable JSONL storage for authority evidence

**Status:** Accepted

**Date:** 2026-07-24

## Context

Authority and verification rows are acted on after the storage call returns. Ordinary
logging semantics are insufficient: concurrent processes can interleave records, short
writes can leave fragments, and a killed process can leave a final row in an uncertain
state. The project also keeps its standard-library-only runtime boundary.

## Decision

Use the crash-safe JSONL primitive in [`src/ao/storage.py`](../../src/ao/storage.py)
for the migrated authority and verification ledgers.

- Readers and writers take a cooperative sidecar `<ledger>.lock`: `flock` on POSIX
  and one-byte `msvcrt.locking` on Windows. The lock spans validation, repair, append
  and durability barriers so a cooperating reader cannot observe a row between write
  and file `fsync`.
- Encode one compact UTF-8 JSON object plus LF and append it in place. Loop on
  `os.write` until every byte is written, retry interruptions, and fail when a write
  makes no progress. A successful return requires file `fsync`.
- When a ledger is created, also `fsync` its directory on supported POSIX filesystems.
- Before appending, validate every complete row and repair at most the incomplete
  tail. Complete a valid unterminated JSON object with LF, or truncate one invalid
  partial tail to the preceding newline, then `fsync` the repair.
- Treat malformed complete rows as corruption and fail closed. An incomplete final
  row may be ignored by readers; complete corruption may not.

## Consequences

Authority-critical writes pay for locking and synchronous persistence, but callers
get a clear return boundary and independent AO processes do not interleave rows.
Append-in-place preserves the ledger model and avoids rewriting history. Sidecar lock
files remain beside ledgers, and recovery is intentionally limited to the one failure
shape that an interrupted append can produce.

## Rejected alternatives

- **Unlocked or buffered logging writes:** return before durable persistence and permit
  concurrent record interleaving.
- **Rewrite the full file or replace it through a temporary file for each row:** adds
  whole-ledger I/O and replacement/metadata failure modes to an append-only operation.
- **Add a database or runtime locking dependency:** conflicts with the zero-runtime-
  dependency boundary and adds deployment state for local evidence.

## Limitations

- Only `authority.jsonl` and `verifications.jsonl` have migrated to this primitive;
  this ADR makes no durability claim for every `.ao/ledger/` file.
- A complete-looking row written before the file `fsync` is ambiguous after a crash:
  it may be present, absent or not durably ordered even though the call never returned.
  JSON shape alone cannot certify that pre-`fsync` row.
- Windows has no directory `fsync` in this implementation. POSIX filesystems that
  reject directory `fsync`, unsupported platforms, and network filesystems provide
  only the guarantees their platform actually implements.
- Advisory locks constrain cooperating writers only. They do not stop another program
  from editing a ledger directly.
- Existing [`Popen.kill` durability tests](../../tests/test_storage_durability.py) use
  real child processes and temporary paths. They are deterministic process-crash
  evidence, not physical power-loss, controller-cache or filesystem certification.

## References/Supersession

- Implementation: [`src/ao/storage.py`](../../src/ao/storage.py) and authority/
  verification integration in [`src/ao/lib.py`](../../src/ao/lib.py)
- Evidence: [`tests/test_storage_durability.py`](../../tests/test_storage_durability.py)
- Background: [the ledger](../ledger.md) and [Windows support](../windows.md)
- Landed in commit `6e089cb` (`feat: add crash-safe JSONL ledger storage`)
- Supersedes: none
