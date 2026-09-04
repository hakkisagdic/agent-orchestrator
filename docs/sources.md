# Sources — feeding the board from a tracker

A team's real backlog lives in Linear, Jira or GitHub Issues, not in a file in the
repository. If the orchestrator cannot see it, someone has to relay items by hand,
and in practice that means the tracker fills up while the agent sits idle.

## `ao` holds no tracker credential

The MCP client is the **agent**, not this tool. An architect turn pulls with a
tracker's own MCP server, normalises the result to a file, and `ao source import`
admits it. So:

- `ao` gains no network code, no API client, no token store, no new dependency.
- The file-only core still runs with nothing installed. A tracker is an addition
  for people who connect one, never a prerequisite.
- Server definitions and credentials live in **keyflip's** MCP registry
  (`keyflip mcpreg set/enable`), which is where identity already lives. `ao`
  names a server; it never holds one. See [keyflip.md](keyflip.md).

```
.ao/sources.json     what to pull, and what may be admitted
.ao/inbox/<id>.json  a normalised pull, waiting to be admitted
.ao/board.md         the admitted queue
```

## Pulling is not authorising

This is the load-bearing distinction. A tracker item is something a person wrote;
it is not a specification anyone verified. Titles from a real board:

```
DKK-510  ACİL - DEFAULT SMS ALANI PASİF HATASI                  ← a slice
DKK-484  YENİ NAVİGASYON SİSTEMİ VE DESKTOP ÇALIŞMA ALANI       ← a project
```

Both are one line in the same list. An agent told to "work through the backlog"
treats them the same and infers scope for the second one, which is how a bug fix
becomes a refactor nobody approved.

So an item enters `queued` only with a written **acceptance boundary**. Without
one it stays in `inbox` with a reason. `ao` enforces that rule and makes no
judgement of its own — it has no model. An architect turn decides and writes the
boundary into the inbox file.

Size is not a reason to park. A project-shaped item is **decomposed**, not
deferred: the architect writes a plan of phases, each phase naming the files it
touches and why, and each phase enters the board as its own item with its own
boundary. Parking is for genuine ambiguity — a missing product decision — not for
an item being large. An item nobody decomposes is an item that never gets done.

```
.ao/plans/<item-id>.md
```

```markdown
# DKK-484 — new navigation system
source: linear · url: … · shape: project · phases: 3

## Phase 1 — route table extraction
acceptance: routes resolve identically before and after; no visual change
files:
- src/nav/routes.ts      — lift the literal table out of the component
- src/nav/Sidebar.tsx    — read from the table, delete the inline copy
gates: quick
```

Each phase becomes one board line, so the queue holds slices, never projects:

```
- [DKK-484/1] route table extraction · plan: .ao/plans/DKK-484.md · acceptance: …
```

The classification is worth doing with a cheap local model and the boundary with
an expensive one — see [ollama](../adapters/ollama.json) and [roles.md](roles.md).

## A plan is read, never edited

The implementer reads its plan and does not write to it. This is the same
separation the tool already holds elsewhere — the actor doing the work is not the
actor that defines or verifies it — applied to the specification itself.

Without the rule, drift is invisible and self-justifying: an agent that finds the
spec inconvenient edits the spec, and every later check then measures the work
against a document the work itself produced. It is not hypothetical. In this
project's own run the implementer had `.kiro/specs/voltrai/design.md` in its
working set while implementing against it.

`ao source import` records each plan's hash when the item is admitted, and
`ao verify` reports a mismatch as a finding. Cheap to enforce, and it converts a
silent failure into a visible one.

## One source, one repository

`bound_root` in both the config and every pull file must match the project the
import runs in, or the import is refused.

Without the check, a tracker feeding project A can put an item on project B's
board, and an agent that grinds boards without reading URLs implements it in the
wrong repository. The failure is silent and lands as a commit. Memory is not a
control here; the boundary is.

## What refills the queue

Nothing pulls on a timer. The watchdog already distinguishes "idle with open
work" from "idle with none", and the second case splits again: finished, or run
dry. When the admitted queue falls below `refill_below`, it wakes the
**architect** — not the implementer.

That actor split is not incidental. Refilling means choosing what may be worked
unattended, and an implementer that chooses its own scope removes the separation
this tool exists to hold.

## Writing back

A tracker that never hears the result is untrackable within a day. Write-back is
per-source and off by default; when enabled, a verified slice moves the item and
attaches its verification record (gates, counts, commit). What may be written is
the source's own setting, not a global one.
