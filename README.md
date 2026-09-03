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

The `ao` CLI that ties them together is still being extracted from that private
implementation. **The commands shown below describe the intended surface; they do not run
yet.** Until they land, this repo is useful as: a protocol you can implement in an
afternoon, a verified adapter registry, and a safety model worth stealing.

Roadmap, in order:

- [x] agent-mail protocol, safety model, roles, parallel lanes, telemetry, MCP surface
- [x] Adapter registry — 3 verified, 8 written from documentation
- [ ] `ao status` / `ao watch` — the observation layer (reference scripts exist privately)
- [ ] `ao mail` / `ao resume` — the driving layer with idle guard
- [ ] `ao verify` / `ao commit-ok` — the gate layer
- [ ] `ao mcp serve`
- [ ] `ao init`, templates, install script

---

## Install

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator
cd agent-orchestrator && ./install.sh
```

## Quickstart

```bash
ao init --implementer kiro            # write steering + hooks + mailbox into this repo
ao mail send DECISION "use WeakSet branding, not instanceof"
ao watch                              # live panel; leave it in a background terminal
ao status                             # one-shot summary
ao verify                             # re-run the gates yourself
ao commit-ok "feat: ..."              # grant commit authority after gates pass
```

## Docs

- [`docs/protocol.md`](docs/protocol.md) — agent-mail specification
- [`docs/adapters.md`](docs/adapters.md) — adapter interface and support matrix
- [`docs/roles.md`](docs/roles.md) — role model, actors and separation of duties
- [`docs/parallel.md`](docs/parallel.md) — parallel projects, lanes and merge queue
- [`docs/ide-extensions.md`](docs/ide-extensions.md) — IDE-native agents (Cursor, Copilot, Qoder) via CLI, MCP or git
- [`docs/cloud.md`](docs/cloud.md) — cloud agents, cloud lanes and the pull-request interface
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
