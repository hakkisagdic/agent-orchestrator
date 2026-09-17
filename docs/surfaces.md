# Control surfaces

The agents run in terminals and IDEs. You are somewhere else — a desktop chat app, an
editor, your phone. Today that means walking over to look. It should not.

The fix is not a new app. It is **one event log with many cheap readers**.

## The event log

Not built yet: the event log. Today every surface reads the project's own files, the mailbox,
the board and the ledgers, as `ao status` does.

<!-- not built: nothing writes an event log; a surface reads the project's files -->
```
~/.ao/events.jsonl        # append-only, one line per event, never rewritten
```

```jsonc
{"ts":"2026-09-03T19:23:11Z","project":"acme-api","actor":"kiro","lane":"impl",
 "kind":"turn_end","summary":"reviewer retried after load failure",
 "credits":354.8,"context_pct":67.5,"tools":540}
{"ts":"…","project":"acme-api","actor":"self","kind":"gate","result":"pass","tests":"326/326"}
{"ts":"…","project":"acme-api","actor":"kiro","kind":"review","verdict":"NEEDS_CHANGES","findings":1}
```

Three kinds of producer feed it:

- **Hooks**, where the CLI has them. A `Stop` hook with a `command` action appends one
  line. Cheap, instant, no polling. This is the mechanism to prefer.
- **A poller**, where it does not. Watch the transcript's write age, derive `turn_end`,
  read context and cost from the records the vendor already writes.
- **The orchestrator itself**, for events no agent knows about: mail sent, gate run,
  commit authority granted, lane started, merge queued.

Why this matters: without the log, every surface has to parse *N* vendor transcripts in
*N* formats. With it, a new surface is a file tail. That is the whole design.

## Surface 1 — terminal dashboard

The default. `ao watch` renders a panel from the project's files; leave it on a second
monitor. See the TUI section below.

## Surface 2 — MCP, i.e. any chat app becomes the cockpit

This is the answer to *"manage it without leaving the app I'm already in."* Register the
server once in Claude Desktop, Cursor, Zed, VS Code — anything that speaks MCP:

```jsonc
{ "mcpServers": { "agent-orchestrator": { "command": "ao", "args": ["mcp", "serve"] } } }
```

Then the orchestration is reachable in ordinary conversation:

> *"What is Kiro doing?"* → `ao_status`
> *"What is blocked, and on what?"* → `ao_board`
> *"Has anyone answered its question?"* → `ao_decisions`
> *"Did the gates pass?"* → `ao_verify`, on a server started with `--allow-verify`
> *"Why has nobody nudged it in twenty minutes?"* → `ao_watchdog`

Not built yet: reading another agent's messages, writing to it and nudging it from the chat
app. Today they are `ao tail`, `ao note` and the watchdog.

No terminal, no context switch, no new UI to learn. The chat app you already have becomes
the control room, and the one switch from [`mcp.md`](mcp.md) applies: `ao_verify` stays off
until you turn it on.

The MCP tools read the same files the CLI does, which is why a surface needs no store of its
own.

## Surface 3 — notifications

A `Stop` hook that fires a desktop notification costs one line and reaches you when you
are not looking at anything. Use `command`-type actions, never `agent`-type: an agent
action on turn-end can start another turn, and now you have a loop.

## Surface 4 — later, optional

A menu-bar item and a small web view are natural next readers of the same log. Neither is
required, and neither should become the only way to do something.

## Align, don't depend

Borrowed, with credit, from keyflip's CodexBar bridge: **read what other tools leave on
disk; never require, spawn or link against them.** agent-orchestrator reads keyflip's
output if keyflip is installed and degrades quietly if it is not. The same courtesy is
owed to anything else on the machine.

`keyflip surfaces` already enumerates the AI tools present on a machine, and
`keyflip codexbar` reads CodexBar's config the same way. If those exist, use them; if not,
carry on.

---

# TUI: what we use and why

**Default renderer: Python standard library plus ANSI. No dependency, no install.**

Zero-install is the project's differentiator — an orchestrator you have to build before
you can watch your build is a bad joke. macOS and every Linux ship Python 3; ANSI works in
every terminal that matters.

That constraint is less limiting than it sounds. With stdlib alone the panel gets:

- the **alternate screen buffer**, so quitting leaves your scrollback intact
- **partial redraw** with cursor addressing instead of clear-and-reprint, which removes
  flicker entirely
- **resize handling** via `SIGWINCH`
- **single-key input** in raw mode: `q` quit, `r` refresh now, `m` read mail, `l` lanes,
  `1`–`9` switch project — no Enter, no prompt
- **sparklines** for credit burn and context growth, drawn with block characters
- **height-aware layout**, which is not a nicety: a panel taller than the window
  scrolls, and a scrolled panel stacks a fresh header on every refresh until the
  screen is nothing but headers. Lay out the fixed sections first, give the
  message log whatever lines remain, and truncate by real lines rather than by
  list elements — a section header carries its own leading blank line, so the two
  counts differ and off-by-two is enough to reintroduce the scroll.

That is a real TUI, in about two hundred lines, that runs anywhere.

## Progressive enhancement, never a requirement

If `rich` or `textual` happens to be importable, the panel uses it for nicer tables and
truecolor. If not, the ANSI renderer runs. The feature set is identical; only the polish
differs. Nothing is gated behind an install. Not built yet: `ao watch` has only the ANSI
renderer today.

<!-- not built: ao watch has no --rich; the ANSI renderer is the only one -->
```bash
ao watch              # stdlib ANSI renderer
ao watch --rich       # uses rich/textual if present, otherwise falls back with a note
```

## What we rejected, and why

| Option | Why not |
|---|---|
| **Textual as the default** | Beautiful, and a `pip install` before you can see anything. It stays optional. |
| **Go / Rust TUI (Bubble Tea, ratatui)** | Single binary is genuinely attractive, but it adds cross-compilation, a release pipeline and a second language to a project whose core is a protocol and some scripts. Revisit if the panel becomes the main product. |
| **Web UI / Electron** | Heaviest possible answer to "show me six numbers", and it competes with Surface 2, which is better. |
| **curses** | In stdlib, but its model fights partial redraw and it degrades badly over SSH. Raw ANSI is simpler and more portable. |

The renderer is deliberately separated from the data layer, so replacing it later costs a
file, not a rewrite.
