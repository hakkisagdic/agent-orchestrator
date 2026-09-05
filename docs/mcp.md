# MCP surface

Everything in agent-orchestrator works with files and a CLI, on purpose: an agent that can
read a directory can join the protocol with **nothing installed**. That path is not going
away and stays the default.

But if your agent already speaks MCP, calling a tool is nicer than parsing a directory.
So `ao` also ships as an MCP server. Same protocol, same state, two doors.

## Running it

```bash
ao mcp serve                 # stdio MCP server
```

Register it the way your agent registers any stdio server:

```jsonc
{ "mcpServers": { "agent-orchestrator": { "command": "ao", "args": ["mcp", "serve"] } } }
```

The server is launched by the agent over stdio — there is no daemon, no port, no hosted
service. It reads and writes exactly the same mailbox and repository state the CLI does,
so the two can be mixed: an architect using files and an implementer using MCP interoperate
without knowing about each other.

## Tools

| Tool | Capability | What it does |
|---|---|---|
| `ao_mail_list` | read | List pending messages addressed to me. |
| `ao_mail_read` | read | Read one message in full. |
| `ao_mail_ack` | read | Delete a message — the delivery acknowledgement. |
| `ao_mail_send` | write | Write a reply or report. |
| `ao_status` | read | Repo, gates, mailbox and session state in one object. |
| `ao_transcript_tail` | read | Last N messages of another agent's session, read-only. |
| `ao_verify` | run | Re-run the project's gates and return the raw results. |
| `ao_resume` | **drive** | Inject a prompt into another agent's session. Idle-guarded. |
| `ao_commit_request` | **authority** | Ask the architect for commit authority for a file set. |

## Capability gating

Tools are grouped, and groups are enabled explicitly:

```bash
ao mcp serve --allow read,write          # default: coordination only
ao mcp serve --allow read,write,run      # plus gate execution
ao mcp serve --allow read,write,run,drive # plus driving other sessions
```

`drive` and `authority` are never on by default. An agent that can inject prompts into
other agents' sessions is a different security proposition from one that can read a
mailbox, and that step should be a decision someone made on purpose.

The invariants from [`safety.md`](safety.md) hold identically over MCP: no tool grants
push, PR, force-push, hook bypass or foreign-repository mutation, and `ao_resume` refuses
a target that is currently writing.

## Files or MCP?

| Prefer files when | Prefer MCP when |
|---|---|
| The agent has no MCP support, or you do not want to configure one. | Your agent already loads MCP servers. |
| You want the coordination history visible and greppable on disk. | You want typed tool calls instead of parsed markdown. |
| Agents run on different machines with a shared folder between them. | Everything runs locally under one agent runtime. |
| You are debugging the protocol itself. | You want fewer moving parts in the prompt. |

Mixing is supported and normal. The mailbox remains the source of truth in both cases —
MCP is an interface to it, not a replacement for it.

## Coordination over MCP, backed by the same files

`ao mcp serve` exposes three coordination tools alongside the read-only ones:

| tool | for |
|---|---|
| `ao_inbox` | messages addressed to the implementer, not yet acknowledged |
| `ao_ack` | confirm delivery by removing one, after applying or rejecting it |
| `ao_report` | tell the architect something at any point in a turn |
| `ao_fanout` | may a fan-out of N sub-agents start now; record what one cost ([fanout.md](fanout.md)) |

**They read and write the mailbox files directly.** There is no MCP-side database,
and that is the whole design: a second store for the same fact is a second thing
to drift, and every expensive failure in this project's history has been two
records of one truth disagreeing. So a project without MCP loses nothing — the
files are the protocol, and MCP is a faster door onto the same room.

`ao_report {kind: "blocked"}` writes the marker the watchdog escalates on within
one cycle. That matters more than it sounds: three finished slices once sat for
half a day because a detector had to infer a blockage the agent already knew
about. An agent that can say "I am stuck" in one tool call has no reason to be
inferred about.

Authority stays off this surface. `ao commit-ok` decides whether work may land and
is deliberately not exposed — an implementer able to grant itself commit authority
would remove the separation the tool exists to hold. `push` is granted by nothing.

## Registering it

```bash
kiro-cli mcp add --name ao --scope workspace \
  --command "$HOME/.local/bin/ao" \
  --args '["-C","/path/to/project","mcp","serve"]'
```

Other clients take the same shape; `ao mcp` with no arguments prints the JSON.

## A2A, and why it is not this

Kiro does not speak A2A. Searched both the CLI binary and the IDE bundle: no
`agent2agent`, no agent card, no `.well-known/agent`, no task lifecycle methods —
the only `a2a` string in the IDE is `a2a-sdk` sitting in a package-name list. MCP,
by contrast, is genuinely implemented in both.

So A2A is an outward surface here, not an inward dependency: `ao a2a serve` lets an
A2A-speaking orchestrator read this board, while the loop itself runs over MCP and
files. Bridges in the other direction exist — [a2a-mcp](https://github.com/a2anet/a2a-mcp)
exposes remote A2A agents as MCP tools — and would let an MCP-only agent reach an
A2A one, if that is ever needed.

## MCP cannot interrupt, and pretending otherwise is the bug

An A2A agent receives an inbound message and can act on it at a suitable point.
An MCP agent cannot: tools fire only when the agent chooses to call one. So an
urgent message sits unread until its next `ao_inbox` — a turn away, or never if it
is stuck. No protocol design closes that gap, and the Kiro CLI offers no hook to
close it either: `preToolUse` and `postToolUse` do not exist in the headless path.

What does exist is a boundary the agent crosses on its own. It runs `ao` to take
the machine lock, to verify, and to ask whether it may commit — all of them just
before something expensive or irreversible. So the message travels there, in three
tiers by how hard each one bites:

| tier | surface | effect |
|---|---|---|
| 1 | `ao lock`, `ao verify` | prints the message before running, flushed so it precedes the subprocess |
| 2 | every `ao_*` MCP response | attaches `URGENT_UNACKNOWLEDGED` to whatever the agent asked for |
| 3 | `ao commit-ok` | **refuses** while any remains unacknowledged |

Tier 3 is the one that actually holds. We cannot stop an agent from working, but
we decide whether the work may land — so an unread urgent message becomes a closed
gate rather than a missed notification. That is the same authority the tool already
owns, pointed at a delivery problem.

Only messages marked `## ACİL` / `## URGENT` travel this way. Routine coordination
waits for the turn-start inbox check, because a channel that carries everything is
one people learn to skim, and then tier 3 is the only tier left.

When it truly must stop now, `ao hold` kills the turn. That is a real interrupt and
it costs the in-flight work, which is why it is the last resort rather than the
mechanism.

## Reaching A2A agents from an MCP-only client

`ao a2a-mcp serve` is the other direction: remote A2A agents appear as MCP tools,
so an implementer that speaks only MCP can discover them, send them work and
follow the result.

| tool | |
|---|---|
| `a2a_agents` | configured agents, and the dialect each one speaks |
| `a2a_agent` | one agent's card and advertised skills |
| `a2a_send` | send a message; returns the task, `input-required` included |
| `a2a_task` | state and artifacts of a task already sent |
| `a2a_cancel` | cancel one |

Agents live in `.ao/a2a-agents.json` — a file, not an environment variable,
because everything else here keeps state where a human can read it during an
incident, and a registry that exists only inside a process is the one thing you
cannot inspect when something is wrong.

### Why not use the existing bridge

[a2anet/a2a-mcp](https://github.com/a2anet/a2a-mcp) does this job and came first.
It speaks A2A 0.3, and 0.3 is no longer what the specification says. 1.0 renamed
every JSON-RPC method, re-cased the roles, prefixed every task state and changed
the content type:

| | 0.3 | 1.0 |
|---|---|---|
| method | `message/send`, `tasks/get` | `SendMessage`, `GetTask` |
| role | `user` | `ROLE_USER` |
| state | `input-required` | `TASK_STATE_INPUT_REQUIRED` |
| content type | `application/json` | `application/a2a+json` |

An 0.3-only client fails silently against a 1.0 agent; a 1.0-only client fails
against most of what is deployed. So this reads the agent card, takes the version
the agent declares, and speaks that dialect — trying both when a card declares
nothing, since an omitted version is far more common than a wrong one. Callers see
one normalised vocabulary either way.

`ao a2a serve` now answers both, verified against itself through this bridge:
`ListTasks` returns `TASK_STATE_INPUT_REQUIRED` and `tasks/list` returns
`input-required`, from the same board.

## Registration is part of `ao init`

`ao init` registers the server for every agent it detects — `.mcp.json` for
Claude Code, `.kiro/settings/mcp.json` for Kiro, a snippet for Codex's
user-level config — merged into whatever those files already hold, and writes
the playbook (`ao skill install`) beside it. The one step it cannot do is the
restart: the human starts or restarts the app in the directory so the tools
load, then runs `ao doctor`. `--no-mcp` skips the registration.
