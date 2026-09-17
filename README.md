**English** | [Türkçe](README.tr.md)

# agent-orchestrator

**Point it at the agent you are already running. It picks up from there.**

No new harness. No re-planning. No "start a fresh session so the tool can manage it."
`ao` attaches to a coding agent that is already working — in your IDE, in a terminal,
started by someone else — reads its state without touching it, and takes over the
tedious half: noticing when it stalls, running the gates, deciding what may be
committed, and keeping work moving while you sleep.

That is the whole difference. Every other orchestrator owns the agent: it spawns the
process, it drives the loop, and adopting it means restarting your work inside it.
`ao` owns the *authority* instead — what is finished, what is good, what may land —
and leaves the agent where it is.

```bash
pip install ao-orchestrator                  # or: uv tool install ao-orchestrator
cd ~/your-project && ao status      # it finds the running session by itself
```

Or with nothing installed at all, using the Python macOS and most Linux distributions
already ship:

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator ~/ao
mkdir -p ~/.local/bin && ln -s ~/ao/bin/ao ~/.local/bin/ao    # ~/.local/bin must be on PATH
```

A symlink, not a shell alias: neither `/bin/sh`, which runs ao's commit hook, nor an agent
the watchdog starts without your shell can see an alias.

No dependencies, by choice — this watches agents on machines it does not control, and
a dependency is a thing that can be missing exactly there. Nothing to configure before
the first run: `ao` discovers the session from the agent's own store — Kiro's and Claude
Code's today — and `session: auto` in a config written by `ao init --profile` resolves the
same way. Where the implementer and the architect run one harness in one directory, `ao`
does not guess which session is whose: `ao doctor` says so, and a pinned id settles it
([docs/profiles.md](docs/profiles.md#how-auto-finds-a-session)).

**Windows** ships PowerShell and does not ship Python, so `bin/ao.ps1` covers `status`,
`board` and `doctor` with nothing installed. It is a deliberate subset and stays one —
everything else either takes a decision, spends the machine, kills processes or speaks
a protocol, and a second implementation of any of those is a second thing to be wrong.
For the rest: `winget install Python.Python.3.12 && pip install ao-orchestrator`.
The script is written and reviewed but **not yet run on Windows**.

> **Where this came from.** Extracted from a 30-epic durable-workflow product built
> over weeks by exactly this loop. Every guard in here exists because something went
> wrong first: a watchdog that started a second turn on one session and left a rename
> half-applied; fifteen agent processes accumulated in one repository; a timestamp bug
> that made live evidence look three hours stale. [`docs/lessons.md`](docs/lessons.md)
> is the list. None of it was designed in advance.

---

## What it does

**Watches.** `ao watch` is a live panel: what the agent is doing, context and cost,
open reviews, the work board, and — the part no other panel has — whether it is
*busy but producing nothing*. An agent stuck in a wait loop looks perfectly healthy:
transcript growing, tool calls firing, credits burning. `ao` compares activity
against artifacts and says so.

**Restarts.** Turn-based agents stop when a turn ends, mid-slice or not. A watchdog
notices and nudges. Detection is free — a file mtime and a couple of `git` calls —
and a chain of guards makes sure a nudge is never spent where it cannot help: no
second turn while one is running, no nudge into a provider outage, no nudge past the
round budget, no nudge when a human has taken the tree.

**Verifies.** `ao verify` runs *your* gates and writes the numbers to a ledger. Not
the agent's report of its gates — the commands, run again, by something with no stake
in the result.

**Decides.** `ao commit-ok` grants commit authority from that evidence: gates passed,
review approved, plan unedited, and the measurement still describes the tree in front
of it. Every refusal names its missing condition. It never covers `push`.

**Keeps going.** A slice blocked on a human decision is parked with the reason
recorded, and work moves to the next pre-authorised item. When the queue runs dry, the
*architect* is woken to refill it — never the implementer, because choosing your own
scope is the one authority an implementer must not have.


## Setup, step by step

1. `pip install ao-orchestrator` (or `uv tool install ao-orchestrator`, or the Homebrew tap).
2. In the repository: `ao init --profile claude-kiro`. Writes `.ao/`, the mailbox,
   the playbook, and registers the MCP server for the agents it detects. With one harness for every role,
   `claude-claude` needs a review tier, because its reviewer is of the implementer's own model family:
   `ao init --profile claude-claude --review-tier same-family --by <name>` lets another model of that
   family review, and every such review says `same family: weaker independence`;
   `ao init --profile claude-claude --review-tier person` configures no model reviewer, and a person
   reviews each candidate with `ao person-review --by <name>`. A reviewer of another family stays the
   default and the strongest ([review tiers](docs/roles.md#review-tiers)).
3. **Wire the rules.** ao does not write your `CLAUDE.md` / `AGENTS.md`: paste the printed pointer
   there, or re-run `ao init --rules`. Until then `ao doctor` says `rules-not-wired` — the playbook
   is written but no agent reads it. (A Claude Code skill file is discovered on its own.)
4. Start or restart the agent's app in the directory so the `ao` MCP tools load.
5. `ao doctor`. Then `ao email setup` and, if you want it, `ao telegram setup`; `ao pings setup --url`
   for the dead man's switch.
6. `ao watchdog install` for unattended runs (a doctor job comes with it). `ao features` to choose
   what you pay for; `ao remove --yes` takes everything off again.
7. `ao prove`. It runs the guarantees instead of describing them: the hook must refuse a synthetic
   candidate, the reviewer must answer as another actor, and a throwaway slice must pass verify,
   review and commit-ok. Nothing is committed. [Getting started](docs/getting-started.md) is the
   whole sequence.

## Commands

| | |
|---|---|
| `ao status` · `ao watch` | one project: state, telemetry, problems, board |
| `ao watch --all` · `ao fleet` | every project, ordered by what needs a human first |
| `ao board` | where each item is; READY = queued items whose `needs:` are done |
| `ao verify [-p full]` | run the declared gates, record the result |
| `ao commit-ok [--verify]` | may this tree be committed? decided from evidence |
| `ao hold` / `ao hold release --note …` | stop every agent in the tree, and keep them stopped |
| `ao writers` / `ao writers --clean` | live turns in the tree (one per turn, not per process), orphans set aside; `--clean` stops only the orphans |
| `ao fanout ok --agents N` / `ao fanout record …` / `ao fanout history` | may a fan-out of N sub-agents start now (hard cap, recent limit hit, provider window); record what one cost |
| `ao cost --since 24h` | what the coordination itself spends: implementer turns by class (product / analysis / ceremony / coordination), wasted turns, reviews; `--since`, like every time a command takes, is `30m`, `2h`, `1d`, `today`, `yesterday` or a date |
| `ao features [on|off <key>]` | the switches and what each costs; all off = deterministic ao, zero model spend ([features.md](docs/features.md)) |
| platforms | macOS and Linux native; Windows first cut ([windows.md](docs/windows.md)); the suite runs on Ubuntu for every push and pull request, on macOS and Windows weekly |
| `ao waive review --slice B7 --by <name> --why …` / `ao catchup` | a person bypasses a gate on the record; catchup reviews each landed range with a model family other than the one that wrote it, against what its commit messages claim, closes a `move-only` split on the proof its grant recorded, run again, and replays deferred wakes and nudges. `ao catchup --plan` previews it, names what closes by proof, and writes nothing, `ao catchup --limit 10` and `ao catchup --slice B7` bound a run, `ao catchup --author-family <family> --by <name>` is a person naming a family ao did not record, and `ao catchup --move-only <slices> --by <name>` is a person stating which waived slices only moved code, each closing only where the proof holds; a run exits 3 when the reviews it started decided nothing, and 0 when it made progress or had nothing to do |
| `ao pings setup --url …` | dead man's switch: external pings that alarm when the watchdog and its doctor job both die |
| `ao hooks [status|install|uninstall] [--allow-shared-hooks]` / `ao push allow` | resolve Git's effective hook path; each role is independent, and shared/external/global mutations require explicit command-wide authorization |
| `ao skill install` / `ao skill show` | the playbook (roles, loop, authority, protocol, alarms, every command) rendered for the agents this repo uses: Claude skill, Kiro steering, AGENTS.md |
| `ao remove --yes [--allow-shared-hooks]` | two-phase removal: delete and commit `.ao-project` while enforcement remains active, then remove AO state after HEAD and index no longer contain it; foreign/protected hooks stay untouched. The second phase takes off the project's scheduled jobs and exactly its own files in `~/.ao`, lists each by name in the dry run, and exits 1 naming what it could not remove ([watchdog.md](docs/watchdog.md)) |
| `ao init --profile claude-kiro|claude-claude [--review-tier same-family|person] [--language en|tr]` | write role blocks and exact `.ao-project` enrollment marker without staging it; a single-harness profile chooses a review tier ([profiles.md](docs/profiles.md)); `--language tr` has what ao writes into the project and for its people in Turkish ([configuration.md](docs/configuration.md)) |
| `ao doctor --check` | quiet doctor for a scheduler: one line per problem, exit 1, alarms raised — installed as a 15-minute launchd job by `ao watchdog install` |
| `ao email setup` / `ao email test` | the red alarm channel: e-mail via formsubmit.co, no server ([alarms.md](docs/alarms.md)) |
| `ao alarms` / `ao alarms test --level red` | live alarm episodes and their level; test rings every channel |
| `ao mail log` / `ao mail search <text>` / `ao mail ack <glob>` | the mail ledger: every message written and when it was consumed, searchable after deletion |
| `ao watchdog explain` / `ao watchdog trace` | why the watchdog did or did not act: measurements and verdicts of this cycle, and of the recorded ones ([watchdog.md](docs/watchdog.md)) |
| `ao source import` | admit tracker items onto the board |
| `ao mail` · `ao notices` | coordination messages; alerts this project raised |
| `ao watchdog install` | launchd job that restarts a stalled agent |
| `ao mcp serve` · `ao a2a serve` | expose state to MCP clients / as A2A tasks |
| `ao telegram setup` | alerts to your phone, decisions back from it |
| `ao digest [--days N]` | what happened, read from the ledgers — also answers "why is nothing moving" |
| `ao ask` · `ao answer` · `ao decisions` | questions answerable in one tap; free text always last; an answer must be an offered key, and `ao answer <D-id> <key> --change` replaces one while the first stays on the record |
| `ao note` | an architect message into the mailbox, through the tool |
| `ao review` | review the tree with an actor that did not write it |
| `ao person-review --by <name>` | a person reads the staged diff, then records `--verdict APPROVED` or `NEEDS_CHANGES` with the `--digest` it showed; bound to the candidate like any review, labeled `person review`, never an agent's command |
| `ao review --commits <range>` | review landed work after the fact; exit 3 means no reviewer could review (never a verdict), fallbacks in `reviewer.fallbacks` ([roles.md](docs/roles.md)) |
| `ao handoff` | write and send everything a successor needs |
| `ao a2a-mcp serve` | reach A2A agents from an MCP-only client |
| `ao prune` | trim accumulated records and logs |
| `ao doctor` · `ao adapters` | check the wiring; what is supported and how well |
| `ao --version` | the installed version |

Hook status separates static intent from executable enforcement. AO enforcement is
opted in only by a root `.ao-project` whose exact `ao-project-v1\n` bytes exist in
HEAD or the active index; an incidental `.ao/` directory is inert. A staged marker
enables first adoption, while a staged deletion remains enrolled through HEAD until
that deletion is committed. An enrolled project requires `.ao/config.json` to be a
valid, non-empty top-level JSON object. AO reads at most 1,048,576 bytes and
rejects container nesting deeper than 64 before recursive JSON decoding;
pre-dispatch loading and commit enforcement use the same bounded result. Missing
or unreadable state refuses with the one-line `ao init --profile claude-kiro`
repair.

The `current-local (behavior unverified)` and `current-scoped (behavior unverified)`
labels describe bytes only; `pre-commit execution: installed (execution proved)`
is printed only after Git resolves and runs the active hook with an isolated
synthetic index and AO returns the nonce-bound refusal for that exact challenge.
The probe never commits, changes the real index, or writes a Git object. A missing,
misplaced, non-executable, stale, foreign, or fail-open hook is `not installed`, and
`hooks status`, `doctor`, `doctor --check`, and `init` consume the same result.
`status` also names the effective path, path class, winning `core.hooksPath`
scope/origin/value, track state, and misplaced AO forms. A hook installed in the
repository's git directory also names the ao that installed it, and falls back on that
file only when `/bin/sh` finds no ao on PATH; `hooks status` and `doctor` say when it
cannot, with the symlink that fixes it. Install handles
pre-commit and pre-push independently, so it may install an eligible pre-commit,
preserve a custom pre-push byte-for-byte, report the push-window hook unavailable,
and return 1. Install, uninstall, and remove refuse the entire mutation set when
any eligible target is shared, external, or selected by global/system config
unless `--allow-shared-hooks` is explicit.

## The rules it enforces

These are not style preferences. Each one is a failure that cost real hours.

- **Whoever writes the code does not verify it, and does not decide it may land.**
- **A plan is read, never edited** — when the document a slice is judged against can be
  edited by the thing being judged, every later check is circular.
- **Pulling work is not authorising it.** A tracker item is something a person wrote,
  not a specification anyone verified. It enters the queue with a written acceptance
  boundary or it does not enter.
- **Heavy operations belong to one actor.** Gates are serialised machine-wide; N
  projects running N test suites is not N times the throughput, it is one suite that
  no longer finishes.
- **Authority lives in always-included context, never in a mailbox.** A stuck agent is
  exactly the agent not reading its mail.
- **`push` is never granted by this tool.** Nor PRs, force-pushes, or hook bypasses.

## Agent support

`ao` reads each agent's own session store; the adapter says where and in what shape.

| verified | adapters |
|---|---|
| **full** — every capability exercised in a production run | kiro, claude-code, antigravity |
| **partial** — reads state, some capabilities unexercised | opencode, command-code |
| **documented** — written from published docs, not yet run | cursor-agent |
| **untested** — schema present, needs a first run | codex, gemini, aider, amp, copilot, amazon-q, deepseek, qoder, ollama |

`ao adapters` shows this table against what is actually installed on your machine, and
— with [keyflip](https://github.com/hakkisagdic/keyflip) — whether an account exists
even when the CLI does not. Adding one is a JSON file; see
[`docs/adapters.md`](docs/adapters.md).

The hosted `tests` workflow runs the suite on every push to main and every pull request
on Ubuntu, with Python 3.9 (the support floor) and 3.12; macOS and Windows run every
week and on demand (`gh workflow run tests -f os=windows-latest -f python=3.12`). The
repository is public, so the hosted runners cost nothing, and the pre-push hook still
runs the suite locally before a push. A release tag runs the suite again before it
publishes. Hosted runs cover OS
API behavior and deterministic process crashes with real child processes and
temporary paths. They are not physical power-loss, storage-controller or filesystem
qualification, including unsupported and network filesystems.

## Documentation

**[getting started](docs/getting-started.md)** ·
[protocol](docs/protocol.md) · [safety](docs/safety.md) · [roles](docs/roles.md) ·
[capability matrix](docs/capability-matrix.md) ·
[architecture decisions](docs/adr/README.md) ·
[slices](docs/slices.md) · [gates](docs/gates.md) · [sources](docs/sources.md) ·
[adapters](docs/adapters.md) · [parallel](docs/parallel.md) · [cloud](docs/cloud.md) ·
[models](docs/models.md) · [telegram](docs/telegram.md) · [mcp](docs/mcp.md) · [telemetry](docs/telemetry.md) ·
[surfaces](docs/surfaces.md) · [ledger](docs/ledger.md) · [recovery](docs/recovery.md) ·
[keyflip](docs/keyflip.md) · [ide-extensions](docs/ide-extensions.md) ·
**[lessons](docs/lessons.md)**

## Status

Working today: everything in the command table above, exercised daily against a real
project. Still specification: every passage the documents mark as not built yet, lanes and
the merge queue among them, and cross-project parallel *execution* (the view exists; running
several implementers at once is governed by the machine gate lock but has not been run in
anger).

MIT.
