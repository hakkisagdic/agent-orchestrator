# Adapters

An adapter is a small JSON file describing how to talk to one agent CLI. Everything
provider-specific lives here; the orchestrator core knows nothing about any vendor.

## The five capabilities

| Capability | Question it answers | Required? |
|---|---|---|
| `send` | How do I run one prompt non-interactively? | yes |
| `resume` | How do I continue a specific existing session? | yes for driving |
| `transcript` | Where does this CLI store the conversation, and how is it parsed? | yes for observation |
| `busy` | Is this session writing right now? | yes for safe injection |
| `directives` | Where do always-included instructions and hooks live? | optional |

An adapter with only `send` is still useful — you lose observation and safe injection,
not the protocol.

## A reviewer is composed from its adapter

A reviewer must not be able to write, so every adapter declares `options.trust_none`: the
flags that leave the harness only reading, or `null` with `trust_none_why`, which makes it
**ineligible for the reviewer role** rather than silently unsafe. Today `kiro` and
`claude-code` declare how to deny tools; every other adapter says why it cannot, or why
nobody has verified that it can.

```bash
ao role set reviewer claude-code --model claude-opus-5
ao role set reviewer kiro --model <model> --effort high
```

`ao role set reviewer <adapter>` composes the invocation from the adapter - `send.argv`, then
`options.model`, `options.effort` (refused when the adapter does not take that value), then
`trust_none` - so a pair of harnesses is chosen by naming two adapters and a model each, not by
writing argv by hand. A hand-written reviewer argv still works and stays the exception.
`ao adapters` shows which adapters may review, and `ao doctor` reports a configured reviewer
whose adapter cannot deny tools.

## A reviewer can be a tool ao runs

A reviewer that runs the architect's binary on the architect's quota window stops when that window
closes, and a stand-in session's answer is not evidence ao produced (#86). A **tool reviewer** is a
program ao runs itself over the staged candidate, on a provider account of its own. Its adapter
declares `"kind": "tool-reviewer"`, `options.trust_none: []` - nothing is left to deny where ao runs
it - and a `review` contract, which ao reads from the package's adapters only:

| Field | What it declares |
|---|---|
| `review` → `candidate` | `diff-file`: ao writes the exact candidate diff, the bytes `diff_digest` names, into the reviewer's own directory outside any repository and fills `{diff_file}` with its path |
| `review` → `answer` | `{from: output, after_prompt}`: the answer is read from the file ao names in `{output}`; with `after_prompt`, only what follows the tool's echo of this exact prompt and that marker line |
| `review` → `environment` | `remove` and `keep`: patterns, matched without regard to case, for inherited variables the tool would read as settings and the ones it keeps; `set`: the variables that pin it, with `{model}` and `{timeout}` |
| `review` → `encoding` | the encoding the tool reads the diff in; a candidate in another is refused before the tool starts |
| `review` → `install`, `limits` | what would install it, named when it is absent; what ao cannot know about its answer, written into the evidence |

```bash
ao role set reviewer pr-agent --model <provider/model> --family <family>
```

A tool reaches many models and families, so its route names both: the model, which the contract
pins, and the family, which a person names because ao never infers one from a model name. A tool
route with no family may not review. The question is ao's own review prompt, candidate included,
so the verdict rule and the count schema are every other reviewer's. The evidence adds `adapter`
and `model` to `reviewer`, and a `tool` block with the tool's version, the digest and size of the
bytes handed and the adapter's limits; a review whose handed bytes are not the candidate diff is
INVALID. A tool that is not installed makes the review UNAVAILABLE, naming what would install it,
and `ao doctor` lists it among the optional capabilities. A tool route spends no actor's window:
ao asks keyflip about no provider for it, and never runs the architect's route.

`pr-agent` is the first, and untested until the owner proves it on a local candidate: plain-diff
mode (`--diff-file`) reads the candidate with no pull request and no platform token and publishes
nowhere, and `ask` carries ao's prompt. A capability-matrix project cannot bind a tool reviewer yet.

## Setting ao up for a harness is declared, not coded

`ao init`, `ao skill` and `ao remove` name no harness (#76). What a harness leaves in a
repository and where ao's files for it go are fields of its adapter, and core reads them:

| Field | What it declares | Used by |
|---|---|---|
| `detect.dirs`, `detect.files`, `detect.binaries` | the signs that a repository uses this harness | `ao init`, `ao skill`, `ao content add` |
| `directives.playbook` | `{path, header}`: where the playbook is rendered; `header` is text or `frontmatter` | `ao init`, `ao skill` |
| `directives.coordination` | a steering file ao writes the coordination rules into | `ao init` |
| `directives.rule_files` | the owner's rule files this harness reads (AGENTS.md is shared and needs no entry) | `--rules`, `ao doctor` |
| `directives.steering_dir`, `directives.skills_dir` | directories the harness reads by itself | `ao doctor`, `ao content add` |
| `directives.ao_files` | every path ao may have written for this harness | `ao remove` |
| `mcp` | `{file, key, extra, register, remove_when_empty}` or `{manual, snippet}`; a snippet's `{exe}` and `{root}` are written escaped for a double-quoted TOML string | `ao init`, `ao remove` |

A harness with no such fields is simply not set up: ao writes `.ao/PLAYBOOK.md` and prints the
pointer, and `--agent` refuses a name no adapter answers to. A project can declare a harness ao
has never shipped in `.ao/adapters/`, and init sets it up the same way.

## Where a harness keeps its sessions is declared, not coded

Session discovery, the implementer's transcript path and the architect's newest session all
read an adapter's `sessions` store (#76):

| `sessions.kind` | Fields | How ao reads it |
|---|---|---|
| `workspace-meta` | `dir`, `meta`, `transcript`, `workspaces`, `title`, `status` | `dir/<workspace>/<session>/meta` names the workspace paths; the newest `transcript` wins |
| `escaped-cwd` | `dir`, `transcript` (with `{session}`) | the working directory with `/` and `.` made dashes (every other character too on Windows) is a directory under `dir` |

`directives.command_hooks` (`{format: "pre-tool-use", shell_tool, project_dir_env, files}`) names
the settings whose hooks can rewrite an agent's shell commands, which `ao doctor` reports as a
measurement filter (#51) and asks what each does to the commands an agent measures with (#52,
[gates.md](gates.md#a-filters-exclusions-are-proved-not-trusted)). `shell_tool` is the tool name a
hook's matcher is tested against, and `project_dir_env` the variable the harness sets to the
project directory for a hook. A file under `~` is user-level; ao runs no hook that a file inside
the project names.

What decides authority reads the **package's** adapters only: which directories are a harness's
(`detect.dirs`, left out of review scope and product-dirty checks) and which stores are scanned. A
user's or a project's adapter layer is writable by the agents it describes, so an adapter there
can add a harness but cannot move a product path out of review by calling it a harness directory.

## Which processes are agents is declared, not coded

`detect.processes` names the program an agent turn runs as. ao counts those processes as writers
(`ao hold`, the nudge's one-turn rule) and treats an adapter's id, binaries and processes as one
agent's names when it asks whether the architect is already at the keyboard (#76). Like the
directories, these are read from the package's adapters only.

`options.mcp_isolation` (`{required, forbidden}`) is the flags a harness needs so a reviewer starts
no MCP server it was not given (#24); `ao doctor` and the reviewer check read it by binary.

## Accounts, windows and installs are declared, not coded

| Field | What it declares | Used by |
|---|---|---|
| `billing.api.driver` | a protocol in `src/ao/drivers.py` (`usage-limits`) with its `token`, `profile`, `body`, `resource` and `login` | `ao credits`, the credit sampler, `ao digest`, handoff |
| `billing.fallback.transcripts` | a glob of transcripts whose usage records are read when the account cannot be | `ao credits --offline` |
| `quota` → `provider` | the keyflip provider an actor running this harness spends | window reserve, rotation (#32), `ao fanout` |
| `detect.install_dirs` | where the harness installs itself outside the usual directories | the newest-binary search |
| `detect.update` | the command that updates the harness | `ao doctor` on a stale binary |

A driver is chosen by name and reads every path, key, command and endpoint from the adapter, so
adding a harness whose account answers the same protocol is a data change (#76).

## Profiles and actor names are declared, not coded

`ao init --profile` offers the presets in [`adapters/profiles.json`](../src/ao/adapters/profiles.json):
which adapter holds each role. The blocks are composed from those adapters (#76) — the implementer's
name from `actor_name`, the reviewer's argv from `send`, `models.review` and `options.trust_none`
with its `family`, the architect's from `resume` with `options.allowed_tools` narrowed to what an
architect runs. An implementer with no `implementer.name` is named by its adapter's `actor_name`,
and a project with no implementer block by the default profile's, so its mail keeps the names it
was written under.

`tests/test_harness_guard.py` fails when a harness's id, command, process, directory or rule file
appears in a core module's code. Docstrings may name one; code reads an adapter instead.

## Shipping an adapter without forking

Adapters load from three places, each overriding the one before by `id`: the package, then
`~/.ao/adapters/`, then the project's `.ao/adapters/`. Supporting a new harness is a JSON file,
not a change to ao and a wait for a release.

```bash
ao adapters                          # each adapter, its source, contract and verification
ao adapters validate my-harness.json # what a candidate is missing, before anyone relies on it
ao adapters conform my-harness       # run send and resume through a fixture harness
```

An adapter declares `"contract": 1`, the adapter contract this ao implements. One declaring
another contract is listed as refused, with both versions named, and never loaded half-way.
`validate` checks the fields every adapter needs - `id`, `name`, `verified`, `contract`, and a
`send.argv` carrying `{prompt}` in exactly one argument - and that `resume.argv` carries
`{session}` and uses only placeholders ao fills. `conform` runs `send` and `resume` with the
harness binary replaced by a fixture that records what it was given, and passes only when the
prompt, with spaces, quotes, `=` signs and a line break, arrives whole in one argument and,
where `resume` takes a session id, the session arrives too; `transcript`, `busy` and `directives` are reported as declared or absent.
ao's own tests run every shipped adapter through the same conformance, so a change in ao that
would break a third party's adapter breaks ours first.

## Support matrix

One row per vendor in [`adapters/vendors.json`](../src/ao/adapters/vendors.json), the canonical list every
surface derives from (#89); a test fails when this table, the list and the shipped adapters disagree.

`full` = every capability verified against a running install. `partial` = the command surface or the store
verified, not both. `documented` and `untested` = written from the tool's own documentation and not run here;
the adapter says so in its `disclaimer`. **Reviewer** is whether the adapter can run without write tools
(`options.trust_none`, #88); an ineligible one says why.

| Vendor | Adapter | Verified | Reviewer | Note |
|---|---|---|---|---|
| `claude` | `claude-code` | full | eligible | Claude Code |
| `codex` | `codex` | untested | ineligible | OpenAI Codex CLI |
| `opencode` | `opencode` | partial | ineligible | opencode |
| `traycer` | — | — | — | no adapter: an orchestrator of agents, as ao is, not an agent ao drives |
| `cursor` | `cursor-agent` | documented | ineligible | Cursor Agent CLI |
| `grok` | `grok` | untested | ineligible | Grok CLI (superagent-ai) |
| `qwen` | `qwen` | untested | eligible | Qwen Code |
| `kiro` | `kiro` | full | eligible | Kiro CLI |
| `droid` | `droid` | untested | ineligible | Factory Droid |
| `kimi` | `kimi` | untested | ineligible | Kimi Code CLI |
| `copilot` | `copilot` | untested | ineligible | GitHub Copilot CLI |
| `kilocode` | `kilocode` | untested | eligible | Kilo Code CLI |
| `openrouter` | — | — | — | no adapter: a model router reached through a harness that takes provider/model, such as opencode or kilocode; not a harness itself |
| `amp` | `amp` | untested | ineligible | Amp (Sourcegraph) |
| `devin` | — | — | — | no adapter: a hosted agent with no local command line; the pull requests it opens are observed through cloud-generic |
| `pi` | `pi` | untested | eligible | pi coding agent |
| `hermes` | `hermes` | untested | eligible | Hermes Agent (Nous Research) |
| `omp` | `omp` | untested | eligible | oh-my-pi |
| `huggingface` | — | — | — | no adapter: a model hub and inference provider reached through a harness, not a harness |
| `reasonix` | `reasonix` | untested | eligible | Reasonix |
| `antigravity` | `antigravity` | full | ineligible | Antigravity CLI (agy) |
| `aider` | `aider` | untested | ineligible | Aider |
| `amazon-q` | `amazon-q` | untested | ineligible | Amazon Q Developer CLI |
| `command-code` | `command-code` | partial | ineligible | Command Code (cmd) |
| `deepseek` | `deepseek` | untested | ineligible | DeepSeek harness / CLI |
| `gemini` | `gemini` | untested | ineligible | Google Gemini CLI |
| `ollama` | `ollama` | untested | ineligible | Ollama (local models) |
| `qoder` | `qoder` | untested | ineligible | Qoder CLI |
| `trae` | `trae` | untested | ineligible | Trae Agent (ByteDance) |
| `cloud` | `cloud-generic` | partial | ineligible | Generic cloud agent (pull-request delivered) |
| `pr-agent` | `pr-agent` | untested | eligible | PR-Agent, a tool reviewer ao runs over the candidate (#86) |

The first twenty-one rows are Traycer's canonical enum, the coverage this list is measured
against; then the harnesses ao shipped before it, and last a tool reviewer. Moving a row to `full` is the most valuable
contribution this project can take. See [`adapters/README.md`](../src/ao/adapters/README.md).

## Two observation modes

Not every CLI keeps a transcript you can read, and that turns out not to matter.

**Passive store** — the vendor writes a session file; the orchestrator reads it read-only.
Kiro and Claude Code work this way. You see *every* turn, including ones a human started
in the IDE. You pay for it by reverse-engineering a private format that can change.

**Call-return** — the CLI returns a complete structured record for each turn, and the
orchestrator persists that record into its own event log. Antigravity works this way, and
so do most CLIs in print mode with `--output-format json`. Verified example:

```json
{"conversation_id":"a0a7445c-…","status":"SUCCESS","response":"…",
 "duration_seconds":18.8,"num_turns":1,
 "usage":{"input_tokens":21140,"output_tokens":1489,"thinking_tokens":1258,
          "cache_read_tokens":0,"total_tokens":22629}}
```

This mode is *better* in every respect except one: you only see turns you started. If a
human drives the same tool from its own UI, you are blind to that work. Choose passive
observation when a human shares the session, call-return when the orchestrator owns it.

Call-return also removes the two-writer hazard rather than mitigating it: print mode is
synchronous, so the orchestrator knows precisely when a turn is in flight and takes an
in-process lock per session instead of guessing from file timestamps.

## Transcript shapes

Three shapes cover everything seen so far:

- **JSONL per session** (Kiro, Claude Code) — one record per line, appended. Cheap to
  tail: seek to the last N bytes, drop the first partial line, parse the rest. The
  orchestrator never reads the whole file; production transcripts reach tens of MB.
- **SQLite** (opencode) — query, never write. Open read-only; a second writer corrupts
  the agent's own state.
- **Call-return** — no file at all; the record arrives as JSON on stdout and the
  orchestrator owns persistence. Prefer this when available.
- **Opaque / none** — observation unavailable. `send` and `resume` still work; the
  dashboard degrades to repo and mailbox signals only.

### What a record looks like is declared, not coded

A JSONL transcript's records are read through what the implementer's adapter declares about
them (#76): the status panel's context, cost, messages and failed tool calls, `ao cost`, the
watchdog's "has the turn ended" and foreign-edit checks, and the offline credit estimate. A
reader asked without an adapter reads what the implementer's adapter declares, and a part an
adapter does not declare is read as nothing, never as another harness's field.

| Field | What it declares | Used by |
|---|---|---|
| `transcript.record.kind` | the path to a record's kind (`payload.type`, `type`); every field below except `time` is read from the object holding the kind | every reader |
| `transcript.record.time` | the path to a record's ISO timestamp, from the record itself | every reader |
| `transcript.record.text_keys` | the keys whose string values are a message's text, at any depth; a dotted path is read by its last key | the panel's messages, `ao tail` |
| `transcript.messages` | `{prompt, reply}`: the kinds of the owner's prompt and of the agent's reply | the panel's messages, `ao tail`, `ao cost` |
| `transcript.turn` | `{start, end, bookkeeping}`: the kinds that open and close a turn, and those that may follow its end without meaning a turn is running | `ao cost`, the credit estimate, the watchdog's reap and idle answer |
| `transcript.tool_call` | `{type, name, args, path_keys, write_tools, write_words}`: a tool call's kind, the paths to its name and arguments, the arguments naming a file, and the tools that write one - by name, or by a word their name holds in any case | `ao cost`, foreign edits |
| `telemetry.context` | `{from: "transcript", type, match, field}`: the record and the path of the context percentage | the panel, `ao_status` |
| `telemetry.cost` | `{from: "transcript", type, field, tools, unit}`: the usage record, the path to its value - or `fields`, several paths that add up - and the path to the tools each entry used | the panel, `ao cost`, the credit estimate |
| `telemetry.failure` | `{from: "transcript", type, field, failed_when, text}`: the verdict on a tool result, the value that means it failed, and the path to its output | the panel's problems |
| `billing.fallback.reading` | how usage records add up to spend: `sum` when every record is the whole cost of the turn it reports, `peak-per-turn` when a record is the running total of the turn in progress and a turn costs the highest total it reached, `per-response` when a response is written as several records that each repeat its usage and each response counts once, by the path to its id (`telemetry.cost.response`). The panel and `ao cost` add usage up by it too, and add records up when none is declared; the estimate reads only a declared reading, and under a reading ao does not implement no reader reads usage | the panel, `ao cost`, `ao credits --offline`, `ao digest` |

A path steps into objects with dots (`value.usagePercentage`), and `[]` steps into each element
of a list (`promptTurnSummaries[].usage`), one entry per element. A turn opens at a `start` kind,
or at a `prompt` when no turn is open or the open one ended; a `start` that follows the prompt of
a turn not yet started is that turn's start, not a second turn. `tests/test_transcript_shape.py`
reads a harness ao never shipped, declared only in a project's adapter layer, and fails when a
core module names a kind, a field or a tool of a shipped adapter's shape.

A shipped declaration says what a harness writes, measured rather than believed. Kiro's turns,
usage, writes and turn ends were measured on one machine's store, July to September 2026. It
writes a turn's prompt and then its start. Each `usage_summary` carries the whole cost of the one
turn it closes, so it declares `sum`: the `peak-per-turn` reading it declared before took a drop
between two records for a new turn and read 79% of what the records add up to. Its tools that
replace text in, append to, delete or move a file are writes, and a `session_start` or a
`tombstone` may follow the end of its last turn. `tests/test_transcript_readings.py` holds each of
these readings.

### A store that nests its records

Kiro writes each tool call, tool result and turn end as a record of its own. Claude Code's session
store writes none of them: a response is one `assistant` record per content block, each repeating
the response's `message.id`, `message.stop_reason` and `message.usage`; a tool call is a `tool_use`
block of the response, its result a `tool_result` block of a `user` record, and no record closes a
turn. Beside the fields above, a store like it declares:

| Field | What it declares | Used by |
|---|---|---|
| `transcript.tool_call.blocks`, `match` | the path to the blocks a record of the tool-call kind holds (`message.content[]`), and the values a block holds to be a call; `name` and `args` are then read from each such block | `ao cost`, foreign edits, the panel's tool calls |
| `telemetry.failure.blocks`, `match` | the same for tool results; `field`, `failed_when` and `text` are read from each such block | the panel's problems |
| `transcript.turn.end_when` | `{type, field, values}`, or a list of them: a record of that kind whose field holds one of the values ends the turn it falls in, as an `end` kind does, under the one turn rule every reader applies | `ao cost`, the panel, the credit estimate, the watchdog's reap and idle answer |
| `transcript.turn.conversation` | the kinds a turn is made of; every other kind is bookkeeping that may follow a turn's end, so a kind a later release adds does not read as a running turn | the watchdog's reap and idle answer |
| `telemetry.cost.response` | the path to a response's id, which the `per-response` reading counts each response once by; under that reading without it, no usage is read | the panel, `ao cost`, `ao_status` |

```jsonc
// transcript
"turn": { "end": ["result"], "conversation": ["user", "assistant"],
          "end_when": { "type": "assistant", "field": "message.stop_reason",
                        "values": ["end_turn", "stop_sequence"] } },
"tool_call": { "type": "assistant", "blocks": "message.content[]", "match": { "type": "tool_use" },
               "name": "name", "args": "input", "path_keys": ["file_path", "notebook_path"],
               "write_words": ["write", "edit"] },
// telemetry
"context": { "from": "none" },
"cost": { "from": "transcript", "type": "assistant", "response": "message.id", "unit": "token",
          "fields": ["message.usage.input_tokens", "message.usage.cache_creation_input_tokens",
                     "message.usage.cache_read_input_tokens", "message.usage.output_tokens"] },
"failure": { "from": "transcript", "type": "user", "blocks": "message.content[]",
             "match": { "type": "tool_result" }, "field": "is_error", "failed_when": true, "text": "content" },
// billing: no transcripts, so the credit estimate reads none of it
"fallback": { "reading": "per-response" }
```

It was measured before it was declared, over 421 local session stores of release 2.1. Every record
of one response carried the same usage and the same stop reason (83,779 responses, 51,865 of them
written as several records), so adding the records up counted about twice the tokens. A parallel
call's result may sit between two records of its response (4,799 responses); none of the 183 stores
of print-mode runs (`-p`, the way ao starts an implementer) had a response whose records spanned two
turns, or a record written twice, so a response is counted once in its turn. No response that ended
a turn held a tool call. A store at rest ended with an `end_turn` response, or with a
`stop_sequence` one - a reply the harness writes itself, an API error among them - in 375 of them,
followed only by kinds such as titles, prompt queues, hook summaries and attachments. The stores
hold twenty kinds besides `user` and `assistant`, which is why the turn names its conversation
rather than its bookkeeping. Every failed tool result carried `is_error: true`, and every `user`
record held one result.

The unit is the token, and every token a response read or wrote counts once: uncached input, input
written to and read from the prompt cache, and output. Server tool requests are counts of requests,
not tokens, and the usage breakdowns - by cache lifetime, thinking and iteration - are already
inside the totals, so neither is added. A token count is not a price: cache reads are most of a long
session's tokens (91-98% measured) and are priced far below the rest. No record carries a context
percentage or the model's window, so no context is read. `tests/test_second_harness_cost.py` reads
the nesting from synthetic records, and fails when a core module names a field or a value it
declares.

## Busy detection

Two signals, both cheap, used together:

1. Session metadata status field, where the CLI exposes one.
2. Age of the last write to the transcript file.

A session counts as safe to inject into only when the status is not running **and** the
last write is older than the idle threshold (default 240s). This conservative AND is
deliberate: a false "idle" corrupts a session, a false "busy" only delays a nudge.

## Capabilities, not flags

The point of an adapter is that ao never learns which harness it is talking to. That only
holds if ao asks in **its own vocabulary** and the adapter answers in the harness's. Four rules
make that work, and each exists because a real harness broke the naive version:

**Ask for the capability; let the adapter spell it.** ao asks *run this with no tools*. One
harness spells that `--trust-tools=`, another `--allowedTools ""`, another a read-only sandbox
mode. ao must not know which. It reads `options.trust_none` and uses whatever is there.

**An absent capability is declared, not guessed.** `trae` has no documented tool-less mode, so
its `trust_none` is `null` with a reason, and its `roles` block says
`reviewer: ineligible: cannot be run without tools`. That is the honest outcome: the harness
is a fine implementer and cannot hold the reviewer role, and ao refuses the binding instead of
inventing a flag. Silence would mean guessing, and a reviewer that can write is not a reviewer.

**Values are mapped and clamped, never passed through.** Effort ladders differ — one harness
accepts `minimal|low|medium|high`, another `low|medium|high|xhigh|max`. ao asks for its own
level; the adapter declares what it supports in `effort_values`; the resolver picks the nearest
available and **records that it clamped**, because a run at `high` reported as `max` is a lie
about how hard the model tried.

**Shape differs, so declare the shape.** Some harnesses take one model id; `trae` takes a
provider *and* a model. A setting may arrive as a flag, an environment variable or a config
file key, and the adapter says which. Where the family matters — reviewer independence is a
family rule — it is read from what the adapter declares, never inferred from a model string.

The test that keeps this honest is the one in #88: compose an invocation for **every** shipped
adapter and assert none of them grants a tool. An adapter that cannot answer a capability fails
that test by declaring itself ineligible, which is a pass.

## Writing an adapter

```jsonc
{
  "id": "mytool",
  "verified": "partial",             // full | partial | untested
  "send":   { "argv": ["mytool", "--print", "{prompt}"] },
  "resume": { "argv": ["mytool", "--session", "{session}", "--print", "{prompt}"] },
  "transcript": {
    "kind": "jsonl",
    "path": "~/.mytool/sessions/{session}/messages.jsonl",
    "record": { "time": "timestamp", "kind": "payload.type", "text_keys": ["content"] },
    "messages": { "prompt": ["user"], "reply": ["assistant"] },
    "turn": { "start": ["turn_start"], "end": ["turn_end"] }
  },
  "busy": { "meta": "~/.mytool/sessions/{session}/meta.json", "status_field": "state",
            "running_values": ["running"], "idle_seconds": 240 }
}
```

**Flag syntax is not cosmetic.** Some CLIs use Go-style flags where the value must be
attached: `agy --print='…'` works, `agy -p '…'` silently swallows the next argument as the
flag value and drops the prompt — producing a clean exit code, no output and no stored
turn. That failure is indistinguishable from an agent choosing to do nothing, so encode
the exact form in the adapter and never improvise it.

Commands are **argv arrays, never shell strings**. There is no string for a prompt to
inject into, and prompts routinely contain quotes, newlines and backticks.
