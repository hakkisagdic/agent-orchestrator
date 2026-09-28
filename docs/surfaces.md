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
monitor. See the TUI section below. `ao status` prints the panel once.

*In ao since slice JSON-OUTPUT: `ao status --json` prints what the panel shows as one JSON document
and nothing else on stdout - no colour, no banner - for a program to read where it had to read the
panel. The panel is drawn from the same reading, so the two cannot disagree; `-m` and `--window`
shape both, and the exit status is the panel's. `ao fleet --json` ([parallel.md](parallel.md)) and
`ao notices --json` ([telemetry.md](telemetry.md)) do the same for theirs.*

| key | what it holds |
|---|---|
| `project`, `root`, `at` | the project's name, its directory, and when it was read, in epoch seconds |
| `implementer` | `adapter`; `state`: working, slowing, stopped, idle or unknown; `seconds_since_write`; `doing`, what its session says it does; `session`, as ao resolved it: `id`, null where ao settled on none; `how`: pinned, recorded, discovered, ambiguous or unresolved; and `why`, what a person reads beside it, which a recorded or discovered session has too, so only a null `id` says ao could not resolve one; `working_elsewhere` (`name`, `root`, `seconds_since_write`) while it writes in a secondary project. Null with no implementer |
| `workspaces` | null while there is an implementer; without one, up to five workspaces with a local session to point ao at, newest first (`path`, `seconds_since_write`) |
| `telemetry` | `context_percent`, `cost_unit`, `turns`, `cost_total`, `cost_average`, `cost_delegated`, `last_turn_cost`, `last_turn_tool_calls`, and `machine_quota`: the lines keyflip reports for the other tools on this machine, never the implementer's own pool |
| `problems` | `nothing_to_do_since` (epoch seconds), `spinning_minutes`, `nudge_error` (`at`, `code`, `tail`, `log`) and `agent_errors` (`time`, `text`) |
| `returned_reviews` | reviews that ended and nobody collected: `id`, `slice`, `verdict`, `state` |
| `reviews`, `rounds` | the newest review artefacts (`file`, `verdict`); the rounds the running slice spent and its budget (`spent`, `budget`), null with no artefact |
| `throughput` | what the window produced: `hours`, `staged`, `landed`, `decisions_asked`, `decisions_open`, `oldest_open_minutes`, `state`, and `stall` (`minutes`, `candidate`, `paths`, `reason`) |
| `repository` | the newest three commits (`log`), `dirty_files`, and where the checkout stands: `ahead`, `behind`, `base`, `merged`; `ahead`, `behind` and `base` are null with no remote default branch to compare against |
| `mail` | the mailbox directory (`dir`), the messages in it (`waiting`), the marked ones for the role asking (`urgent`: `id`, `to`, `title`; null when the mailbox could not be read), and those nobody was shown, oldest first (`unseen`: `id`, `class`, `age_seconds`) |
| `board` | `counts` by state, and the `blocked` items: `id`, `title`, and `why`, null when the board gives no reason |
| `messages` | the recent messages, oldest first: `time` (HH:MM), `role` (user or assistant), `text` |

Without an implementer `telemetry`, `problems` and `throughput` are null and `messages` is empty.
Unlike the panel, the document records no message as seen, whoever runs it: a program reads it,
and a status bar polling it would otherwise keep a marked message no person was shown from climbing
the watchdog's ladder ([messaging.md](messaging.md)). Such a message is in `urgent` and in `unseen`
both.

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

Colour and screen codes go only where something draws them. Output that is not a terminal - a
pipe, a file, a scheduled job's log - carries no ANSI codes, and no output does while `NO_COLOR` is
set, to any value, or under `TERM=dumb`. There `ao watch` prints the panel once and exits: in a loop
it wrote the alternate screen and a clear before every frame into whatever read it, and never ended.

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
