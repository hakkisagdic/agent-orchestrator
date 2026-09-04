# Composing with keyflip

[keyflip](https://github.com/hakkisagdic/keyflip) and agent-orchestrator solve adjacent
problems and are designed to compose. Holding the boundary below keeps both tools small.

## The boundary

| Question | Owner |
|---|---|
| *Which account / provider does this agent run as?* | keyflip |
| *Is there quota left? Should I rotate accounts?* | keyflip |
| *Which machines do I own, and how do commands reach them securely?* | keyflip |
| *How does project memory move when a project changes tool?* | keyflip (`handoff`, `ctxsync`) |
| *What is the agent doing right now?* | agent-orchestrator |
| *Is the work finished, and is it actually good?* | agent-orchestrator |
| *May it commit? May it push?* | agent-orchestrator |
| *How do two agents coordinate on one task over time?* | agent-orchestrator |
| *When should the agent be nudged, and is it safe to inject now?* | agent-orchestrator |

Put simply: **keyflip owns identity, quota and transport. agent-orchestrator owns work,
quality and authority.** keyflip moves the agent; agent-orchestrator decides what the
agent should do and whether the result may land.

Two pairs look similar but are not:

- `keyflip handoff` is a **one-shot migration** — "this project is moving from Kiro to
  Cursor, here is a continue-prompt". agent-orchestrator runs a **continuous loop**
  between an architect and an implementer that both stay in place.
- `keyflip swarm run` is **transport** — deliver an argv array to my enrolled machines,
  origin-authenticated and replay-guarded. agent-orchestrator decides **what** that argv
  should be and **verifies** what came back.

## Integration point 1 — remote implementers (fleet transport)

Run the implementer on another machine while the architect stays local.

- The mailbox directory is placed inside keyflip's encrypted fleet rendezvous, so
  messages ride the existing origin-authenticated, replay-guarded bus.
- `ao resume --machine <name>` delegates to `keyflip swarm run` with the adapter's argv
  array (no shell string — nothing to inject into).
- Consent gating stays keyflip's: exec requires explicit `--allow-exec` trust per machine.

agent-orchestrator adds **no** new crypto, no new transport and no new trust store.

## Integration point 2 — quota-aware driving

The single most common cause of a stalled implementer is an exhausted account, and it
looks exactly like "the agent went quiet".

Before every nudge, the driver can ask keyflip:

```bash
keyflip usage --providers        # remaining quota across Claude / Codex / Gemini / Cursor / Copilot
keyflip next --strategy best     # rotate to the account with the most headroom
```

Policy: if the implementer's account is exhausted → rotate → then nudge. If no account
has headroom → do not nudge; surface it to the human instead of burning turns.

## Integration point 3 — which tools are actually usable here

`keyflip surfaces` reports, per tool, whether an authenticated account exists —
without ever reading the secret. Combined with a plain `which`, that answers the
question an adapter registry cannot answer on its own:

```
adapter         verified    on this machine
antigravity     full        installed
copilot         untested    account, no CLI
opencode        partial     installed + account
```

"Account, no CLI" is the useful cell: the credential is already there, so the tool
is one install away from being usable. Without keyflip, `ao` could only report
whether a binary exists.

## Integration point 4 — budgets instead of invented thresholds

The watchdog needs to know whether spending a turn is allowed. Its own answer was
a guessed "97% of the window", which is a number nobody chose. keyflip enforces
per-account 5h/7d budgets the user actually set:

```bash
keyflip budget set <account> --5h 80 --7d 90
```

When a budget exists, `ao` defers to it and stops inventing thresholds. When none
exists, it falls back to reading the raw window. A policy someone chose beats a
constant a script made up.

## Integration point 5 — quota notifications at the source

keyflip already emits `quota`, `switch` and `fleet-reply` events to desktop or a
webhook. Turning them on means quota trouble reaches the human from the component
that actually knows about it, rather than through a second detector in `ao`:

```bash
keyflip notify set --desktop --events quota,switch
```

## Integration point 6 — adapter discovery

`keyflip agents` already enumerates other tools' memory and config locations
(Cursor, Gemini, Codex …). That inventory seeds the adapter registry instead of
hard-coding paths per machine.

## What agent-orchestrator must never reimplement

Account capture or switching, credential storage, encrypted sync, provider routing,
cross-machine key exchange, cost/budget accounting. If you need one of those, call
keyflip — or do without.

## Using them separately

Neither tool requires the other. agent-orchestrator works fine on a single machine with
one account; keyflip works fine without any orchestration. The integration is opt-in and
lives behind `ao config set transport keyflip`.

## Integration point 7 — MCP server definitions

Once the architect pulls work from Linear, Jira or GitHub, every project and every
agent surface needs those MCP servers configured. Done per tool, that is a config
sprawl problem, and it is a *credential* problem — which puts it on keyflip's side
of the boundary, not this one.

keyflip already carries the mechanism:

```bash
keyflip mcpreg set linear --command … --args … --env …   # define once
keyflip mcpreg enable linear --surface claude-code       # turn on per surface
keyflip mcpreg list
```

`ao` therefore names a server and nothing else — `"server": "linear"` in
`.ao/sources.json`. It stores no endpoint, no token, no command line. If the
server is not enabled on the surface the architect runs on, the pull simply finds
no tool, which is the correct failure: visible, and fixed in one place.

Two gaps worth closing on the keyflip side rather than working around here: the
registry is currently empty on this machine (the mechanism exists, unused), and
`enable` targets only `claude-code` and `claude-desktop` — Kiro, Cursor, Codex and
opencode are not yet surfaces.
