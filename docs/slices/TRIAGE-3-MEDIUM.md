# TRIAGE-3-MEDIUM — medium re-review corrections

## Acceptance boundary

Resolve the seven MEDIUM clusters M1–M7 in TRIAGE-3 and the independent pre-review's
follow-ups within those clusters. Deferred reviews retain their submitted identity;
grant audits read every tool value without reading the prompt as a grant; known
wrappers cannot hide a review harness; evidence pruning validates its references;
retirement preserves user work; unreadable credit checks are reported; and a new
decision gets its own escalation window.

Each correction has a regression that fails with that correction removed. Covering
tests run together, including grant-parser importers, lane retirement, retention,
documentation and the shared-part namespace proofs. Product changes stay below the
400-line guideline. No new governance commands, dependencies or ledger formats.

## Why one slice

The architect explicitly requested one corrective slice for M1–M7 and retained that
boundary after the independent pre-review raised the five-path guideline. These
fixes span independent subsystems; there is no single coupled implementation
invariant that would make them technically indivisible. This is an explicit grouping
exception for the bounded triage batch, not a claim that the path guideline is met.
The reviewer can assess each cluster through its regression and individual undo
proof, while acceptance and rollback cover the batch as requested. The follow-ups
repair grant parsing, reference validation and the lane handoff introduced within
this same candidate.

## Paths

- src/ao/allowlist.py — tool grant parsing and bypass detection.
- src/ao/drivers.py — credit lookup failures.
- src/ao/parts/cli_review.py — deferred review identity and route checks.
- src/ao/parts/lib_adapters.py — route and unattended-turn checks.
- src/ao/parts/lib_gates.py — review artefact retention.
- src/ao/parts/lib_lanes.py — lane-specific retirement checks.
- src/ao/parts/lib_mail.py — strict worktree status reads.
- src/ao/parts/lib_slices.py — worktree retirement.
- src/ao/watchdog.py — turn prompts, credit checks and escalation keys.
- tests/test_triage_3_medium.py (new) — original cluster regressions.
- tests/test_triage_3_medium_rereview.py (new) — pre-review regressions.
- tests/test_watchdog_cycle.py — per-decision throttle expectation.
- docs/adapters.md — wrapper refusal.
- docs/alarms.md — escalation windows.
- docs/parallel.md — retirement safety and lane compatibility.
- docs/pipeline.md — submitted review identity.
- docs/safety.md — grant auditing.
- docs/standin.md — carried review identity.
- docs/telemetry.md — credit failures and retention.
- docs/slices/TRIAGE-3-MEDIUM.md (new) — this boundary and grouping decision.

## Out of scope

TRIAGE-3 HIGH and LOW clusters outside the explicitly included M1–M7 work remain
separate. There is no new lane force option and no permission to discard uncommitted
user work. Verification ledgers already accepted legacy prefixes before this
pre-review round; the compatibility test preserves that behaviour.
