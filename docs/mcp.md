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
