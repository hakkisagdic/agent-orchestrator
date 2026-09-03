# Control surfaces

The agents run in terminals and IDEs. You are somewhere else — a desktop chat app, an
editor, your phone. Today that means walking over to look. It should not.

The fix is not a new app. It is **one event log with many cheap readers**.

## The event log

```
~/.ao/events.jsonl        # append-only, one line per event, never rewritten
```

```jsonc
{"ts":"2026-09-03T19:23:11Z","project":"voltrai","actor":"kiro","lane":"impl",
 "kind":"turn_end","summary":"reviewer retried after load failure",
 "credits":354.8,"context_pct":67.5,"tools":540}
{"ts":"…","project":"voltrai","actor":"self","kind":"gate","result":"pass","tests":"326/326"}
{"ts":"…","project":"voltrai","actor":"kiro","kind":"review","verdict":"NEEDS_CHANGES","findings":1}
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

The default. Reads the log, renders a panel, leaves it on a second monitor. See the TUI
section below.

## Surface 2 — MCP, i.e. any chat app becomes the cockpit

This is the answer to *"manage it without leaving the app I'm already in."* Register the
server once in Claude Desktop, Cursor, Zed, VS Code — anything that speaks MCP:

```jsonc
{ "mcpServers": { "agent-orchestrator": { "command": "ao", "args": ["mcp", "serve"] } } }
```

Then the orchestration is reachable in ordinary conversation:

> *"What is Kiro doing?"* → `ao_status`
> *"Show me its last five messages."* → `ao_transcript_tail`
> *"Tell it to use a WeakSet brand, not instanceof."* → `ao_mail_send`
> *"Did the gates pass?"* → `ao_verify`
> *"It has been quiet for twenty minutes — nudge it."* → `ao_resume` (idle-guarded)

No terminal, no context switch, no new UI to learn. The chat app you already have becomes
the control room, and the same capability gating from [`mcp.md`](mcp.md) applies — `drive`
stays off until you turn it on.

This is also why the event log matters more than any single renderer: the MCP tools are
thin readers over it.

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
differs. Nothing is gated behind an install.

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
