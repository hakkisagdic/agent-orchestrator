**English** | [Türkçe](README.tr.md)

# agent-orchestrator

**The code your AI agents commit is reviewed first, waived by a person, or flagged by ao.** By
default a model of another family reviews the exact change an agent staged, and a git hook refuses
anything else at `git commit`. A ledger records every decision; ao reviews a waived change after it
lands, and `ao doctor` and the watchdog flag a commit whose content no review or waiver granted.

Keep the coding agents you already use: Claude Code, Codex, Kiro, Qoder, OpenCode and the others
in the table below. `ao` sits above them. It runs your gates on the exact change an agent staged,
has a model of another family review that change, and lets only that change commit. The hook
refuses everything else at `git commit`, and every decision goes into a ledger that shows
tampering.

## Why

- **Agents commit faster than anyone reads.** A test that passed on one tree says nothing about
  the tree that gets committed. `ao` grants commit authority to one exact staged tree, and its
  hook refuses any other.
- **A model reviewing its own family's work shares its blind spots.** The reviewer is of another
  model family by default, or a person you name, and it runs with tools that only read.
- **You keep your tools.** `ao` reads each agent's own session store and drives its command line.
  Adopting it does not mean restarting your work inside something new.

## Five minutes to the first reviewed commit

```bash
pip install ao-orchestrator          # or: uv tool install ao-orchestrator
cd your-repository
ao init --profile claude-kiro        # Claude Code implements, Kiro reviews; or claude-claude
git add .ao-project && git commit -m "adopt ao"
ao hooks install                     # from here on, only a granted candidate commits
```

Then, for every change your agent stages:

```bash
ao status                            # what your agents are doing, and what waits for you
ao verify                            # your gates, run on the staged candidate
ao review                            # a model of another family reviews exactly that candidate
ao commit-ok                         # may it land? decided from the evidence, refusals named
ao commit -m "feat: what changed"    # commit it, bound to that evidence by its trailers
```

[Getting started](docs/getting-started.md) goes through it step by step, and `ao prove` runs the
guarantees on a throwaway candidate instead of describing them. With one harness for every role,
`claude-claude` asks for a [review tier](docs/roles.md#review-tiers): another model of the same
family, labelled as weaker independence, or a person.

## How a change lands

```text
the agent edits and stages
  -> ao verify      your gates, run again by something with no stake in the result
  -> ao review      another model family reads that candidate and the code around it
  -> ao commit-ok   commit authority for that exact tree, granted from the evidence
  -> ao commit      the commit carries trailers that bind it to the grant
the pre-commit hook refuses every other commit; push is never granted
```

## What else it does

- **Watches.** `ao watch` is a live panel: what each agent is doing, context and cost, open
  reviews, the board, and whether an agent is *busy but producing nothing*.
- **Restarts.** A watchdog notices a turn that stopped mid-work and nudges it - never into a
  provider outage, past the round budget, or into a tree a person has taken.
- **Keeps going.** A board of pre-authorised work: a slice waiting on you is parked with its
  reason, and when the queue runs dry the architect is woken to refill it.
- **Catches up.** A review you waive is recorded, and `ao catchup` has a reviewer of another
  family review the landed range later.
- **Reviews safely.** A reviewer runs with tools that only read, and ao measures that per
  harness: kiro-cli ran untrusted tools all the same with no person to ask, so a Kiro reviewer
  runs as a read-only agent ao writes for it.

Every command is in [docs/commands.md](docs/commands.md).

## Install

`pip install ao-orchestrator`, `uv tool install ao-orchestrator`, or
`brew install hakkisagdic/tap/agent-orchestrator`. No dependencies, by choice: ao watches agents on
machines it does not control, and a dependency is a thing that can be missing exactly there. With
nothing installed, the Python macOS and most Linux distributions ship runs it from a clone:

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator ~/ao
mkdir -p ~/.local/bin && ln -s ~/ao/bin/ao ~/.local/bin/ao    # ~/.local/bin must be on PATH
```

A symlink, not a shell alias: neither `/bin/sh`, which runs ao's commit hook, nor an agent the
watchdog starts without your shell can see an alias. On **Windows**, `bin/ao.ps1` covers `status`,
`board` and `doctor` with nothing installed; for the rest,
`winget install Python.Python.3.12 && pip install ao-orchestrator` ([windows](docs/windows.md)).

> **Where this came from.** Extracted from a 30-epic durable-workflow product built
> over weeks by exactly this loop. Every guard in here exists because something went
> wrong first: a watchdog that started a second turn on one session and left a rename
> half-applied; fifteen agent processes accumulated in one repository; a timestamp bug
> that made live evidence look three hours stale. [`docs/lessons.md`](docs/lessons.md)
> is the list. None of it was designed in advance.

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
| **partial** — reads state, some capabilities unexercised | opencode, command-code, codex, qoder, openai-api |
| **documented** — written from published docs, not yet run | cursor-agent |
| **untested** — schema present, needs a first run | gemini, aider, amp, copilot, amazon-q, deepseek, ollama, droid, grok, hermes, kilocode, kimi, omp, pi, pr-agent, qwen, reasonix, trae |

`ao adapters` shows this table against what is actually installed on your machine, and
— with [keyflip](https://github.com/hakkisagdic/keyflip) — whether an account exists
even when the CLI does not. Adding one is a JSON file; see
[`docs/adapters.md`](docs/adapters.md).

The suite runs on Linux, macOS and Windows with Python 3.9 and 3.12, on hosted runners and
locally before every push ([commands](docs/commands.md#tests)).

## Documentation

**[getting started](docs/getting-started.md)** · **[commands](docs/commands.md)** ·
[protocol](docs/protocol.md) · [safety](docs/safety.md) · [threat model](docs/threat-model.md) · [roles](docs/roles.md) ·
[privacy](docs/privacy.md) · [security policy](SECURITY.md) ·
[capability matrix](docs/capability-matrix.md) ·
[architecture decisions](docs/adr/README.md) ·
[slices](docs/slices.md) · [gates](docs/gates.md) · [sources](docs/sources.md) ·
[adapters](docs/adapters.md) · [parallel](docs/parallel.md) · [cloud](docs/cloud.md) ·
[models](docs/models.md) · [telegram](docs/telegram.md) · [mcp](docs/mcp.md) · [telemetry](docs/telemetry.md) ·
[surfaces](docs/surfaces.md) · [ledger](docs/ledger.md) · [recovery](docs/recovery.md) ·
[keyflip](docs/keyflip.md) · [ide-extensions](docs/ide-extensions.md) ·
**[lessons](docs/lessons.md)**

## Status

Working today: every command in [the command list](docs/commands.md), exercised daily against a real
project. Still specification: every passage the documents mark as not built yet, read lanes,
a lane's role and the merge queue among them, and cross-project parallel *execution* (the
view exists; running several implementers at once is governed by the machine gate lock but
has not been run in anger).

MIT.
