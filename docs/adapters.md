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
| `mcp` | `{file, key, extra, register, remove_when_empty}` or `{manual, snippet}` | `ao init`, `ao remove` |

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

`directives.command_hooks` (`{format: "pre-tool-use", files}`) names the settings whose hooks can
rewrite an agent's shell commands, which `ao doctor` reports as a measurement filter (#51).

What decides authority reads the **package's** adapters only: which directories are a harness's
(`detect.dirs`, left out of review scope and product-dirty checks) and which stores are scanned. A
user's or a project's adapter layer is writable by the agents it describes, so an adapter there
can add a harness but cannot move a product path out of review by calling it a harness directory.

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

The first twenty-one rows are Traycer's canonical enum, the coverage this list is measured
against; the rest are harnesses ao shipped before it. Moving a row to `full` is the most valuable
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
    "record": { "time": "timestamp", "role": "payload.type", "text": "payload.content" }
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
