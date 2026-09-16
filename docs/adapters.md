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

`full` = every capability verified against a running install.
`partial` = command surface verified, transcript store not yet mapped.
`planned` = interface known, not yet written.

| CLI | Send | Resume | Transcript | Busy | Directives | Status |
|---|---|---|---|---|---|---|
| Kiro CLI | ✓ | ✓ | ✓ JSONL | ✓ | steering + hooks | **full** |
| Claude Code | ✓ | ✓ | ✓ JSONL | ✓ | CLAUDE.md + hooks | **full** |
| Antigravity (`agy`) | ✓ | ✓ | ✓ call-return | ✓ | — | **full** |
| opencode | ✓ | ? | SQLite | ? | — | partial |
| Codex CLI | | | | | | planned |
| Gemini CLI | | | | | | planned |
| Cursor Agent (`agent`) | ✓ | ✓ | ✓ call-return | ✓ | .cursor/rules | documented |
| Command Code (`cmd`) | ✓ | ✓ | ✓ JSONL | ✓ | AGENTS.md | partial |
| Codex CLI | | | | | | planned |
| Gemini CLI | | | | | | planned |
| DeepSeek | | | | | | planned |
| Qoder | | | | | | planned |
| Aider | | | | | | planned |
| Amp | | | | | | planned |
| GitHub Copilot CLI | | | | | | planned |
| Amazon Q CLI | | | | | | planned |

Contributions that move a row from `planned` to `full` are the most valuable thing you
can send this project. See [`adapters/README.md`](../src/ao/adapters/README.md).

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
