# ao on the donor's machine — a proposal

*Status: proposal, 2026-09-06. Not decided. The donor harness is part of Voltrai's
architecture and that decision is Hakkı's; this page states the case so the
decision has something concrete to accept or reject.*

## Three layers, three tools

Voltrai's donor lends the agent quota already on their machine to a maintainer's
issues. Three things must happen there, and three tools already do one each:

| layer | owner | what it holds |
|---|---|---|
| **authority** | Voltrai core | the work order, the acceptance boundary from the issue, per-effect authorization keys, usage receipts, the merge decision; nothing on the donor machine can widen them |
| **accounts, quota, fleet** | keyflip | which accounts exist on the machine, which is active, how much of each window is left, switching, the fleet view across machines |
| **execution and quality** | ao | driving the agent the donor already has (adapters), gates under the machine lock, independent review, commit authority by digest, the cost menu, the ledgers |

"Install keyflip, get ao by default" is coherent in this shape: keyflip decides
*whose quota*, ao decides *how the work is done and proven*, Voltrai decides
*what may be done and whether it lands*.

## What ao would need: a donor profile

- `ao init --profile donor`: implementer + reviewer + gates; `architect_wake`,
  `refill`, `reports` off. There is no human architect on a donor machine; the
  work order carries the boundary and Voltrai core answers the questions.
- Authority from outside: `commit-ok` takes the work order's authorization key
  as the grant, records the receipt, mints nothing local.
- Runs inside Voltrai's isolation backend (Epic 15 B3), never beside it: no
  network, no push, no repository outside the work order.
- Receipts out: verification, review and authority ledgers become the donor's
  usage receipt (Epic 21: authentic receipt, `unavailable` when absent).
- Alarms to the donor through keyflip's channel only.
- Conformance, not adoption: Epic 21 defines the LSM-1 capacity profiles and the
  adapter conformance SDK; ao implements that interface. The spec stays the
  authority, ao an implementation of it.

## Sequencing

Not now. B8–B10 (the Epic 17 fixture chain) and Epic 21's conformance SDK define
the interface ao would implement; building the donor profile first would write
the contract twice. The moment is Epic 21's first slice.

## Other harnesses

The donor layer is adapter-based on purpose. A new harness — a DeepSeek-based
one, a revived multi-harness CLI — is one more adapter and one more conformance
run, not a new architecture. The conformance profile decides, not the brand.
