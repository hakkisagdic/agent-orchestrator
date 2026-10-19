# MCP surface

Everything in agent-orchestrator works with files and a CLI, on purpose: an agent that can
read a directory can join the protocol with **nothing installed**. That path is not going
away and stays the default.

But if your agent already speaks MCP, calling a tool is nicer than parsing a directory.
So `ao` also ships as an MCP server. Same protocol, same state, two doors.

## Running it

```bash
ao mcp serve                 # stdio MCP server
ao mcp serve --role reviewer # the same, serving the reviewer's tools alone (see Roles)
ao mcp serve --project all   # one server for every project in ao's project registry
```

Register it the way your agent registers any stdio server:

```jsonc
{ "mcpServers": { "agent-orchestrator": { "command": "ao", "args": ["mcp", "serve"] } } }
```

The server is launched by the agent over stdio — there is no daemon, no port, no hosted
service. It reads and writes exactly the same mailbox and repository state the CLI does,
so the two can be mixed: an architect using files and an implementer using MCP interoperate
without knowing about each other.

*In ao since slice LEADS-OPEN: both stdio servers refuse a request whose id is
not a string, finite number or null before dispatching its tool, answer with
invalid-request code -32600 and a null id, and read the next line. A nested id
could previously end the server while its reply was encoded. The A2A bridge
also reads on after JSON exceeds the parser's integer or nesting limit, as the
orchestration server already did.*

*In ao since slice WINDOWS-CLOSE:* a request is read as UTF-8 and every reply is seven-bit
JSON, whatever the platform's code page, in this server and in the A2A bridge below. On a
default Windows install a reply holding a Turkish letter or an arrow ended the server
([windows.md](windows.md)).

*In ao since slice LEADS-MCP: a line that is JSON but no object - an array, a number, `null` - is
answered as an invalid request (`-32600`) and the server reads on, as the A2A bridge does since
A2A-HARDEN; one the parser will not hold, a number of more than 4300 digits or nesting deeper than it
recurses, is passed over as a line that is no JSON is. Each ended the server, and its client lost
every tool for the rest of the session.*

*In ao since slice MCP-2026: `ao mcp serve` speaks the Model Context Protocol revision dated
`2026-07-28` beside `2025-06-18`. A `2026-07-28` client negotiates on each request in
`params._meta` (`io.modelcontextprotocol/protocolVersion` and
`io.modelcontextprotocol/clientCapabilities`, with optional `io.modelcontextprotocol/clientInfo`
and `io.modelcontextprotocol/logLevel`) and may probe `server/discover` for `supportedVersions`
(`["2026-07-28", "2025-06-18"]`), `capabilities`, `resultType: "complete"` and
`_meta["io.modelcontextprotocol/serverInfo"]`, which `2026-07-28` replies on `tools/list`,
`tools/call`, `resources/list`, `resources/templates/list`, `resources/read`, `prompts/list`,
`prompts/get` and `ping` also carry; an unknown version in `_meta` is refused with JSON-RPC error
`-32022` (`Unsupported protocol version`) and
`data: {"supported": ["2026-07-28", "2025-06-18"], "requested": ...}`, and missing or malformed
`_meta` with `-32602` (`Invalid params`). An older client that opens the session with `initialize`
and sends no `_meta` on later calls keeps working: `initialize` answers `2025-06-18` for
`2025-06-18` or an unknown or omitted legacy version as that revision's lifecycle specifies,
refuses `2026-07-28` in `protocolVersion` or `params._meta` with `-32602` (`Invalid params`)
pointing at `server/discover` and per-request `params._meta` because `2026-07-28` is stateless and
has no `initialize` handshake (MCP-2026-2), and refuses a non-string or empty `protocolVersion` with
`-32602`.*

## Tools

Each tool has an access class, which says what calling it does to the project: **read**
changes nothing, **write** records something in its mailbox, decisions or ledgers, and
**run** runs its gates.

| Tool | Access | What it does |
|---|---|---|
| `ao_status` | read | The implementer's state, context and cost telemetry, git state, the mailbox and the latest reviews, and the agent processes in the tree, `agent_processes`: null where the process table cannot be read, with `agent_processes_unread` saying why. |
| `ao_board` | read | Where each pre-authorised work item is, what may start now, and what the board cannot resolve. |
| `ao_candidate` | read | The candidate staged now and what a review judges it against: the running slice and its boundary, the candidate's digest and paths, and the committed source a test-only candidate exercises. Not the diff. |
| `ao_notices` | read | Alerts this project raised, and on request the ones the rate limit held back. |
| `ao_fleet` | read | One row per project with a local agent session. |
| `ao_inbox` | write | Messages to the implementer that it has not acknowledged; it records that each was shown. |
| `ao_ack` | write | Acknowledges one message `ao_inbox` lists, once it is applied or rejected: removes it, or records it handled when `mail.store` is append-only. |
| `ao_report` | write | Writes a report to the architect; `blocked` wakes it on the next watchdog cycle, and a repeat folds into the report already standing. |
| `ao_ask` | write | Records a question with options for the architect or a person, and sends it to a phone when one is set up. |
| `ao_decisions` | read | The questions asked, open, proposed or answered. |
| `ao_propose` | write | Records a proposed change to a rule the agent works under, and its question for a person to accept or reject; it writes no rule file ([protocol.md](protocol.md#changing-the-rules-an-agent-proposes-a-person-decides)). |
| `ao_fanout` | write | Whether a fan-out of N agents may start now; with `action: record`, what one cost ([fanout.md](fanout.md)). |
| `ao_watchdog` | read | Why the watchdog acted or did not: one dry cycle, or the cycles it recorded. |
| `ao_verify` | run | Runs the project's gates as `ao verify` does, and records the verification. |

## Resources

*In ao since slice MCP-RES: `ao mcp serve` advertises the `resources` capability and serves four
read-only JSON resources per project through `resources/list`, `resources/templates/list` and
`resources/read`. None carries or records authority.*

| URI | Template | MIME type | What it holds |
|---|---|---|---|
| `ao://<project>/board` | `ao://{project}/board` | `application/json` | Where each pre-authorised work item stands on the board, what is ready, and any graph problems. |
| `ao://<project>/roles` | `ao://{project}/roles` | `application/json` | The project's actors, held roles and any pending reassignment waiting for a running slice to finish. |
| `ao://<project>/ledger` | `ao://{project}/ledger` | `application/json` | Read-only summary counts and latest entries of the slice, verification, review, decision and waiver ledgers (never a grant token). |
| `ao://<project>/decisions` | `ao://{project}/decisions` | `application/json` | The project's latest architect decisions and asked questions, with the count still open or proposed. |

`<project>` is percent-encoded in each `ao://<project>/<kind>` URI so a registered project whose
name contains spaces or reserved URI characters is listed and read as a valid URI (MCP-2026-2). An
unknown URI or a project the server does not serve is refused on `resources/read` with `-32002`
(`Resource not found`) in `2025-06-18` and `-32602` (`Invalid params`) in `2026-07-28`, as each
revision of the specification defines.

## Prompts

*In ao since slice MCP-RES: `ao mcp serve` advertises the `prompts` capability and offers three menu
prompts through `prompts/list` and `prompts/get`.*

| Prompt | Arguments | Ordered tools named |
|---|---|---|
| `status` | `project` (required with `--project all`) | `ao_status` → `ao_board` → `ao_decisions` → `ao_notices` |
| `land-slice` | `slice` (required), `project` (required with `--project all`) | `ao_board` → `ao_status` → `ao_inbox` → `ao_decisions` → `ao_verify` → `ao_candidate` |
| `stuck` | `project` (required with `--project all`) | `ao_status` → `ao_board` → `ao_decisions` → `ao_notices` → `ao_watchdog` → `ao_inbox` |

Each prompt returns a user message naming the project, the `ao` tools to call in order, and the CLI
commands for any step that belongs to a person (`ao commit-ok`, `ao commit`, `ao review`,
`ao person-review`, `ao collect-review`, `ao waive`, `ao answer`, `ao harness seen`), built from
words with every word passed through `shlex.quote` after checking any caller-supplied project
against the project registry's keys by exact match and holding any slice or question id to ao's id
pattern (MCP-2026-2). When the server is
started with `--role`, `prompts/list` and `prompts/get` serve only the prompts whose ordered tools
are all in that role's tool set (MCP-2026-2). No resource, prompt or tool records an approval, a
`commit-ok`, a waiver, an answer or a `harness-seen`: a person's step comes back as the CLI command
text only.

## Serving every registered project (`--project all`)

```bash
ao mcp serve --project all
ao mcp config --project all
```

Started with `--project all`, one server serves every project recorded in ao's machine project
registry (`~/.ao/projects.json`, or `AO_PROJECT_REGISTRY`). Every tool schema requires `project`,
every tool response names `project`, `resources/list` lists the four `ao://<project>/...` resources
for each registered project, and `prompts/list` marks `project` as a required prompt argument. Any
tool call, resource read or prompt get naming a project outside the registry is refused before
anything runs.

## Capability gating

The server has three switches: `--allow-verify`, for the one tool that runs something, `--role`, for
which tools a session is served at all ([Roles](#roles), below), and `--project all`, for serving
every project in ao's project registry on one server.

```bash
ao mcp serve                  # every tool but ao_verify
ao mcp serve --allow-verify   # ao_verify too: it runs the project's real gates
```

Not built yet: grouping the tools by access and switching each group on by itself. No tool
injects a prompt into another agent's session or grants authority: nudging stays with the
watchdog, and commit authority with `ao commit-ok` (below).

The invariants from [`safety.md`](safety.md) hold identically over MCP: no tool grants
push, PR, force-push, hook bypass or foreign-repository mutation.

## Roles

*In ao since slice MCP-ROLES: a server started with `--role` lists and runs only the tools its
role's playbook uses, and `ao init` writes the role into each registration only one role reads.
Before it, every agent was served every tool, so a reviewer could send a report and acknowledge
mail meant for the implementer.*

```bash
ao mcp serve --role implementer   # the implementer's tools alone
ao mcp config --role reviewer     # a client's config, with the role among the server's arguments
```

| Role | Served |
|---|---|
| architect | every tool, as before roles existed |
| implementer | `ao_status`, `ao_board`, `ao_notices`, `ao_inbox`, `ao_ack`, `ao_report`, `ao_ask`, `ao_decisions`, `ao_propose`, `ao_fanout`, `ao_watchdog`, `ao_verify` |
| reviewer | `ao_status`, `ao_board`, `ao_candidate` |

A tool outside its role's set is neither listed nor run: called by name anyway, it answers with an
error and changes nothing. A reviewer reads - the project's state, the board and the candidate it
judges, what [a stand-in reviewer](standin.md#opening-a-stand-in-reviewer) is told to read - and
sends no mail and no report. Urgent mail does not ride along on its answers either, since it could
neither read the message nor acknowledge it. ao starts its own reviewers outside the repository with
their MCP servers off wherever the adapter can turn them off (`options.mcp_isolation`), so the
reviewer's set is for a reviewer session that does load the server: a stand-in whose client a person
configures with `ao mcp config --role reviewer`, or a harness registered as the reviewer alone. The
implementer is served the tools its playbook's mailbox section names, and `ao_verify`, the gate
step of its loop, still only with `--allow-verify`.

With no `--role` the server serves every tool, as it did before roles existed, and says so in one
line on stderr, which an MCP client keeps in its log. A role is read without case or surrounding
space, so `Reviewer` is the reviewer. One it does not know - mistyped, or nothing after `--role` - is
refused, by `ao mcp` and by the server itself, naming the three, and the server does not start; the
line is in the client's log.

*In ao since slice HUNT-LEADS-SEC: a role the server did not know was served every tool, so a
registration that misspelt `reviewer` started a reviewer that could send reports and acknowledge the
implementer's mail. Every registration `ao init` writes names one of the three, or none.*

**Which role a registration names.** `ao init` reads it from `.ao/config.json`: the role whose
block - or a reviewer's fallback - runs a harness that reads the registration. A file holds one
`ao` entry and two harnesses can read one file (Claude Code and Qoder both read `.mcp.json`), so
every harness whose adapter declares that file counts; a snippet a person adds by hand is read by
its own harness alone. One role names the registration. Two or more, or none, name nothing, and
its server serves every tool: in `claude-codex` Claude Code is both the implementer and the
architect, so `.mcp.json` names no role, while in `agy-codex` each role runs on its own harness
and Claude Code, the architect alone, is registered as the architect. A project on a capability
matrix binds its roles to declared tools rather than to
harnesses, and its registrations name none. A role reassigned later, with `ao role set` or
`ao role swap`, reaches the registrations when `ao init` runs again.

So a server with no role is one `ao init` registered for a file two roles read, or none, or a
person's own, and it keeps every tool, the architect's. No agent can put a reviewer on one: ao starts
its reviewers outside the repository, with their MCP servers off wherever the adapter can turn them
off, and hands none a candidate's configuration. An implementer that took the role out of its own
registration would gain two reads, `ao_candidate` and `ao_fleet`, by a change to harness
configuration, which no turn ao starts runs on before a person has seen it
([safety.md](safety.md#harness-configuration-an-agent-changed-waits-for-a-person)). And `ao_ack` takes
the implementer's mail alone, whatever the role (below).

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

The coordination tools, `ao_inbox`, `ao_ack`, `ao_report`, `ao_ask`, `ao_propose` and
`ao_fanout`, are the ones that write. `ao_propose` is on this surface as `ao_ask` is: an agent
proposes a change to a rule it works under and a person decides it, so it grants nothing, and
no tool here answers it.

**They read and write the project's files directly**: the mailbox, the decisions and the
ledgers. There is no MCP-side database,
and that is the whole design: a second store for the same fact is a second thing
to drift, and every expensive failure in this project's history has been two
records of one truth disagreeing. So a project without MCP loses nothing — the
files are the protocol, and MCP is a faster door onto the same room.

*In ao since slice LEADS-MCP: `ao_ack` takes only a message `ao_inbox` lists - one addressed to the
implementer - whatever role the server was started for. It took any name in the mailbox, so an
implementer could consume a report waiting for the architect, the hunter's leads or the watchdog's
anomaly: deleted, or with `mail.store` append-only recorded handled, which the store keeps from the
actor deciding what was handled so that it cannot erase the question ([protocol.md](protocol.md#delivery-by-deletion)).
The architect's mail and the watchdog's is acknowledged by the one it is for, with `ao mail ack`.*

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
  --args '["-C","/path/to/project","mcp","serve","--role","implementer"]'
```

Other clients take the same shape; `ao mcp` with no arguments prints the JSON, and
`ao mcp config --role <role>` prints it with the role the harness holds in the project.

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

Urgent checks use the same recipient filtering as the inbox. A note to a reviewer, including
its normalized model-based address, is not implementer mail: it stays outside `ao_inbox`,
`ao_ack` and the implementer's commit checks, even when marked urgent. It remains visible
when a person reads all recipients' urgent mail (TRIAGE-3-LOW).

Only messages marked `## URGENT` or `## STOP` travel this way - or `## ACİL` and `## DUR`,
their Turkish forms, read in every project ([protocol.md](protocol.md#markers)). Routine
coordination waits for the turn-start inbox check, because a channel that carries everything is
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

A credential is the one thing that file does not hold. A product repository tracks
`.ao/`, so a token written into it goes into the history with the next commit that
takes the file. A header's value names the variable that holds it, `${NAME}`, read
from the environment the MCP client starts the bridge with. A header whose name says
it carries a credential - one of its words is `auth`, `authorization`, `key`, `apikey`,
`token`, `secret`, `password`, `cookie`, `session`, `credential` or `bearer`, so
`X-Api-Key` does and `X-Monkey` does not - and whose value is written out in the
project's file is refused, not sent, and so is a header that names a variable not set.
A credential of fewer than eight characters is sent nowhere - nor is a value a variable
holds, in any header, since what a variable holds is taken for a credential, nor a cookie
any of whose values is that short - since an answer that echoed one could not be told
from ordinary text to be hidden. The machine's `~/.ao/a2a-agents.json`, outside every repository, may hold one; it
is read where the project has no file of its own. A variable the project's file names
is sent only to an origin - scheme, host and port - that the machine's file sends the
same variable to: an agent can write `.ao/a2a-agents.json`, and one that kept the header
and named its own host had the bridge send the token there. So an agent that needs a
credential is registered in `~/.ao/a2a-agents.json` with its URL and header, and the
project's file may name it too. What an agent answers is its own text: a credential the
bridge sent, should an agent echo it in an error, a task or its card, is answered as
`[credential]`, to the model and in the notice the bridge records - as it was sent, a URL's
user, password and query values among them, since an agent's URL is answered by its origin
alone, scheme, host and port - and each of
its parts, the token after `Bearer` and each value of a `Cookie`. An agent the person gave a
credential to can still pass it on encoded; that agent is trusted with it, as the threat
model says ([threat-model.md](threat-model.md)). In `.ao/a2a-agents.json`:

```json
{"weather": {"url": "https://agent.example.com/a2a/v1",
             "headers": {"Authorization": "Bearer ${WEATHER_A2A_TOKEN}"}}}
```

*In ao since slice LEADS-SECRETS: the file held an agent's `Authorization` header as
written, and `ao a2a-mcp config` showed a token written into it; nothing kept it out
of a commit in a project whose `.gitignore` does not cover the file. Leaving the file
out of git with `ao init` was the other way, and it would have kept the token in the
working tree, where every agent in the checkout reads, and the agents' addresses out
of the repository a team shares. `tests/test_leads_secrets.py`.*

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

*In ao since slice A2A-HARDEN: the bug hunter read both and named five faults, each now held by
`tests/test_a2a_harden.py`. The server answers only a request named for its own address - `127.0.0.1`
or `localhost` with its port - as `ao watch --web` does, so a page from another site that points its
own name at 127.0.0.1 reads no board through a person's browser; each connection has a thread and 30
seconds to send its request, where one client that stalled held every other; a body past 1 MiB, or a
length that is none, is refused unread; and a request that is no JSON object, or whose params are
none, is answered as invalid instead of ending with no answer. The bridge follows no redirect, which
carried an agent's `Authorization` header to wherever it pointed - an agent that moved is written anew
in `.ao/a2a-agents.json`; a message is sent in the other dialect only when the first said it does not
take the method, keeping its `messageId`, since after a timeout it is not known whether the agent took
it; and a line on its standard input that is no JSON object is answered as invalid, where it ended the
bridge.*

## Registration is part of `ao init`

`ao init` registers the server for every agent it detects — `.mcp.json` for
Claude Code, `.kiro/settings/mcp.json` for Kiro, a snippet for Codex's
user-level config — merged into whatever those files already hold, each naming
the role its readers hold in the project where they hold one ([Roles](#roles)), and writes
the playbook (`ao skill install`) into files ao owns — never into CLAUDE.md or
AGENTS.md unless asked with `--rules`; those are the owner's rule files, and a
tool appending instructions there is a tool issuing rules nobody authorised. The one step it cannot do is the
restart: the human starts or restarts the app in the directory so the tools
load, then runs `ao doctor`. `--no-mcp` skips the registration.
