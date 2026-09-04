# Adapters

One JSON file per agent CLI. Everything vendor-specific lives here; the orchestrator core
knows nothing about any provider. See [`../docs/adapters.md`](../docs/adapters.md) for the
interface, the two observation modes and the support matrix.

## Status of what is here

| File | Status | Verified against |
|---|---|---|
| `kiro.json` | **full** | kiro-cli / Kiro IDE 1.0.411 — reference adapter, multi-week production use |
| `antigravity.json` | **full** | agy 1.1.19 — call-return mode, token accounting verified |
| `claude-code.json` | **full** | Claude Code |
| `opencode.json` | partial | storage path verified, flags from docs |
| everything else | untested | written from documented interfaces |

`untested` means exactly that: the file encodes a documented interface that nobody has run
against a live install. Flags drift between releases. Treat those adapters as a starting
point, not a promise.

## Contributing one

The most valuable contribution to this project is moving a row from `untested` to `full`.
It takes about fifteen minutes.

**1. Verify the command surface.**

```bash
<cli> --help
```

Record the exact non-interactive form. Note whether flags take attached values
(`--print='x'`) or separated ones (`-p 'x'`) — this is not cosmetic. A CLI using Go-style
flags will silently swallow a separated prompt, exit 0, and store nothing. That failure
looks identical to an agent deciding to do nothing, and it cost us an afternoon.

**2. Find out how it reports a turn.**

```bash
<cli> --output-format json --print='say OK'
```

If you get a structured record back, it is a **call-return** adapter — usually with a
session id and token usage included, which is better than any transcript. Record the field
names in `result_record` and `telemetry`.

**3. If not, find the transcript.**

```bash
cd /tmp && mkdir probe && cd probe
<cli> <non-interactive form> 'MYPROBE12345 write this word only'
grep -rl "MYPROBE12345" ~/.<tool> ~/.config ~/.local/share ~/Library/Application\ Support 2>/dev/null
```

Beware false positives: some tools record your *shell history*, so the marker can appear
in a completely unrelated store because your command text contained it. Confirm by looking
at the surrounding record, not just the filename.

**4. Work out busy detection.** A status field in session metadata, the write age of the
transcript, or — for call-return adapters — nothing at all, because the call is
synchronous.

**5. Fill in the file** using `kiro.json` as the model, set `verified` honestly, and open a
pull request. Include the CLI version you tested against.

## Rules

- **argv arrays, never shell strings.** Prompts contain quotes, newlines and backticks;
  there must be no string for them to escape out of.
- **Encode the exact flag form.** Never improvise separated/attached syntax at call time.
- **Read-only observation.** Never write to a vendor's session store. For SQLite-backed
  agents, open read-only — a second writer corrupts the agent's own state.
- **Record where credentials live, never their values.**
- **`verified` is a claim about you, not the tool.** Set it to what you actually ran.
