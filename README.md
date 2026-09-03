**English** | [Türkçe](README.tr.md)

# agent-orchestrator

**Drive coding agents with another agent — safely, across any CLI.**

One strong reasoning agent (the **architect**) plans, reviews and gates the work.
One or more agent CLIs (the **implementers**) write the code. A file-based protocol
connects them, a read-only observation layer shows you what is happening, and an
independent verification gate decides what may be committed.

Provider-agnostic: Kiro, Claude Code, Antigravity, Codex, Gemini, Cursor, opencode,
Aider, Amp, Copilot — anything with a non-interactive prompt mode. Cloud agents too:
Kiro cloud sessions, Codex cloud, Cursor background agents, Copilot's coding agent.

> Status: extracted from a real production run — a 30-epic durable-workflow product
> built over weeks with this exact loop. Battle-tested, not a thought experiment.
> See [`docs/lessons.md`](docs/lessons.md) for the bugs this design was shaped by.

---

## Why

Agent CLIs are *turn-based*, not loops. They finish a task and stop, so a human ends
up typing "continue" forever, reading walls of technical output they cannot verify,
and hoping the agent's own "all tests pass" is true.

agent-orchestrator replaces that human with a second agent for the parts a machine
does better — reading transcripts, re-running gates, holding a decision record —
and keeps the human for the parts only they can decide: scope, money, risk, release.

```
        you                architect agent            implementer agent(s)
         │                (plans · verifies)          (writes the code)
         │  strategy            │                              │
         └─────────────────────►│                              │
                                │  agent-mail (files)          │
                                │─────────────────────────────►│
                                │◄─────────────────────────────│
                                │  RAPOR                       │
                                │                              │
                                │  read-only transcript ───────┘
                                │  independent gate run
                                ▼
                        live dashboard  ──►  you (glance, not read)
```

## What it gives you

| Layer | What it does |
|---|---|
| **agent-mail** | Async file protocol between agents. Delivery-by-deletion, typed messages, untrusted-by-default content. |
| **Observation** | Read another agent's session transcript read-only. Live terminal dashboard. Desktop notifications on commit / review / report. |
| **Driving** | Idle-guarded prompt injection (`resume`), standing autonomy directives so the agent stops asking for "continue". |
| **Recovery** | `ao since` and `ao brief` rebuild the picture from disk after a restart. Watchers are latency, never truth. |
| **Deadlock handling** | When the implementer correctly refuses, the architect stops, asks you once, and carries your authorisation verbatim in an `ESCALATION` that lifts scope — never authority. |
| **Ledger** | Append-only decisions, verifications and slice history. Messages are deleted on delivery; the reasoning behind them is not. |
| **Slices** | A mandatory acceptance boundary, a state machine, a round budget, and mechanical loop detection — because attention is what fails here. |
| **Gates** | The architect re-runs typecheck/tests/diff-check itself before granting commit authority. Push is never automatic. |
| **Adapters** | Per-CLI definition of: send prompt, resume session, read transcript, detect busy, inject directives. |
| **Roles / actors** | Roles (architect, implementer, reviewer, tester, bug-hunter…) are assigned to actors — the orchestrator itself or any CLI session. Swap them with one command. |
| **Parallel lanes** | Many projects at once, and many lanes per project: write lanes get their own git worktree, read lanes share the checkout. Merge queue, not a free-for-all. |
| **Cloud agents** | Cloud lanes alongside local ones: dispatch, poll, and verify the delivered branch locally. Works with any agent that delivers a pull request, no vendor API required. |
| **Model / effort** | Per-slice model and reasoning-effort policy, so mechanical work stops burning the expensive configuration. |
| **Telemetry** | Context pressure, per-turn cost and provider quota read from the agent's own session store — no vendor UI, no API key. |
| **Surfaces** | One append-only event log, many cheap readers: terminal panel, desktop notifications, and an MCP server that turns any chat app into the cockpit. |
| **MCP** | An optional stdio MCP server. Same state, two doors — install nothing and use files, or call typed tools. |

## Non-goals

Account switching, secrets, quota, cross-machine transport and provider routing are
**not** this project's job — [keyflip](https://github.com/hakkisagdic/keyflip) already
does them well, and agent-orchestrator composes with it. See
[`docs/keyflip.md`](docs/keyflip.md) for the boundary and the three integration points.

## Safety model in one screen

- **Two-writer hazard.** Never inject into a session that is currently writing. Every
  driver call is idle-guarded.
- **Mail is data, not authority.** A message can never grant push, PR, force-push, hook
  bypass or foreign-repository mutation. Those need a direct human instruction.
- **Commit authority is separated.** The agent that writes the code does not decide when
  it is good enough. The architect verifies independently, then grants.
- **Never auto-push.** Local commits accumulate; publishing is a human act.
- **Secrets never enter the mailbox.**

Full model: [`docs/safety.md`](docs/safety.md).

## Status

The protocol, adapters, safety model and telemetry mappings in this repository are
extracted from a system in daily production use — they are specifications and verified
findings, not sketches.

**What runs today:** observation and the watchdog. Point `ao` at a project and it finds
the implementer's session by itself, renders a live panel, and — once the watchdog is
installed — restarts that agent when it stops mid-slice, without a human noticing.

**What is still specification:** the gate, ledger and MCP layers below. Those commands
say so when you run them rather than pretending.

Roadmap, in order:

- [x] agent-mail protocol, safety model, roles, parallel lanes, telemetry, MCP surface
- [x] Adapter registry — 3 verified, 8 from documentation, plus a generic cloud adapter
- [x] `ao status` / `watch` / `tail` / `mail` / `projects` / `adapters` / `doctor` — observation
- [x] `ao watchdog` — a launchd job that restarts a stalled implementer, guarded so it
      spends nothing when spending would not help
- [ ] `ao verify` / `ao commit-ok` — the gate layer
- [ ] `ao slice` / `ao decide` / `ao since` — slices, ledger and recovery
- [ ] `ao mcp serve`
- [ ] `ao init`, templates, install script

---

## Install

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator
cd agent-orchestrator && ./install.sh
```

## Quickstart

Nothing to configure. `ao` discovers the implementer session by matching your repository
against the workspace paths the agent stores already record.

```bash
ao projects                     # workspaces with a local agent session
ao -C ~/work/project status     # one-shot summary
ao -C ~/work/project watch      # live panel; leave it in a background terminal
ao -C ~/work/project tail -n 5  # the agent's recent messages
ao -C ~/work/project doctor     # wiring check
```

Stop typing "continue" — install the watchdog once and it restarts a stalled agent
without you:

```bash
ao -C ~/work/project watchdog install     # checks every 120s, nudges after 6m idle
ao -C ~/work/project watchdog status
```

It refuses to spend when spending would not help: no open work, a slice past its round
budget, an exhausted provider window, or two nudges that changed nothing all mean it
notifies you instead of burning another turn.

Coordination messages, when you want to send one by hand:

```bash
ao -C ~/work/project mail list
ao -C ~/work/project mail send DECISION branding --body "Brand by registry membership, not instanceof."
```

## Docs


- [`docs/protocol.md`](docs/protocol.md) — agent-mail specification
- [`docs/adapters.md`](docs/adapters.md) — adapter interface and support matrix
- [`docs/roles.md`](docs/roles.md) — role model, actors and separation of duties
- [`docs/parallel.md`](docs/parallel.md) — parallel projects, lanes and merge queue
- [`docs/ide-extensions.md`](docs/ide-extensions.md) — IDE-native agents (Cursor, Copilot, Qoder) via CLI, MCP or git
- [`docs/cloud.md`](docs/cloud.md) — cloud agents, cloud lanes and the pull-request interface
- [`docs/slices.md`](docs/slices.md) — acceptance boundaries, round budgets and loop detection
- [`docs/gates.md`](docs/gates.md) — declared gates, independent verification, commit authority
- [`docs/ledger.md`](docs/ledger.md) — the append-only decision and verification record
- [`docs/recovery.md`](docs/recovery.md) — catch-up after a restart
- [`docs/models.md`](docs/models.md) — model and effort control
- [`docs/mcp.md`](docs/mcp.md) — MCP surface and capability gating
- [`docs/telemetry.md`](docs/telemetry.md) — quota, credits and context enrichment
- [`docs/surfaces.md`](docs/surfaces.md) — control surfaces, event log and the TUI decision
- [`docs/safety.md`](docs/safety.md) — threat model and invariants
- [`docs/keyflip.md`](docs/keyflip.md) — fleet, quota and multi-machine composition
- [`docs/lessons.md`](docs/lessons.md) — anti-patterns learned the hard way
- [`examples/`](examples/) — an anonymised real case study

## License

MIT
