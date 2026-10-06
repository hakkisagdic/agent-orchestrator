# Commands

Every `ao` command and what it does. The [README](../README.md) starts with the five you use for every change; [getting started](getting-started.md) is the first run, step by step.

| | |
|---|---|
| `ao status` · `ao watch` | one project: state, telemetry, problems, board |
| `ao watch --all` · `ao fleet` | every project, ordered by what needs a human first |
| `ao watch --web [--port N]` | the panel, the board and the fleet as read-only pages on 127.0.0.1: a browser tab in place of a terminal ([surfaces.md](surfaces.md)) |
| `ao board` | where each item is; READY = queued items whose `needs:` are done |
| `ao board add ID "title" --acceptance "…"` | admit one item, only with its acceptance boundary |
| `ao verify [-p full]` | run the declared gates, record the result |
| `ao commit-ok [--verify]` | may this tree be committed? decided from evidence |
| `ao commit -m …` · `ao commit-check --range A..B` | commit the granted candidate, its message ending with Ao- trailers that name the grant; check that each landed commit has the tree its trailers name, with no `.ao/` needed, as CI runs it ([gates.md](gates.md#a-commit-names-its-grant)) |
| `ao hold` / `ao hold release --note …` | stop every agent in the tree, and keep them stopped |
| `ao writers` / `ao writers --clean` | live turns in the tree (one per turn, not per process), orphans set aside; `--clean` stops only the orphans |
| `ao fanout ok --agents N` / `ao fanout record …` / `ao fanout history` | may a fan-out of N sub-agents start now (hard cap, recent limit hit, provider window); record what one cost |
| `ao cost --since 24h` | what the coordination itself spends: implementer turns by class (product / analysis / ceremony / coordination), wasted turns, reviews; `--since`, like every time a command takes, is `30m`, `2h`, `1d`, `today`, `yesterday` or a date; `--usd` prices the tokens in US dollars from a bundled price table, as an estimate ([telemetry.md](telemetry.md)) |
| `ao features [on|off <key>]` | the switches and what each costs; all off = deterministic ao, zero model spend ([features.md](features.md)) |
| platforms | macOS and Linux native; Windows first cut ([windows.md](windows.md)); the suite runs on Ubuntu for every push and pull request, on macOS and Windows weekly |
| `ao waive review --slice B7 --by <name> --why …` / `ao catchup` | a person bypasses a gate on the record; catchup reviews each landed range with a model family other than the one that wrote it, against what its commit messages claim, closes a `move-only` split on the proof its grant recorded, run again, and replays deferred wakes and nudges. `ao catchup --plan` previews it, names what closes by proof, and writes nothing, `ao catchup --limit 10` and `ao catchup --slice B7` bound a run, `ao catchup --author-family <family> --by <name>` is a person naming a family ao did not record, and `ao catchup --move-only <slices> --by <name>` is a person stating which waived slices only moved code, each closing only where the proof holds, and `ao catchup --slice B7 --reviewer <actor>` reviews with another reviewer actor of the table, so runs over different slices go side by side; a run exits 3 when the reviews it started decided nothing, and 0 when it made progress or had nothing to do |
| `ao pings setup --url …` | dead man's switch: external pings that alarm when the watchdog and its doctor job both die |
| `ao hooks [status|install|uninstall] [--allow-shared-hooks]` / `ao push allow` | resolve Git's effective hook path; each role is independent, and shared/external/global mutations require explicit command-wide authorization |
| `ao skill install` / `ao skill show` | the playbook (roles, loop, authority, protocol, alarms, every command) rendered for the agents this repo uses: Claude skill, Kiro steering, AGENTS.md |
| `ao remove --yes [--allow-shared-hooks]` | two-phase removal: delete and commit `.ao-project` while enforcement remains active, then remove AO state after HEAD and index no longer contain it; foreign/protected hooks stay untouched. The second phase takes off the project's scheduled jobs and exactly its own files in `~/.ao`, lists each by name in the dry run, and exits 1 naming what it could not remove ([watchdog.md](watchdog.md)) |
| `ao update [--dry-run\|--yes]` | update ao the way it was installed: a git clone fast-forwards, and is refused while it has uncommitted changes; a Homebrew, pipx or uv install upgrades through its own tool, a pip install through the pip of the interpreter running ao. The command is shown first and runs on `--yes` or a yes at the terminal ([getting started](getting-started.md#5-update-and-uninstall)) |
| `ao uninstall [--yes] [--purge] [--allow-shared-hooks]` | take what ao installed off this machine: every scheduled job of ao's, ao's own hooks in each project the registry knows, and the `ao` entry in their MCP files; a dry run unless `--yes`, naming what it leaves - each project's `.ao/`, `~/.ao` unless `--purge`, and the program, with the command that removes it |
| `ao init --profile claude-kiro|claude-claude [--review-tier same-family|person] [--language en|tr]` | write role blocks and exact `.ao-project` enrollment marker without staging it; a single-harness profile chooses a review tier ([profiles.md](profiles.md)); `--language tr` has what ao writes into the project and for its people in Turkish ([configuration.md](configuration.md)) |
| `ao doctor --check` | quiet doctor for a scheduler: one line per problem, exit 1, alarms raised — installed as a 15-minute launchd job, or systemd user timer on Linux, by `ao watchdog install` |
| `ao email setup` / `ao email test` | the red alarm channel: e-mail via formsubmit.co, no server ([alarms.md](alarms.md)) |
| `ao alarms` / `ao alarms test --level red` | live alarm episodes and their level; test rings every channel |
| `ao mail log` / `ao mail search <text>` / `ao mail ack <glob>` | the mail ledger: every message written and when it was consumed, searchable after deletion |
| `ao watchdog explain` / `ao watchdog trace` | why the watchdog did or did not act: measurements and verdicts of this cycle, and of the recorded ones ([watchdog.md](watchdog.md)) |
| `ao source import` | admit tracker items onto the board |
| `ao mail` · `ao notices` | coordination messages; alerts this project raised |
| `ao watchdog install` | launchd job, or systemd user timer on Linux, that restarts a stalled agent ([watchdog.md](watchdog.md)) |
| `ao mcp serve` · `ao a2a serve` | expose state to MCP clients / as A2A tasks |
| `ao telegram setup` | alerts to your phone, decisions back from it |
| `ao digest [--days N]` | what happened, read from the ledgers — also answers "why is nothing moving" |
| `ao ask` · `ao answer` · `ao decisions` | questions answerable in one tap; free text always last; an answer must be an offered key, and `ao answer <D-id> <key> --change` replaces one while the first stays on the record |
| `ao propose "…" --why "…" [--rule-file <path>]` · `ao proposals` | an agent proposes a change to a rule it works under instead of editing it: recorded in the decision ledger with its evidence, and a person accepts or rejects it with `ao answer`; nothing writes the rule file ([protocol.md](protocol.md#changing-the-rules-an-agent-proposes-a-person-decides)) |
| `ao note` | an architect message into the mailbox, through the tool |
| `ao review` | review the tree with an actor that did not write it |
| `ao person-review --by <name>` | a person reads the staged diff, then records `--verdict APPROVED` or `NEEDS_CHANGES` with the `--digest` it showed; bound to the candidate like any review, labeled `person review`, never an agent's command |
| `ao review --commits <range>` | review landed work after the fact; exit 3 means no reviewer could review (never a verdict), fallbacks in `reviewer.fallbacks` ([roles.md](roles.md)) |
| `ao handoff` | write and send everything a successor needs |
| `ao a2a-mcp serve` | reach A2A agents from an MCP-only client |
| `ao prune` | trim accumulated records and logs |
| `ao doctor` · `ao adapters` | check the wiring; what is supported and how well |
| `ao completion zsh` | print a script that completes ao's commands, options and choices; `bash`, `fish` and `powershell` too, installed as [getting started](getting-started.md#6-shell-completion) shows, and printed again after `ao update` |
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


## Tests

The hosted `tests` workflow runs the suite on every push to main and every pull request
on Ubuntu, with Python 3.9 (the support floor) and 3.12; macOS and Windows run every
week and on demand (`gh workflow run tests -f os=windows-latest -f python=3.12`). The
repository is public, so the hosted runners cost nothing, and the pre-push hook still
runs the suite locally before a push: first the tests the pushed commits touch, then the
whole suite, in four processes where pytest-xdist (the `dev` extra) is installed -
`AO_PREPUSH_WORKERS` sets how many, and 0 keeps one. A release tag runs the suite again
before it publishes. Hosted runs cover OS
API behavior and deterministic process crashes with real child processes and
temporary paths. They are not physical power-loss, storage-controller or filesystem
qualification, including unsupported and network filesystems.
