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

## A prompt past one argument reaches its command another way

A review prompt carries a diff of up to 400 KB and the context it is judged against, and it went to the
reviewer as one argument. Linux refuses one argument over 131,072 bytes and Windows a command line over
32,767 characters, and Linux and macOS bound the arguments and the environment together: past that the
reviewer could not start, and the spawn error read as a reviewer that was unavailable. An adapter declares
how else its CLI takes a prompt, under `send` and under `resume`:

| Field | What it declares |
|---|---|
| `send` → `stdin`, `resume` → `stdin` | `{replaces, with, note}`: the arguments of that capability's `argv` that carry `{prompt}`, and the arguments that stand in their place when the prompt is on standard input - none, or a marker such as `-` |
| `send` → `file`, `resume` → `file` | the same, with `with` naming the prompt's file in `{prompt_file}` exactly once |

```jsonc
"send": { "argv": ["mytool", "exec", "{prompt}"],
          "stdin": { "replaces": ["{prompt}"], "with": ["-"],
                     "note": "what was verified, where, and when" } }
```

While a command fits with room to spare - the prompt's argument, the other arguments, the environment and a
margin for a launcher that starts the real program - the prompt stays in its argument and nothing changes.
Past that ao uses what the adapter declares for the command: `resume`'s channels for one that carries
`{session}`, `send`'s for any other, standard input before a file. The prompt's bytes are written to a file
of mode 0600 in a directory of its own, never inside the repository, handed over as standard input or by
its path, and removed when the process ends. Every prompt ao hands an agent's CLI goes this way: a
reviewer's, the bug hunter's, and those of the watchdog's nudges and architect wakes. A watchdog turn runs
detached and nothing waits to remove a file after it, so it takes standard input or nothing. A review
prompt grows with it: while every reviewer route that may run declares a channel or fits here, a waived
range's commit messages and a candidate's context take `review.context_bytes` beside the diff instead of
what one argument leaves ([configuration.md](configuration.md)).

With nothing declared nothing starts, and the refusal names the prompt's size, what the platform carries
and that the adapter declares no other channel. `ao review` exits 2 with it, as a configuration error: it
writes no review file and is neither UNAVAILABLE nor a verdict, so `ao catchup` keeps the waiver open. In
a chain, a route that cannot take the prompt is passed over as a configuration error and the next route is
tried; the review is refused only when none can take it. Like a review contract, channels are read from the
package's adapters only: a layer an agent can write must not choose the arguments a reviewer runs with.

A channel is declared only where the CLI's documentation or its `--help` shows it, and its `note` says
where. `claude-code`, `codex`, `command-code`, `gemini` and `omp` take a prompt on standard input for both
capabilities, and `amp`, `copilot` and `qwen` for `send`. `hermes` takes one for `resume`, through
`hermes chat --query-file`. `aider` takes a file for both, and `droid` standard input or a file for `send`
and a file for `resume`. Every other shipped adapter takes its prompt in its argument alone, and
`ao adapters validate` checks that each channel replaces arguments its capability's `argv` carries once.

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

`detect.headless` names the arguments that make a process of that harness a turn started without a
person: a flag, alone or with its value attached after `=` (`--print=…`), or a subcommand (`exec`,
`run`). `ao hold` and the watchdog's reap stop only such turns, `ao writers` labels them, and an
architect process holding one is not a person at the keyboard. An argument counts only for the
harness whose names the command line runs as - hermes selects a profile with the `-p` that makes
other harnesses print one answer - and only the package's adapters are read, so a layer an agent can
write cannot turn a person's session into an unattended one; a command line no shipped adapter
answers to is never taken for one. Every shipped adapter declares its list, and every command its
`send` and `resume` run holds one of them; an adapter that starts no local turn declares `[]`.

`options.mcp_isolation` (`{required, forbidden}`) is the flags a harness needs so a reviewer starts
no MCP server it was not given (#24); `ao doctor` and the reviewer check read it by binary.

## Accounts, windows and installs are declared, not coded

| Field | What it declares | Used by |
|---|---|---|
| `billing.api.driver` | a protocol in `src/ao/drivers.py` (`usage-limits`) with its `token`, `profile`, `body`, `resource` and `login` | `ao credits`, `ao cost`, the credit sampler and the samples `ao doctor` reads, `ao digest`, handoff: each for the implementer's own adapter |
| `billing.fallback.transcripts` | a glob of transcripts whose usage records are read when the account cannot be | `ao credits --offline`, `ao digest`, for the implementer's own adapter |
| `quota` → `provider` | the keyflip provider an actor running this harness spends | window reserve, rotation (#32), `ao fanout` |
| `detect.install_dirs` | where the harness installs itself outside the usual directories | the newest-binary search |
| `detect.update` | the command that updates the harness | `ao doctor` on a stale binary |

A driver is chosen by name and reads every path, key, command and endpoint from the adapter, so
adding a harness whose account answers the same protocol is a data change (#76). A lookup is asked
only for the adapter the reading is about, the implementer's, and only as the package declares it:
a lookup runs the command its adapter names, so what a layer an agent can write declares is never
asked, and there is no first adapter to fall back on. An implementer whose adapter declares none has
no account ao can read, no credit samples and no credits alarm; every sample names the adapter it
was read through ([telemetry.md](telemetry.md), "Whose account").

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
| `billing.fallback.reading` | how usage records add up to spend: `sum` when every record is the whole cost of the turn it reports, `peak-per-turn` when a record is the running total of the turn in progress and a turn costs the highest total it reached, `per-response` when a response is written as several records that each carry its usage so far and each response counts once in a reading, at the highest usage its records reached, whichever turn or transcript holds a copy, by the path to its id (`telemetry.cost.response`). The panel and `ao cost` add usage up by it too, and add records up when none is declared; the estimate reads only a declared reading, and under a reading ao does not implement no reader reads usage | the panel, `ao cost`, `ao credits --offline`, `ao digest` |

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
| `transcript.messages.blocks`, `match` | the path to the blocks a prompt or a reply holds, and the values a block holds to be its words; a record holding blocks of which none match - a tool's result, a tool call - is no message, and one whose content is not blocks is read whole | the panel's messages, `ao tail` |
| `telemetry.cost.response` | the path to a response's id, by which the `per-response` reading counts each response once in a reading, at its highest record: across every turn of a transcript, every subagent transcript read with it, and every transcript the credit estimate adds up; under that reading without it, no usage is read | the panel, `ao cost`, `ao_status`, the credit estimate |

```jsonc
// transcript
"messages": { "prompt": ["user"], "reply": ["assistant"], "blocks": "message.content[]", "match": { "type": "text" } },
"turn": { "end": ["result"], "conversation": ["user", "assistant"],
          "end_when": { "type": "assistant", "field": "message.stop_reason",
                        "values": ["end_turn", "stop_sequence", "refusal"] } },
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
turns, or a record written twice. No response that ended a turn held a tool call. A store at rest
ended with an `end_turn` response, or with a `stop_sequence` one - a reply the harness writes
itself, an API error among them - in 375 of them, followed only by kinds such as titles, prompt
queues, hook summaries and attachments. The stores hold twenty kinds besides `user` and `assistant`,
which is why the turn names its conversation rather than its bookkeeping. Every failed tool result
carried `is_error: true`, and every `user` record held one result.

The readings that followed were measured on the same stores before they changed, and the readers
compared before and after on the 419 at rest. A Stop hook runs after an `end_turn` response and
writes nothing when it starts: its `stop_hook_summary` system record comes when it ends - 1.7 s
later at the median, 13 s at the 99th percentile, 278 s at most - after 3,764 of the 4,760
`end_turn` responses in the 172 stores that hold one, and after no other stop reason. No hook went
on with a turn: a prompt, a meta record or nothing came after every summary. So a turn has ended at
its response while its hook runs; waiting for a summary would leave one turn in five running for
ever, and the watchdog's reap still waits its idle window of silence. A `refusal` ended its turn
both times one was written, and a prompt followed; `max_tokens` never did: another response of the
same turn followed all five. Of the panel's last eight messages per store, 1,468 of 2,548 were tool
results shown as the person's words and 164 were tool calls shown as the agent's (542 of 794 in
print-mode stores), so a message's words are its `text` blocks. 89 responses were written again
turns after the first, 83 with the same record ids and times, and 10,145 appear in more than one
store, copied into a resumed session's transcript: desktop-app stores read 0.2% more tokens than
once per response, and the stores added up transcript by transcript read 34,107,386,097 tokens
against 27,529,044,146, so a response is counted once in a reading. A `Read` names its file by the
same `file_path` an `Edit` does, and foreign-edit detection took every call naming a file for a
write: 4,503 paths in the stores' last 3 MB, of which 3,007 were written (8 of 466 in print-mode
stores). Kiro's `read_file`, `readFile`, `read_files` and `list_directory` name a `path` too, and
146 of its 152 were only read or listed. A write is now what `write_tools` and `write_words`
declare, as `ao cost` reads it; every other kiro reading over its 17 stores is unchanged.

The unit is the token, and every token a response read or wrote counts once: uncached input, input
written to and read from the prompt cache, and output. Server tool requests are counts of requests,
not tokens, and the usage breakdowns - by cache lifetime, thinking and iteration - are already
inside the totals, so neither is added. A token count is not a price: cache reads are most of a long
session's tokens (91-98% measured) and are priced far below the rest. No record carries a context
percentage or the model's window, so no context is read. `tests/test_second_harness_cost.py` reads
the nesting from synthetic records, and fails when a core module names a field or a value it
declares; `tests/test_harness_readings_2.py` holds each reading measured since, and the
declarations of `detect.headless`.

### Subagents, and records that are no one's words

A harness that delegates may write each subagent's records to a transcript of its own, beside the
session's rather than in it, and a store may write records of the prompt's kind that no one said.
A store like that declares:

| Field | What it declares | Used by |
|---|---|---|
| `transcript.subagents.dir` | the directory of a session's subagent transcripts, relative to the session transcript's own directory; `{session}` is the session transcript's name without its extension, and stands in every `dir`, so no session reads another's (below: where a subagent path may lead) | `ao cost`, the panel, `ao_status`, the credit estimate, foreign edits, and whether the implementer is working: its state in the panel and `ao_status`, the watchdog's idle guard, reap and spin check |
| `transcript.subagents.transcripts` | `[{path, named_by}]`: where a subagent's transcript is under that directory, `*` standing for any name and `{id}` for the value a record of the session holds at `named_by` when that record starts it - the result of the call that started it, naming it | the same |
| `transcript.subagents.sidecar` | `{path, call}`: the file beside a subagent's transcript, `{name}` standing for the transcript's name without its extension, whose `call` field holds the id of the tool call that started it, in the session's transcript or in another subagent's | `ao cost`, the panel, `ao_status`, the credit estimate |
| `transcript.tool_call.id` | the path to a tool call's id, read where its name is | the same |
| `transcript.messages.not_words` | a list of matches, paths with the values they hold: a prompt or a reply holding every value of one - a note the harness writes, the summary of a compacted conversation - has no words; it still opens the turn the model answers | the panel's messages, `ao tail` |

A subagent works for the turn that started it. Each reading reads a subagent's transcript right after
the record that starts it - whichever comes first of the call its sidecar names and the record that
names it - and charges the subagent's spend to the turn that record falls in, however many turns later
the subagent wrote. Its own prompts and turn ends move only its own turn, under the adapter's reading,
and a subagent it starts is charged to the same turn. The spend stays inside the turn's, so a total is
still the sum of its turns, and `ao cost` shows how much of each class's was delegated, the panel how
much of the total, and `ao_status` as `cost_delegated`. A subagent's tool calls, writes and commits are
its turn's, and every subagent transcript written within the foreign-edit window is the implementer's,
whichever turn started it. A subagent transcript that no record of a reading starts is not counted: its
spend is in no turn the reading holds. The panel reads a tail, and a subagent started before the tail
belongs to a turn outside it, as `ao cost` leaves out the turn already under way where a transcript's
records begin. A response written as several records costs the most its records reached.

```jsonc
// transcript, beside what the store above declares
"messages": { "not_words": [{ "isMeta": true }, { "isCompactSummary": true },
                            { "origin.kind": "task-notification" }] },
"tool_call": { "id": "id" },
"subagents": { "dir": "{session}/subagents",
               "transcripts": [{ "path": "agent-{id}.jsonl", "named_by": "toolUseResult.agentId" },
                               { "path": "workflows/{id}/agent-*.jsonl", "named_by": "toolUseResult.runId" }],
               "sidecar": { "path": "{name}.meta.json", "call": "toolUseId" } }
```

It was measured on the same machine's stores of release 2.1, read-only. A subagent writes its records to
`agent-<id>.jsonl` under its session's `subagents` directory, or to `workflows/<run>/agent-<id>.jsonl`
for the agents a workflow run starts, each with a `.meta.json` sidecar: 3,563 of them beside a session
transcript. The result of the call that started one names it at `toolUseResult.agentId`, whether the
agent ran to its end or went on in the background, and a workflow's result names its run at
`toolUseResult.runId`: 3,545 are joined that way. The sidecar of an agent a call started names the call
at `toolUseId`, and joins the 17 agents another subagent started, whose calls' results carry no
`toolUseResult`, and one agent whose result named nothing. None beside a session transcript is left
unjoined; 139 more sit in three directories whose session transcript is gone, where no reading looks.
The prompt id a subagent's records carry is not the link: 961 of the 3,268 agents workflow runs started
carry a later turn's, the prompt current when the run started them. No response is in both a subagent's
transcript and its session's, nor in two subagents', and the usage a call's result carries sits on a
`user` record no reading reads; its total matched the subagent's spend in none of 38 results. A
subagent's transcript writes a response's records as it streams them: 53,498 of the 66,125 responses
written as several records grew in output tokens from record to record, and reading their first records
missed 67,037,492 tokens. A session transcript repeats one usage in every record of a response, so
reading each response at its highest record moved no session's own tokens; the credit estimate reads
2,990,529 more, from three responses two transcripts hold with different usage.

The readers were compared before and after on the 419 stores at rest, and every store's own tokens and
turns read as before. The desktop app's 180 stores hold 2,908 subagent transcripts, all joined: `ao cost`
read 26,128,151,129 tokens before and 34,913,789,378 after, 8,785,638,249 of them delegated, over the
same 3,927 turns, and its tool calls went from 60,613 to 147,140 and its product writes from 2,006 to
5,743. The 183 print-mode stores hold one: 210,548,799 tokens before, 213,215,662 after, over the same
184 turns. The panel's 12 MB tails joined 1,918 transcripts, and its slowest reading took 545 ms of CPU
against 61. In the 59 stores that delegate the subagents spent a median 21% of the session's tokens, and
more than the session itself in 11; 177 of the 274 turns that started one spent more through it than in
their own records. Of the panel's last eight messages per store, 82 notes the harness wrote (`isMeta`),
3 compaction summaries and 54 task notifications were shown as the person's words, and none are now. Kiro
declares no subagents, and its readings over its 17 stores are unchanged: every figure, and every
reading's digest once the new `delegated` fields, all zero, are set aside. `tests/test_subagent_spend.py`
holds these readings from synthetic records, and fails when a core module names a field they declare.

A subagent at work is its implementer at work. A session waits for a subagent with its own transcript
quiet, or ends its turn while one works on in the background, so every reader that asks whether the
implementer is working reads the subagent transcripts too. The implementer's last write is the latest of
its session transcript's and theirs: its state in the panel and `ao_status` reads it, and so do the
watchdog's idle guard, its reap, and its check that the implementer is working in a secondary project. A
turn has not ended while a subagent transcript was written after the session transcript was: the session
writes a record of each subagent it takes back - the notification of its end, or the result of the call
that waited for it - and until it does, the watchdog treats the runtime waiting for the subagent as a
turn, reaped only after three idle windows of silence. What a subagent writes is the transcript growing
for the spin check, so one looping while nothing is produced is busy without progress, not busy. These
readers run every cycle: they list the directory and read each subagent transcript's modification time
and size, and none of them opens a subagent transcript or a sidecar. A subagent's own records could not
tell them it finished, since an agent a workflow run starts ends on the result of the call that returns
its output, not on a response that ends a turn.

A subagent's failed calls are not the implementer's errors. They are steps the subagent recovers from
inside, and what the implementer is told is how the subagent ended, in its own transcript: the result of
the call that waited for it, which the panel's problems read when it failed, or the notification of its
end, which they read when it failed or was stopped (below). And `not_words` is about words, not turns: a
task notification or a note the harness writes after a turn's end - another session's message among them -
asks the model to answer, and opens the turn its answer makes as a prompt does; a compaction summary
written while a turn runs stays inside that turn.

These were measured on the stores at rest, from each record's time. The 59 stores that delegate hold
2,916 subagent transcripts and 257,598 records in them. 77,402 of the records were written while the
session transcript had been silent for six minutes or more, the watchdog's idle window, in 40 of the
stores, and the session transcript alone read idle while a subagent had written within that window for
2,900 minutes, in 308 stretches of up to 85 minutes. 143,529 were written while the session's turn read
as ended, in 41 stores. Of the 444 closed turns during which a subagent wrote, it went on for 203 s after
the close at the median and 108 minutes at most, and in 179 the session had been silent for the idle
window while a subagent was still to write, which is when the watchdog reaped a runtime lingering for it.
Each of the 444 ended at a record of the session - a task notification in 251, 0.1 s after the last
subagent write at the median - and no subagent transcript at rest was written after its session
transcript. Read against the session transcript's last write, one of the 444 still reads as ended while
its subagent is silent for the idle window and then writes again, as a session's own long tool call
would; read against the time of the record that closed the turn, the same one does, and two stores at
rest would read as running for ever, their subagent transcripts modified 22 hours and 15 days after their
last records. A subagent's own last record closes its turn in 624 of the 2,916: in 194 of the 239 agents
a call started, and in 430 of the 2,677 a workflow run started, whose 2,187 more end on the result of the
call that returns their output.

In the same stores the sessions hold 1,011 failed results and their subagents 2,154, 1,858 of them shell
commands, more than the session's own in 34 of the 59. 1,116 of the 1,171 subagents that failed a call
returned a result after it, and their sessions were told of 737 as completed, 45 as stopped and 3 as
failed; 10 of the sessions' own failures were calls that started a subagent. Read beside the session's
own, the subagents' failures would take a line of the panel's two in 20 of the 58 stores that show one,
and both lines in 13. Of the 5,035 turns `ao cost` reads in the 419 stores, 1,477 open at a task
notification and 512 at a note, 295 of those another session's message, and 1,378 and 371 of them charge
the model's tokens. Were those records bookkeeping, it would read 4,645 turns with the same tokens: a
follow-up's first response would fall in the turn before it, and the tool result after that response
would open another. The 344 turns that charge nothing were opened by notes (141), notifications (99), a
person's prompts (102) and summaries (2) alike, and 328 of them hold only a reply the harness writes
itself.

The readers were compared before and after on the same stores at rest. Kiro declares no subagents, and
every reading of its 17 stores is unchanged: `ao cost`, the panel's figures, failures and messages, the
turn end, the busy state, the spin check's growth, foreign-edit writes and the credit estimate. On the
419 stores of the second harness, the turn end (373 ended), the busy state, `ao cost`, the panel's
figures, failures and messages and foreign-edit writes read as before, and the spin check's growth now
holds the subagent transcripts of the 59 that delegate, all 2,916 found through the declaration. The
slowest busy reading took 6.3 ms of CPU against 0.02 before, for a session with 458 subagent transcripts,
and the slowest turn end 6.6 ms against 2.9. `tests/test_subagent_liveness.py` holds these readings from
synthetic records.

### Where a subagent path may lead, a background task's end, and a reply no model wrote

A subagent declaration names files ao lists, stats and reads, and an adapter layer the agents it describes
can write may declare it (#77). So every path it names stays in the session's own subagent directory. `dir`,
each transcript's `path` and the sidecar's `path` are names joined by `/`, with no empty step, `.`, `..`,
absolute path or drive, and `dir` holds `{session}`: a declaration that breaks this is not read, and
`ao adapters validate` names the path. A reading then compares real paths: the subagent directory must be
inside the session transcript's own directory, and every transcript and sidecar inside the subagent
directory. A symbolic link whose target leaves it is not followed - a transcript, a sidecar, or a directory
a wildcard reaches - and a link whose target stays inside is. `*` and `{id}` still match within one name,
and a name starting with a dot only where the step starts with one, as a glob matches.

The declaration is still read from every layer, unlike the sets that decide authority. Those are read from
the package's adapters because a layer could take a product path out of review, hide a writer from a hold or
name a command ao runs. Confined, a subagent declaration chooses only which of the files a store keeps for
that session are its subagents, and what that moves - spend, writes, whether the implementer is working -
the same layer already moves through the rest of `transcript`, which is how a project declares a harness ao
never shipped. Read from the package alone, it would guard nothing confinement leaves open, and one adapter's
shape would come from two layers.

| Field | What it declares | Used by |
|---|---|---|
| `telemetry.failure.ends` | the records that tell a session a task it ran in the background ended: `records`, each `{type, match, field}` - the kind, the values the record holds and the field holding its text - and the pairs of markers the end stands between in that text: `status`, of which `failed_when` lists the failures, `id`, naming the task, and `text`, what a person reads | the panel's problems |
| `transcript.messages.harness_replies` | a list of matches: a reply holding every value of one, with no usage, was written by the harness in the model's place, and a turn holding no other reply was not answered | `ao cost`, `ao cost --features`, the panel's turns, `ao_status`, the credit estimate |

```jsonc
// transcript.messages and telemetry.failure, beside what the stores above declare
"messages": { "harness_replies": [{ "message.model": "<synthetic>" }] },
"failure": { "ends": { "records": [{ "type": "user", "match": { "origin.kind": "task-notification" },
                                     "field": "message.content" },
                                   { "type": "attachment",
                                     "match": { "attachment.commandMode": "task-notification" },
                                     "field": "attachment.prompt" }],
                       "status": ["<status>", "</status>"], "failed_when": ["failed", "stopped"],
                       "id": ["<task-id>", "</task-id>"], "text": ["<summary>", "</summary>"] } }
```

A task the implementer runs in the background - a shell command, a monitor, a subagent, a workflow - ends
after the call that started it returned, so no tool result says how it ended: the harness tells the session
in a notification. An end whose status is a failure reaches the panel's problems as a failed result does,
once however many times the session is told of it. The second harness writes a notification as a `user`
record holding `origin.kind` `task-notification` when no turn runs, and as an `attachment` holding
`attachment.commandMode` `task-notification` queued into a turn that runs; `queue-operation` records repeat
its text while it waits, and are not read.

It was measured read-only on the 419 stores at rest. Their sessions hold 1,517 such records and 1,771 such
attachments, none holding two notifications. 1,010 carry no status - a monitor's or a workflow's progress -
and the others tell an end: 2,000 completed, 183 failed, 90 stopped and 5 killed. A failed end is a command
that exited non-zero (116), an agent that terminated early or stalled (41), and a monitor's script (10), an
MCP task (10) or a workflow's script (6) that failed. A stopped end is work a session's previous process
ended before it finished, told when the session resumed: tasks it found no completion record for (64),
background commands of the previous session (23), agents that did not finish before it ended (3). A killed
end is a task stopped on purpose, by the person in 3 of the 5, so it is no failure. 228 of the 273 failed
and stopped ends name the call that started their task, and none of those calls has a failed result. An end
is told again at times - 9 of the 2,269, all completed - and is read once, by the ids of the tasks it ends.
None of these ends reached the panel before. Its two lines per store now show 555 failures in the 289 stores
that show one, against 554, and 44 of them are background ends, in 38 stores; its 12 MB tails read 205 ends
beside the 2,234 failed results they read before.

A store also writes replies of its own where the model gave none. All 515 in those stores' sessions and 180
in their subagents hold `message.model` `<synthetic>` and no usage, 693 ending at `stop_sequence` and 2 at
`refusal`: 510 are an error the service or the account returned - a usage limit, an API error, a failed
authentication - and 185 the words "No response requested.". Such a reply still ends its turn, for the
watchdog as for every reading. But a turn holding no other reply was not answered: of the 5,035 turns
`ao cost` reads, 344 charge nothing, and 328 of them hold no reply but these - 180 an error after a person's
prompt (96), a notification (79) or a note (5), and 148 "No response requested." after a note (133) or a
notification (15). The other 16 hold no reply at all (14), each the last turn of its transcript, or a
response that charged nothing (2), and read as before; 187 more turns hold such a reply beside the model's
own response, and keep their class.

So a turn holding only replies written in the model's place, with no usage, is `unanswered`: `ao cost`
counts it on a line of its own below the total, and no class, total or feature count holds it; the turns of
the panel, `ao_status` and the credit estimate leave it out, and the panel's average is over the turns the
model answered. It is no turn of work, and no bookkeeping either, which would let the next prompt run on in a
turn that has ended. Compared before and after on the same stores, `ao cost` read the same 42,850,456,356
tokens over the same 5,035 turns, analysis 2,968 turns before and 2,640 after beside 328 unanswered, every
other class unchanged. The panel read the same 29,852,261,803 tokens over 3,348 turns against 3,634; its last
turn is another in 59 stores, and the 49 stores whose every turn is unanswered show no cost line. The credit
estimate, had the adapter declared these stores, read the same 36,329,609,271 tokens over 4,693 turns against
5,021.

Kiro declares none of the three, and every reading of its 17 stores is the same before and after, each
reading's digest included: `ao cost` (470 turns, 26,457.94 credits), the panel's figures (90 turns, 4,575.93
credits), its 21 failure lines in 12 stores and its messages, the turn end, the busy state, foreign-edit
writes and the credit estimate (26,457.92 credits over 447 turns). On the second harness's 419 stores the
listing found the same 2,916 subagent transcripts, names and sidecar calls, and no subagent directory holds
a symbolic link or a hidden name; the turn end, the busy state, messages, the spin check's growth and
foreign-edit writes read as before. The listing reads each entry's kind from the directory listing and
costs less: 34 ms of CPU over the 419 stores against 46, 2.3 ms against 5.9 for the session with 458
subagent transcripts, and 2.8 ms against 6.4 for the slowest busy reading. `tests/test_subagent_bounds.py`
holds these readings from synthetic records, and fails when a core module names a value these declarations
hold.

## Busy detection

Two signals, both cheap, used together:

1. Session metadata status field, where the CLI exposes one.
2. Age of the last write to the transcript file, or to a subagent transcript beside it
   (`transcript.subagents`).

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
  "detect": { "headless": ["--print"] },    // what makes a running mytool an unattended turn
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
