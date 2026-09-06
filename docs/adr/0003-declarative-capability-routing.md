# ADR 0003: Declarative capability routing for independent review

**Status:** Accepted

**Date:** 2026-07-24

## Context

Reviewer selection must be vendor-neutral, independently checkable and safe to bind
into commit authority. A hard-coded provider registry ages quickly; tool names alone
do not establish capabilities or separation; and runtime probing cannot retroactively
become authority. Fallback must improve availability without shopping a substantive
rejection or malformed review for an approval.

## Decision

Make capability routing a strict, opt-in, versioned declaration. Absence of the
top-level `capability_matrix` key preserves legacy behavior. Presence activates
version 1 validation and fails closed, including when malformed configuration still
expresses intent to opt in.

Keep the resolver in [`src/ao/matrix.py`](../../src/ao/matrix.py) pure and
vendor-neutral. It validates provider, model, tool and binding declarations without
reading files, probing executables or starting processes. Required capabilities are
layered: providers invoke; implementer models implement and tools prompt/write the
workspace; reviewer models perform semantic review and tools prompt through a valid
argv template.

Canonicalize the complete validated matrix and bind its digest to evidence. Reviewer
commands are shell-free argv lists with constrained `{prompt}` and `{model}` expansion.
Derive independence from declarations: reviewer binding must differ from implementer
binding, and reviewer model family must differ from implementer family. Inline family
claims cannot override the bound model.

Try eligible reviewer bindings in declared order. Runtime unavailability such as a
missing executable, timeout, silence, quota or authentication failure may advance to
the next route. A substantive response with invalid review structure stops as
`INVALID`; it is never sent to another reviewer for approval shopping.

Strict prospective evidence and grants use schema 3 safe identity: matrix version and
digest, role bindings, model/provider/tool identities, and canonical attempt outcomes.
Raw argv, process output and credentials are excluded. Grant revalidation rejects
matrix, role or identity drift while keeping binding independence and family
independence as separate checks.

## Consequences

Projects can add providers and tools without changing AO code, while authority compares
stable declarations. Shell-free execution removes command-string interpretation. Ordered
fallback handles operational outages predictably, and unavailable review remains a third,
non-authorizing state rather than a negative verdict. Strict and legacy evidence cannot
cross-authorize.

## Rejected alternatives

- **Hard-coded provider/model registry:** couples AO releases to vendor catalogs and
  turns project policy into product code.
- **Shell command strings:** add quoting and injection ambiguity to reviewer execution.
- **Inline family overrides:** allow a route to spoof independence from its bound model.
- **Runtime probing as authority:** availability observations do not attest declared
  provider, model, family or capability identity.
- **Approval shopping:** retrying after a substantive invalid or negative review biases
  fallback toward authorization rather than availability.

## Limitations

- Provider, model and family identity is declared, not remotely or cryptographically
  attested. AO proves configuration consistency, not which model a provider ran.
- When the matrix key is absent, legacy routing remains available by design.
- Runtime availability is separate from declaration validity and can still leave every
  eligible route unavailable.
- Only capability-matrix version 1 is accepted; later schemas require a new explicit
  version and migration decision.

## References/Supersession

- Implementation: [`src/ao/matrix.py`](../../src/ao/matrix.py), strict opt-in handling
  in [`src/ao/lib.py`](../../src/ao/lib.py), and execution in
  [`src/ao/cli.py`](../../src/ao/cli.py)
- Evidence: [`tests/test_capability_matrix.py`](../../tests/test_capability_matrix.py)
- Background: [capability matrix](../capability-matrix.md), [roles](../roles.md), and
  [adapters](../adapters.md)
- Landed in commit `ed03184` (`feat: add capability-based reviewer routing`)
- Supersedes: none
