# Control surfaces

The agents run in terminals and IDEs. You are somewhere else — a desktop chat app, an
editor, your phone. Today that means walking over to look. It should not.

The fix is not a new app. It is **one event log with many cheap readers**.

## The event log

*In ao since slice EVENTS-LOG: ao appends one line to `~/.ao/events.jsonl` each time it records a
verification, submits a review or sees a submitted one end, grants commit authority, sends mail, and
nudges or wakes an agent; `ao events` reads the log, and with `--follow` prints each event as it is
written. Before, a surface learned what ao did by reading each project's files again, and a person
waiting on a submitted review ran `ao reviews` until it came back.*

```bash
ao events                                   # the newest 20 events of every project on this machine
ao events --project acme-api --since 2h     # one project's, of the last two hours
ao events --follow                          # then each event as it is written, until Ctrl+C
ao events --follow --json                   # each event as a line of JSON, for a program
```

Each line is one JSON object: when (`at`, seconds since the epoch, to the millisecond), which
project (`project`, the key its files in `~/.ao` carry), what happened (`kind`), and a short
payload (`data`) of ids, names and verdicts - never a body, a diff or a transcript. A field with
no value is left out.

```jsonc
// ~/.ao/events.jsonl
{"at":1790413391.207,"project":"acme-api","kind":"verification","data":{"id":"V-1790413391","passed":true,"profile":"quick"}}
{"at":1790413402.551,"project":"acme-api","kind":"review-submitted","data":{"review":"R-1790413402551","slice":"B8","tree":"4b825dc6…"}}
{"at":1790414318.004,"project":"acme-api","kind":"review-finished","data":{"review":"R-1790413402551","state":"finished","verdict":"APPROVED","slice":"B8","artefact":"2026-09-26-121838-a1b2c3d.md"}}
```

| `kind` | written when | `data` |
|---|---|---|
| `verification` | `ao verify` records its result | `id`, `passed`, `profile` |
| `review-submitted` | `ao review submit` pins the staged candidate | `review` (its `R-` id), `slice`, `tree` |
| `review-finished` | a submitted review ends | `review`, `state` (`finished`, `unavailable`, `failed` or `stale`), `verdict`, `slice`, `artefact` |
| `authority-granted` | `ao commit-ok` grants | `token`, `verification`, `review`, `waiver`, `index_tree` |
| `mail-sent` | ao writes a message into the mailbox | `message` (its file name), `kind`, `from`, `to` |
| `nudge` | the watchdog starts the implementer's turn | `pid`, `attempt`, and `exit` when the turn ended within its first seconds |
| `wake` | the watchdog wakes the architect | `why` (`reports` or `refill`), `pid`, `reports` or `queue`, and `retried` when a wake before it failed |

What the log leaves out is on the record elsewhere: a refused `ao commit-ok` stays in the authority
ledger, a review run in the foreground (`ao review` without `submit`) is seen by whoever runs it, a
submitted review whose run died before writing its end is the one `ao reviews` shows as lost, and a
message an agent writes into the mailbox by hand is not one ao sent.

The log observes; nothing reads it to decide anything. Writing an event never fails the command
that did the work: an event that cannot be written is said on standard error, and the
verification, grant or mail it would have told of stands. It is one file for the whole machine,
bounded as the other observation stores are: once it is a quarter past `retention.events_kb`
(2048 KB), its oldest lines go, back to three quarters of it. `ao events --follow` reads on across
that trim, and says so if a trim ever took lines it had not reached. Every string in a payload is
scanned for credentials first, as evidence is ([safety.md](safety.md)). `ao remove` leaves a
project's lines in the log: they are the machine's history.

```bash
ao config set retention.events_kb 4096 --machine
```

Not built yet: the other two producers. Hooks, where an agent's CLI has them - a `Stop` hook
with a `command` action appending a line, cheap and instant - and a poller, where it has none,
deriving a turn's end, its context and its cost from the records the vendor already writes. Until
then an agent's turns are read from its transcript, as `ao status` does.

Why this matters: without the log, every surface has to parse *N* vendor transcripts in
*N* formats. With it, a new surface is a file tail. That is the whole design.

## Surface 1 — terminal dashboard

The default. `ao watch` renders a panel from the project's files; leave it on a second
monitor, or keep it in a browser tab with `ao watch --web` (Surface 4). See the TUI section below.
`ao status` prints the panel once.

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
Unlike the panel in a terminal, the document records no message as seen, whoever runs it: a program
reads it, and a status bar polling it would otherwise keep a marked message no person was shown from
climbing the watchdog's ladder ([messaging.md](messaging.md)). Such a message is in `urgent` and in
`unseen` both.

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
the control room, and the switches from [`mcp.md`](mcp.md) apply: `ao_verify` stays off
until you turn it on, and a server given no `--role` serves every tool, which is what a
person's own control room wants ([roles](mcp.md#roles)).

The MCP tools read the same files the CLI does, which is why a surface needs no store of its
own.

## Surface 3 — notifications

A `Stop` hook that fires a desktop notification costs one line and reaches you when you
are not looking at anything. Use `command`-type actions, never `agent`-type: an agent
action on turn-end can start another turn, and now you have a loop.

## Surface 4 — a browser tab

`ao watch --web` serves what `ao watch`, `ao board` and `ao fleet` print as three pages on
127.0.0.1, for a person who would rather keep a browser tab open than a terminal:

```bash
ao watch --web                # http://127.0.0.1:8732/ is the panel; /board and /fleet sit beside it
ao watch --web --port 9000    # another port; 0 takes a free one, and the line it prints names it
ao watch --web --all          # the same pages; the address it prints is the fleet's
```

*In ao since slice WEB-VIEW: `ao watch --web [--port N]`. It is a view of ao, not a new app. Each
page is computed when it is asked for, from the project's own files, by the functions the three
commands print with: the view keeps no state, has no form, answers no method but GET (405 for any
other) and loads nothing from anywhere else - its style is inline, it runs no script, and its
Content-Security-Policy allows none. The reads it shares with those commands keep what they keep for
the next reader, such as a provider's quota reading; the view adds no write of its own. A page
refreshes itself every `--interval` seconds, 15 unless changed, with a meta tag. The server binds
127.0.0.1, refuses any address that is not loopback, and answers only a request addressed to
127.0.0.1 or localhost at its own port, so a page from another site cannot point its own name at this
machine and read the view through your browser. Loopback keeps the view off the network, not away
from the machine's other accounts: on a shared machine, anyone logged in can read it. One thing the
terminal does, a page does not: it marks no urgent message seen. Any process on the machine can
fetch a page, and a fetch is no proof that a person read it, so the message's alarm keeps climbing
until a command shows it to its reader. A page carries the panel's colours when ao's own output
would: started from a terminal, and not while `NO_COLOR` is set.*

A menu-bar item is a natural next reader, not built yet. Neither it nor the tab should become the
only way to do something.

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
| **Web UI / Electron** | Heaviest possible answer to "show me six numbers", and it competes with Surface 2, which is better. `ao watch --web` is not one: it serves the panel's own text to a tab, with the standard library. |
| **curses** | In stdlib, but its model fights partial redraw and it degrades badly over SSH. Raw ANSI is simpler and more portable. |

The renderer is deliberately separated from the data layer, so replacing it later costs a
file, not a rewrite.
