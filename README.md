**English** | [Türkçe](README.tr.md)

# agent-orchestrator

**Point it at the agent you are already running. It picks up from there.**

No new harness. No re-planning. No "start a fresh session so the tool can manage it."
`ao` attaches to a coding agent that is already working — in your IDE, in a terminal,
started by someone else — reads its state without touching it, and takes over the
tedious half: noticing when it stalls, running the gates, deciding what may be
committed, and keeping work moving while you sleep.

That is the whole difference. Every other orchestrator owns the agent: it spawns the
process, it drives the loop, and adopting it means restarting your work inside it.
`ao` owns the *authority* instead — what is finished, what is good, what may land —
and leaves the agent where it is.

```bash
pip install ao-orchestrator                  # or: uv tool install ao-orchestrator
cd ~/your-project && ao status      # it finds the running session by itself
```

Or with nothing installed at all, using the Python macOS and most Linux distributions
already ship:

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator ~/ao
echo 'alias ao="$HOME/ao/bin/ao"' >> ~/.zshrc && exec zsh
```

No dependencies, by choice — this watches agents on machines it does not control, and
a dependency is a thing that can be missing exactly there. Nothing to configure before
the first run: `ao` discovers the session from the agent's own store.

**Windows** ships PowerShell and does not ship Python, so `bin/ao.ps1` covers `status`,
`board` and `doctor` with nothing installed. It is a deliberate subset and stays one —
everything else either takes a decision, spends the machine, kills processes or speaks
a protocol, and a second implementation of any of those is a second thing to be wrong.
For the rest: `winget install Python.Python.3.12 && pip install ao-orchestrator`.
The script is written and reviewed but **not yet run on Windows**.

> **Where this came from.** Extracted from a 30-epic durable-workflow product built
> over weeks by exactly this loop. Every guard in here exists because something went
> wrong first: a watchdog that started a second turn on one session and left a rename
> half-applied; fifteen agent processes accumulated in one repository; a timestamp bug
> that made live evidence look three hours stale. [`docs/lessons.md`](docs/lessons.md)
> is the list. None of it was designed in advance.

---

## What it does

**Watches.** `ao watch` is a live panel: what the agent is doing, context and cost,
open reviews, the work board, and — the part no other panel has — whether it is
*busy but producing nothing*. An agent stuck in a wait loop looks perfectly healthy:
transcript growing, tool calls firing, credits burning. `ao` compares activity
against artifacts and says so.

**Restarts.** Turn-based agents stop when a turn ends, mid-slice or not. A watchdog
notices and nudges. Detection is free — a file mtime and a couple of `git` calls —
and a chain of guards makes sure a nudge is never spent where it cannot help: no
second turn while one is running, no nudge into a provider outage, no nudge past the
round budget, no nudge when a human has taken the tree.

**Verifies.** `ao verify` runs *your* gates and writes the numbers to a ledger. Not
the agent's report of its gates — the commands, run again, by something with no stake
in the result.

**Decides.** `ao commit-ok` grants commit authority from that evidence: gates passed,
review approved, plan unedited, and the measurement still describes the tree in front
of it. Every refusal names its missing condition. It never covers `push`.

**Keeps going.** A slice blocked on a human decision is parked with the reason
recorded, and work moves to the next pre-authorised item. When the queue runs dry, the
*architect* is woken to refill it — never the implementer, because choosing your own
scope is the one authority an implementer must not have.

## Commands

| | |
|---|---|
| `ao status` · `ao watch` | one project: state, telemetry, problems, board |
| `ao watch --all` · `ao fleet` | every project, ordered by what needs a human first |
| `ao board` | where each item is; READY = queued items whose `needs:` are done |
| `ao verify [-p full]` | run the declared gates, record the result |
| `ao commit-ok` | may this tree be committed? decided from evidence |
| `ao hold` / `ao hold release --note …` | stop every agent in the tree, and keep them stopped |
| `ao writers` / `ao writers --clean` | live turns in the tree (one per turn, not per process), orphans set aside; `--clean` stops only the orphans |
| `ao fanout ok --agents N` / `ao fanout record …` / `ao fanout history` | may a fan-out of N sub-agents start now (hard cap, recent limit hit, provider window); record what one cost |
| `ao email setup` / `ao email test` | the red alarm channel: e-mail via formsubmit.co, no server ([alarms.md](docs/alarms.md)) |
| `ao alarms` / `ao alarms test --level red` | live alarm episodes and their level; test rings every channel |
| `ao mail log` / `ao mail search <text>` | the mail ledger: every message written and when it was consumed, searchable after deletion |
| `ao watchdog explain` / `ao watchdog trace` | why the watchdog did or did not act: measurements and verdicts of this cycle, and of the recorded ones ([watchdog.md](docs/watchdog.md)) |
| `ao source import` | admit tracker items onto the board |
| `ao mail` · `ao notices` | coordination messages; alerts this project raised |
| `ao watchdog install` | launchd job that restarts a stalled agent |
| `ao mcp serve` · `ao a2a serve` | expose state to MCP clients / as A2A tasks |
| `ao telegram setup` | alerts to your phone, decisions back from it |
| `ao digest [--days N]` | what happened, read from the ledgers — also answers "why is nothing moving" |
| `ao ask` · `ao answer` · `ao decisions` | questions answerable in one tap; free text always last |
| `ao note` | an architect message into the mailbox, through the tool |
| `ao review` | review the tree with an actor that did not write it |
| `ao handoff` | write and send everything a successor needs |
| `ao a2a-mcp serve` | reach A2A agents from an MCP-only client |
| `ao prune` | trim accumulated records and logs |
| `ao doctor` · `ao adapters` | check the wiring; what is supported and how well |

## The rules it enforces

These are not style preferences. Each one is a failure that cost real hours.

- **Whoever writes the code does not verify it, and does not decide it may land.**
- **A plan is read, never edited** — when the document a slice is judged against can be
  edited by the thing being judged, every later check is circular.
- **Pulling work is not authorising it.** A tracker item is something a person wrote,
  not a specification anyone verified. It enters the queue with a written acceptance
  boundary or it does not enter.
- **Heavy operations belong to one actor.** Gates are serialised machine-wide; N
  projects running N test suites is not N times the throughput, it is one suite that
  no longer finishes.
- **Authority lives in always-included context, never in a mailbox.** A stuck agent is
  exactly the agent not reading its mail.
- **`push` is never granted by this tool.** Nor PRs, force-pushes, or hook bypasses.

## Agent support

`ao` reads each agent's own session store; the adapter says where and in what shape.

| verified | adapters |
|---|---|
| **full** — every capability exercised in a production run | kiro, claude-code, antigravity |
| **partial** — reads state, some capabilities unexercised | opencode, command-code |
| **documented** — written from published docs, not yet run | cursor-agent |
| **untested** — schema present, needs a first run | codex, gemini, aider, amp, copilot, amazon-q, deepseek, qoder, ollama |

`ao adapters` shows this table against what is actually installed on your machine, and
— with [keyflip](https://github.com/hakkisagdic/keyflip) — whether an account exists
even when the CLI does not. Adding one is a JSON file; see
[`docs/adapters.md`](docs/adapters.md).

## Documentation

[protocol](docs/protocol.md) · [safety](docs/safety.md) · [roles](docs/roles.md) ·
[slices](docs/slices.md) · [gates](docs/gates.md) · [sources](docs/sources.md) ·
[adapters](docs/adapters.md) · [parallel](docs/parallel.md) · [cloud](docs/cloud.md) ·
[models](docs/models.md) · [telegram](docs/telegram.md) · [mcp](docs/mcp.md) · [telemetry](docs/telemetry.md) ·
[surfaces](docs/surfaces.md) · [ledger](docs/ledger.md) · [recovery](docs/recovery.md) ·
[keyflip](docs/keyflip.md) · [ide-extensions](docs/ide-extensions.md) ·
**[lessons](docs/lessons.md)**

## Status

Working today: everything in the command table above, exercised daily against a real
project. Still specification: `ao init`, `ao decide`, `ao since`, and cross-project
parallel *execution* (the view exists; running several implementers at once is
governed by the machine gate lock but has not been run in anger).

MIT.
