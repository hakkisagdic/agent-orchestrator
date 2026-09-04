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
