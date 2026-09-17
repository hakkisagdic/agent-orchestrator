# ao on a donor's machine — a design note

*Status: a design, not decided and not built. It records how ao could run on a machine whose
owner lends its agent quota to work someone else coordinates.*

## Three layers, three tools

A donor lends the agent quota already on their machine to a coordinator: a service or a
maintainer that hands out work orders and decides what lands. Three things must happen on the
donor's machine, and each belongs to one layer:

| layer | owner | what it holds |
|---|---|---|
| **authority** | the coordinator | the work order, the acceptance boundary it carries, per-effect authorization keys, usage receipts, the merge decision; nothing on the donor machine can widen them |
| **accounts, quota, fleet** | [keyflip](keyflip.md) | which accounts exist on the machine, which is active, how much of each window is left, switching, the fleet view across machines |
| **execution and quality** | ao | driving the agent the donor already has (adapters), gates under the machine lock, independent review, commit authority by digest, the cost menu, the ledgers |

"Install keyflip, get ao by default" is coherent in this shape: keyflip decides *whose quota*,
ao decides *how the work is done and proven*, and the coordinator decides *what may be done and
whether it lands*.

## What ao would need: a donor profile

<!-- not built: the donor profile is a design; ao init offers claude-kiro and claude-claude -->
- `ao init --profile donor`: implementer, reviewer and gates; `architect_wake`, `refill` and
  `reports` off. Nobody plays architect on a donor machine: the work order carries the
  boundary, and the coordinator answers the questions.
- Authority from outside: `commit-ok` takes the work order's authorization key as the grant,
  records the receipt and mints nothing locally.
- It runs inside the coordinator's isolation, never beside it: no network, no push, no
  repository outside the work order.
- Receipts out: the verification, review and authority ledgers become the donor's usage
  receipt — an authentic one, or `unavailable` when there is none.
- Alarms reach the donor through keyflip's channel only.
- Conformance, not adoption: the coordinator's specification defines the capacity profiles and
  an adapter conformance suite, and ao implements that interface. The specification stays the
  authority; ao is one implementation of it.

## Sequencing

Not before a coordinator publishes the interface ao would implement: building the profile
first would write the contract twice.

## Other harnesses

The donor layer is adapter-based on purpose. A new harness is one more adapter and one more
conformance run, not a new architecture. The conformance profile decides, not the brand.
