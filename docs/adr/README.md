# Architecture decision records

Architecture decision records (ADRs) preserve repository-level choices and their
trade-offs beside the implementation. They describe architecture that applies to
agent-orchestrator across runs and projects. Runtime and project-specific decisions
remain append-only evidence in [`.ao/ledger/decisions.jsonl`](../ledger.md); that
ledger does not replace ADRs, and ADRs do not replace the ledger.

## Index

| ADR | Status | Date | Decision |
|---|---|---|---|
| [0001](0001-durable-jsonl-storage.md) | Accepted | 2026-07-24 | Durable JSONL storage for authority evidence |
| [0002](0002-immutable-index-candidate-authority.md) | Accepted | 2026-07-24 | Immutable index candidates bind commit authority |
| [0003](0003-declarative-capability-routing.md) | Accepted | 2026-07-24 | Declarative capability routing for independent review |

## Convention

ADRs use the next four-digit sequential number and a short kebab-case title in the
filename. Each record has a numbered title, `Status`, `Date`, and these sections:
`Context`, `Decision`, `Consequences`, `Rejected alternatives`, `Limitations`, and
`References/Supersession`.

Accepted ADRs are historical records. A changed decision gets a new sequential ADR;
the old and new records link each other through `References/Supersession` rather
than rewriting the original rationale.
