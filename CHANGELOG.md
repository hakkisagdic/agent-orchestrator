# Changelog

Every release of ao, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are major.minor.patch; while
ao is below 1.0 a minor release may change behaviour, and each such change is listed under
Behaviour changes.

Each line says what a user of ao gets, with the slice that landed it or the backlog row it
closed in parentheses: `#n`, or `part of #n` for a row still open, each kept with its evidence in
[docs/backlog.md](docs/backlog.md). Where a change had neither, its commit is named.
[docs/release.md](docs/release.md) is the checklist for publishing a release.

## [Unreleased]

### New commands

- `ao catchup --reviewer <actor>` reviews a run with another reviewer actor of the project's table in place of the reviewer role, leaving the table as it is, so catch-up runs over different slices go side by side on different platforms ([features.md](docs/features.md)) (CATCHUP-REVIEWER).

## [0.5.0] - 2026-10-01

Everything that landed on main after v0.4.0, each line naming the slice that landed it or the
backlog row it closed.

### Upgrading from 0.4.0

- Run `ao doctor` in each project after upgrading: it names each grant, reviewer flag, session and hook that 0.5.0 holds to a stricter rule, and says what to change.
- Run `ao watchdog install` again: the doctor job 0.4.0 installed runs `ao doctor --check`, which now pages nobody; the job 0.5.0 installs runs `ao doctor --check --notify` (QUIET-CHECK, #40).
- An authority ledger holding grants from 0.4.0 has no chain links, and 0.5.0 refuses it rather than trust it: archive `.ao/ledger/authority.jsonl`, then verify, review and grant the staged candidate again ([docs/ledger.md](docs/ledger.md)) (#3).
- A project set up before the `.ao-project` marker stays governed as `legacy`, and `ao hooks install` refuses there until the marker is adopted: `printf 'ao-project-v1\n' > .ao-project && git add .ao-project`, landed through `ao commit-ok` (AO59e, #103).
- ao writes English unless a project chooses Turkish: `ao config set language tr` in a project, or with `--machine` for every project, brings back the Turkish ao wrote before, byte for byte (LANGUAGE-FILES, LANGUAGE-PROMPTS, LANGUAGE-OUTPUT).
- `ao waive` needs `--slice` and `--by <person>` beside `--why`, and a waiver expires after 24 hours unless `--hours` says otherwise, at most 168 (WAIVER-BOUND, #67).
- The implementer commits with `ao commit -m …`; the shipped Claude Code grant no longer admits `git commit`, whose `--no-verify` skipped the only commit-time check (ACTOR-GRANTS, #58).
- `ao commit` now ends each message with Ao- trailers and refuses a message that already carries them; to make them a gate, require the `commit trailers` status check in a ruleset on main, a person's step ([gates.md](docs/gates.md#a-commit-names-its-grant)) (COMMIT-TRAILER).
- A clone run through a shell alias needs a symlink instead, `ln -s <clone>/bin/ao ~/.local/bin/ao`: the commit hook runs under `/bin/sh`, which cannot see an alias (SAFE-REMOVE).
- Enrolled repositories reinstall their hooks with `ao hooks install`: 0.5.0 writes hook version 4, which reads a Windows drive-letter index path as absolute, and `ao doctor` reads an older ao hook as legacy until it is replaced (HOOK-V4).

### New commands

- `ao commit-check --range A..B` holds each landed commit to the tree its Ao- trailers name, read-only and with no `.ao/` needed, and the tests workflow runs it on every push to main ([gates.md](docs/gates.md#a-commit-names-its-grant)) (COMMIT-TRAILER).
- `ao completion bash|zsh|fish|powershell` prints a script that completes ao's commands, their options and the choices each declares, generated from the ao that prints it and quoted so no word it holds is read as syntax ([getting started](docs/getting-started.md#6-shell-completion)) (SHELL-COMPLETION).
- `ao commit -m <message>` (or `-F <file>`) commits the staged candidate after `ao commit-ok`: it runs the commit hook's authority check itself, so a deleted or redirected hook skips nothing, then compares the tree that landed with the tree the grant bound (ACTOR-GRANTS, #58; LANDED-TREE, #64).
- `ao commit-check` is what the pre-commit hook runs: it revalidates the recorded grant against Git's active index and neither issues nor consumes one ([ADR 0002](docs/adr/0002-immutable-index-candidate-authority.md); 56ea557).
- `ao config [list|get|set|unset] [--machine]` sets what ao used to decide for you - round budget, review timeout, alarm hours, idle minutes, waiver hours, stall and gate timeouts and more - per project or for the machine ([docs/configuration.md](docs/configuration.md)) (SETTINGS, SETTINGS-2, #74).
- `ao prove`, or `ao init --prove`, runs the guarantees instead of describing them: the hook must refuse a synthetic candidate, the reviewer must answer as another actor, and a throwaway slice must pass verify, review and commit-ok, with nothing committed (PROVE, #85).
- `ao stats [--since <when>] [--until <when>] [--slices] [--all]` gives each landed slice's rounds, first-pass approval, time to land, size and defects found later, read from the ledgers and never from what an agent typed (OUTCOMES, #49).
- `ao review submit`, `ao reviews`, `ao review collect <R-id>|--any` and `ao commit-ok --review <R-id>`: a review runs in the background on a pinned copy of the candidate, and grants only while the staged tree is still that tree (PIPELINE-SUBMIT, #27).
- `ao person-review --by <name>` shows a person the staged diff and its digest, then records APPROVED or NEEDS_CHANGES bound to exactly those bytes, labeled as a person's review (REVIEW-TIERS).
- `ao collect-review <nonce> --response <file> --model <model> --by <name>`: when no reviewer can be reached, a person carries ao's exact review request to a session of their choosing and records its answer, bound to the candidate (STANDIN-COLLECT, #75).
- `ao role [show|set|swap]` shows which actor holds the implementer, architect and reviewer roles and reassigns them, refusing any assignment in which an actor would review its own work (ROLE-TABLE, #79; TANDEM, #13).
- `ao merge-check <branch> [--into <ref>]` runs the gates on the merge result in a temporary worktree without touching the checkout, and records which merge the run vouches for (MERGE-EVIDENCE, #39).
- `ao recall <words>` finds earlier decisions, answers, waivers, review findings and lessons across every project on the machine, each with the file and row it came from, and `ao ask` prints the nearest ones with a new question (RECALL, #43).
- `ao worktrees [list|prune --yes]` retires each worktree whose slice landed or was rejected, archiving its ao state and keeping its branch tip under `refs/ao/archive/` (WORKTREE-LIFETIME, #42).
- `ao backup --to <dir|ref|remote:<name>>` and `ao restore <dir>` back up the governance - config, authority, board, backlog, decisions, ledgers, mail and the review artefacts a grant rests on - to a directory with a digest manifest, a local ref, or a remote the host confirms is private, and restore only the files whose digest matches (GOVERNANCE-BACKUP, #46).
- `ao hunt [run|discard|status]` is a read-only bug hunter: on a schedule it reads the next bounded slice of the tree and mails the architect at most a few new leads, never verdicts (BUG-HUNTER, #45).
- `ao content add <source>@<commit> --skills|--steering|--agents [--dry-run]` and `ao content verify` borrow third-party skills, steering files and agent definitions pinned to a full commit, text only and never their hooks; `ao doctor` names a file that drifted from its pin (CONTENT-SEAM, #14; KIRO-CONTENT, #15).
- `ao room search <words>` searches the stored messages of every registered project (MESSAGE-STORE, #80).
- `ao split-check` proves a staged candidate is a pure move: every moved definition byte for byte, nothing else changed (SPLIT-CHECK, #44).
- `ao remove --yes [--allow-shared-hooks]` takes ao off a repository in two phases, its scheduled jobs and exactly its own files in `~/.ao` included; the dry run lists each by name (9c6c54e; AO59a; SAFE-REMOVE).
- `ao --version` prints the installed version (CLI-ROBUST).
- `ao board ready` lists exactly the items that may start now, and exits 1 on a broken dependency edge (READY-GRAPH, #33).
- `ao alarms snooze <key> --until <date> --why <text>` keeps an alarm nobody can act on off the human channels until a date, on the record; `ao alarms unsnooze <key>` lifts it (ALARM-SNOOZE, #108).
- `ao notices <id>` prints the check and every sample a notice was raised on (NOTICE-EVIDENCE, #37).
- `ao doctor --consistency [--repair]` checks the board, the ledgers, the review artefacts and git against each other, and repairs only mechanical disagreements, on the record (CONSISTENCY, #47).
- `ao ask --codebase "<question>"` asks the code through a configured provider, and passes on only an answer that cites files and lines in the repository (CODEBASE-QUESTION, #81).
- `ao answer <id> <key> --change` replaces an answer while the first stays on the record (CLI-ROBUST).
- `ao cost --features` shows what each feature switch spent, measured from the transcript and the watchdog's logs (FEATURE-COSTS, #10).
- `ao status --window <span>` adds throughput: candidates staged and landed, decisions waiting with the oldest one's age, and a stalled slice with its reason (THROUGHPUT, #91).
- `ao init` takes `--review-tier same-family|person` (REVIEW-TIERS), `--language en|tr` (LANGUAGE-OUTPUT), `--rules` (9c6c54e) and `--allow-uncovered-gates` (GATE-COVERAGE, #5).
- `ao email setup --provider smtp` sends the red alarm through any SMTP server, reading the password once from the variable `--password-env` names (NOTIFY-PROVIDERS, #74).
- `ao mail send --class <class>`, `ao mail compact <days>` and `ao mail sync` declare what a message asks of its reader, collapse old bodies to stubs, and push the message store to a private repository (UNREAD-AGE, #30; MESSAGE-STORE, #80; MAIL-SYNC, #83).
- `ao catchup --plan` previews the replay of waived reviews and writes nothing; `--limit`, `--slice`, `--author-family … --by` and `--move-only … --by` bound a run or record what a person states (CATCHUP-READY, CATCHUP-EVIDENCE).
- `ao fanout ok --roots <R> --per-root <K>` gates a two-stage pipeline on its whole bound, R + R×K agents (9c6c54e).
- `ao prune --review-days <days>` archives review artefacts nothing rests on (REVIEW-RETENTION, #38).
- `ao board add ID "title" --acceptance "…" [--needs A,B] [--role R]` admits one item to the board only with its acceptance boundary, and records the plan baseline an import records; a phase id such as `ACME-187/1` is admitted, and `--needs` is checked as the board will read it (BOARD-ADD, BOARD-ADD-2).
- `ao lane start|list|remove ITEM`: a READY board item gets a git worktree and a branch of its own, prepared from the `lane.*` settings, and a lane holding changes nobody committed is not removed (LANE-START).
- `ao pr watch --once`, with `pr.watch` on, mails the implementer what GitHub says its pull requests need - a failed check, a conflict with the base, changes requested - once each, and writes nothing to GitHub (PR-WATCH).
- `ao status --json`, `ao fleet --json` and `ao notices --json` print what the panel shows as one JSON document, for an agent or a script (JSON-OUTPUT).
- `ao events [--follow]` reads the machine's event log, `~/.ao/events.jsonl`: each verification, review submitted or ended, commit grant, mail, nudge and wake, as ao records it (EVENTS-LOG).
- `ao watch --web [--port N]` serves what `ao watch`, `ao board` and `ao fleet` print as three read-only pages on 127.0.0.1 (WEB-VIEW).
- `ao propose`, `ao proposals` and the `ao_propose` MCP tool: an agent proposes a rule, a person accepts or rejects it, and neither writes a rule file (RULE-PROPOSALS).
- `ao cost --usd` estimates the implementer's spend at list prices from `src/ao/prices.json`, naming the table's version and sources; what the table does not price is unknown, never zero (USD-COST).
- `ao update` updates ao the way it was installed, having shown the command first; `ao uninstall` takes off every scheduled job, hook and MCP entry ao set up, and `--purge` `~/.ao` with them (UPDATE-UNINSTALL).
- `ao init --adopt` enrolls, in one step, a project an older ao set up or one whose marker nobody staged (INIT-ADOPTION).
- `ao review cancel <R-id>` stops a submitted review and the reviewer it started, and records it failed with how (REVIEWER-ORPHAN).
- On Linux, `ao watchdog install`, `status` and `uninstall` run the watchdog and the doctor as systemd user timers (LINUX-SCHEDULER).

### Behaviour changes

- `ao commit` ends each message with Ao- trailers naming the grant it was made under, the tree, the verification and the review or waiver it rested on, and refuses a message that already carries them (COMMIT-TRAILER).
- What ao writes into a project, tells its agents and shows a person is English unless the project's `language` is `tr`; markers written in either language are read in every project (LANGUAGE-FILES, LANGUAGE-PROMPTS, LANGUAGE-OUTPUT).
- `ao doctor --check` reports to its caller and pages nobody; only the scheduled job, with `--notify`, pages, only for red findings, and a red condition with a known end is mailed once (QUIET-CHECK, #40).
- One condition rings one alarm whoever sees it: the doctor raises what the watchdog raises under the watchdog's own alarm (ALARM-DEDUPE, #106; NOTICE-NOISE).
- A standing condition - spent credits, an open decision, a failing wake, a report no architect will read - is told once for what it says, not on every cycle (NOTICE-NOISE, WAITING-ONE-ALARM, NOISE-REPEATS).
- The first watchdog cycle after a silence longer than `watchdog.resume_gap_hours` records what it would ring and tells a person once, instead of ringing every record on the age it reached (RESUME-QUIET).
- Messages are addressed to roles, never to an actor's name, so swapping an actor no longer leaves mail addressed to nobody (ROLE-TABLE, #31).
- The handoff note, the note a hold's release leaves and a person's message from Telegram are written through write_mail - an envelope naming the roles, the credential scan, the mail ledger - and `ao role swap` names two different roles (ROLE-TABLE-2).
- A returned review is handled before new work: `ao status` names each one until it is collected, the watchdog raises one left longer than `review.unhandled_minutes`, and the playbook has the implementer take it first (PIPELINE-HANDLE, #28).
- A review runs as sections, each answer kept before the next is asked, so a review that is cut off resumes where it stopped; a slice may declare the lenses it is reviewed through (REVIEW-SECTIONS, #26, #78).
- A reviewer is stopped for silence - no CPU for `review.stall_minutes` - rather than for thinking long, and what it said is kept (SILENCE-DEADLINE, #25).
- One walk of the reviewer chain has one time budget, and `ao doctor` states a review's worst case (CHAIN-BUDGET, #100).
- Review tiers: a reviewer of another model family stays the default; another model of the implementer's family reviews only after a person opts in on the record, and each such review is labeled `same family: weaker independence` ([docs/roles.md](docs/roles.md#review-tiers)) (REVIEW-TIERS).
- The reviewer reads a copy of the candidate's tree, unpacked with `git archive` into its own directory outside the repository, instead of a diff alone ([docs/pipeline.md](docs/pipeline.md)) (REVIEW-TREE).
- A review request separates the candidate that may land from the read-only context it is judged against, and a concern outside the candidate goes under Notes, where it cannot change the verdict (REVIEW-REQUEST, #97, #98).
- A review's commit-message claims and read-only context get the room its reviewer routes can carry (`review.context_bytes`), instead of what one argument leaves (REVIEW-BUDGET).
- A running review prints a line every minute naming the reviewer, the time elapsed and its pid, and one line saying how it ended (REVIEW-HEARTBEAT, #21).
- A message whose body cannot be read, or whose written time in the mail ledger is no number, stays among the unseen and climbs the ladder, classed by its name and aged by its file (UNREAD-AGE-2).
- A verification keeps each gate's exit code and closing line, `ao_report` appends them to the implementer's report, and a report that claims green over a failed verification is marked `## INCONSISTENT` and raised by the watchdog (REPORT-EVIDENCE, #6).
- READY is derived from a checked dependency graph: `needs:` and `unlocks:` must name board items, an unknown id, a duplicate or a cycle is a named problem, and an item `waiting:` on someone is not READY (READY-GRAPH, #33).
- A board row may point at a boundary file, `boundary: path@commit`, which the reviewer gets with its diff since that commit; a slice's declared paths are checked when it is registered (SLICE-BOUNDARY, #35, #73).
- Candidate size is measured by kind - product, tests, fixtures, generated, deletions - and passing the size guideline asks for a reason instead of refusing green work (SIZE-TRIPWIRE, #34).
- A gate may declare the paths it reads as `inputs`; once every gate does, a dirty path outside them and outside the candidate is listed as noise instead of withholding authority (ISOLATION-SCOPE, #16).
- `ao doctor` names each source tree no gate exercises, gate detection writes gates for every toolchain it finds, and `ao init` refuses quick gates that exercise none of them (GATE-COVERAGE, #5).
- `ao doctor` flags two critical roles on one quota pool with no fallback, and a READY queue below two with nothing running (2e3a7ab).
- Notices, logs and the progress record are held to `retention.observation_kb` as they are written, and an evidence ledger past its bound is sealed into `.ao/ledger/sealed/`, never truncated (BOUNDED-STORES, #50).
- `ao prune` archives review artefacts nothing rests on after `review.prune_after_days`, and keeps every artefact a grant, a verification, the board, a waiver or a decision names (REVIEW-RETENTION, #38).
- With `mail.store` set to `append-only`, every message is kept and `ao mail ack` records who handled it; the unhandled queue is derived instead of proved by deletion (MESSAGE-STORE, #80).
- `mail.sync_repo` syncs the message store to one private repository under `refs/mail/<project>`, and the product's own remote is refused (MAIL-SYNC, #83).
- A message records when it was first shown to its reader, and a decision request nobody has seen climbs the alarm ladder by age (UNREAD-AGE, #30).
- Urgent mail is shown to the role it is addressed to on the commands that role runs - `ao status`, `ao board`, `ao tail` and `ao mail` - not only on lock, verify and commit-ok (URGENT-BOTH-WAYS, #29).
- An open decision rings a person after `decisions.human_after_minutes`, and wakes deferred through an outage collapse into one instead of replaying in a burst (DECISION-LADDER, #20, #87).
- The architect is owed a wake until one that worked has the report, so a wake that died at once no longer takes the report with it (ESCALATE-DURABLE, #18, #23, #69).
- An item a board problem touches is not READY - an id on the board twice, an item that unlocks what is not on it - and a remark in `needs:` names no id however many words it has; `ao doctor` says when the board cannot be read as a graph (READY-GRAPH-2).
- A notice is held for the architect only when a wake is coming; otherwise it goes to a person and says why no architect will act (HELD-NOTICES, #92a).
- The playbook states what the implementer may settle without an architect: an open question parks its slice, the nudge names the next READY item, and the implementer stands down only when nothing is READY (AUTONOMY-ENVELOPE, #84).
- A project may name secondary projects: an implementer working in one is not nudged in the other, and with nothing READY here the nudge names READY work there (SECONDARY-PROJECT, #8, #22, #92).
- On a tool repository (`repository.kind` `tool`) roles may rotate per slice while the reviewer still never reviews its author; on a product repository the architect implements only a named hotfix (TANDEM, #13).
- With `keyflip.rotation` on, a spent provider window is rotated through keyflip before an actor starts, one rotation at a time (KEYFLIP-ROTATION, #32).
- `ao status` and `ao doctor` say where a checkout stands against the remote's default branch - ahead, behind, already merged, or `?` - and never show an ahead count alone (GIT-POSITION).
- `ao cost` and `ao credits` set the account's own figure beside ao's share and say whose each is, since an account counts every session on it (SPEND-LABELS, #95).
- `ao features` shows what each switch spent over the last seven days, measured instead of estimated (FEATURE-COSTS, #10).
- `ao watchdog status` prints the longest recent cycle and the longest silence between cycles, so a gap in the log can be explained (CYCLE-DURATION, #107).
- The watchdog stops nudging an implementer whose nudged turn answered with nothing to do, until the board, the backlog, a decision, its mail or the work moves (IDLE-ANSWER, #96).
- `ao init` writes no line into `CLAUDE.md` or `AGENTS.md` unless `--rules` asks: the playbook goes into files ao owns, and `ao doctor` reports `rules-not-wired` until the pointer is pasted (9c6c54e, c2996e9).
- Every time option takes one syntax - `30m`, `2h`, `1d`, `1w`, `today`, `yesterday` or a date, a bare number keeping the unit it always had - and ordinary mistakes end in one line: exit 2 naming the forms, 130 on Ctrl+C, a traceback only with `AO_DEBUG=1` (CLI-ROBUST).
- Colour reaches only a terminal, `NO_COLOR` and `TERM=dumb` turn it off, and `ao watch` into a pipe prints its panel once (CLI-ROBUST).
- `ao answer` takes only a key the question offers, or `x <text>` for free text, and refuses a second answer without `--change` (CLI-ROBUST).
- An MCP server started with `--role` lists and runs only the tools of that role's playbook, and `ao init` writes the role into each registration only one role reads (MCP-ROLES).
- `ao prove` reports a step of its throwaway slice that raised as a check that failed, with what would fix it, and names a `merge.link_paths` entry it could not link when the gates fail (PROVE-2).
- A boundary that lists criteria is judged criterion by criterion: the reviewer answers `CRITERION <n>: MET` or `NOT MET` for each, and `ao commit-ok` refuses while one is not met or was never judged (CRITERIA-VERDICTS).
- `ao reviews` says what a review in flight is doing - starting, preparing, waiting and on what, or which reviewer runs - and a run that stops on an error is recorded failed, where it read as lost (REVIEW-START-DELAY).
- An implementer's usage limit is read in the words its own adapter declares; its slice is parked until the reset and resumed then, where every nudge died on the limit until a person was told the agent was stuck (QUOTA-PARK).
- An architect wake stopped on its usage limit waits for that limit's reset, a weekly one included, and one notice says when wakes start again (ARCHITECT-WAKE-QUOTA).
- `ao verify` takes the machine gate lock once it knows the gates it will run, so a project with none, or an unknown profile, is told so at once (VERIFY-LOCK-LATE).
- A review's counts are never below the findings the reviewer listed outside its notes: an answer that counted HIGH 0 above a listed HIGH finding is NEEDS_CHANGES, and the artefact says which count its list raised (REVIEW-FINDINGS-COUNT).
- An answer whose verdict line or counts ao cannot read is asked for once more, naming the lines it missed, before the review is INVALID; the artefact says when a reviewer was asked twice (REVIEW-REASK).
- `ao doctor` reads each grant's rules as the commands they run, behind `timeout`, `nice`, `env`, `sudo` and the other programs that run what follows them, rtk's wrappers and git's global options: `Bash(git -C:*)` is named for admitting `git commit --no-verify`, and `Bash(timeout 60 pytest:*)` for admitting `pytest -p x` (ALLOWLIST-NORMALIZE).
- `ao harness probe [ID…] [--sessions]` asks each harness whose adapter declares an ACP command what it supports - `initialize`, and with `--sessions` how many sessions it lists here - and keeps the answer in `~/.ao/harness/<id>.json`; it sends no prompt and spends no quota. Kiro, OpenCode and Qoder declare the command they were seen to answer with (ACP-PROBE).
- A rule proposal's evidence names each rate's denominator and says when the review ledger could not be read, and a decision given at a terminal records the login and whether a terminal was attached (RULE-PROPOSALS-2).
- `ao.acp.Session` holds one ACP session with a harness and runs one prompt turn: the agent's message, its tool calls, and each permission it asks decided by a policy that allows nothing unless told otherwise; nothing drives a harness through it yet (ACP-CLIENT).
- `ao agent-hook EVENT` is what a harness's lifecycle hook runs: it records the event - the session, its directory, a tool's name - in the machine's event log, never a prompt or a tool's input, prints nothing and always exits 0; `ao agents` shows what each session is doing (HOOK-SPOOL).
- A slice parked on quota keeps the `needs:` it had before the park on its board line, and gets it back when the park ends, even one the watchdog's state lost (QUOTA-PARK-2).
- `ao uninstall` and `ao remove` take an MCP entry for ao's only when it runs `ao … mcp serve`, not whenever its command is named ao (UPDATE-UNINSTALL-2).
- An architect woken again and again by a weekly or monthly limit that names no reset waits twice as long each time, up to the period the stop names (ARCHITECT-WAKE-QUOTA-2).
- The playbook names `ao agents`, `ao agent-hook` and `ao harness probe`, as it names every command, and an ACP turn's result names its end without a transcript field's name (SUITE-GUARDS).

### Fixes

- `ao watch --web` and the A2A server listen at once where the machine's resolver is slow to name the loopback address, as on a hosted macOS runner: they no longer ask it (LOOPBACK-BIND).
- A candidate an APPROVED review is bound to stands on that review, where an open waiver for its slice was taken first and spent on it; `ao commit-check` holds a grant to the waiver it stood on; and `ao catchup` closes a range as having no net change only when it has none (WAIVER-BOUND-2).
- A child that re-enters ao - the review runner, the watchdog's hunt, the MCP server's verify - finds ao when ao runs from a clone with nothing installed; the review runner used to die silently and leave its review "running" until it was lost (LOOP-FIXES).
- A row store named by a path holding `?`, `#`, `%` or a UNC server opens as the file it names (SQLITE-URI, LOOP-FIXES).
- `ao verify` and `ao merge-check` wait for a machine gate lock held by their own project, as they did for another project's, so two suites no longer run at once in one checkout (GATE-LOCK-SAME-ROOT).
- `ao status`, the watchdog, `ao tail`, `ao cost` and `ao fleet` find the session right after `ao init --profile`: `session: auto` resolves to each role's own session and never another role's, and two sessions ao cannot tell apart are named ambiguous instead of guessed (SESSION-IDENTITY).
- A quota block ends at the reset its message named instead of renewing itself every cycle (QUOTA-WINDOW, #40).
- The credit check finds the harness CLI under the scheduler's short `PATH`, and says when it cannot read usage instead of skipping the reading silently (CREDITS-SAMPLER, #95a).
- A credit projection uses only the current account's samples, and a reset named with a date is read, so a week-long limit is not retried every few hours (GOLDEN-VALUES, #36, #41).
- Every credit reader - `ao credits`, `ao digest`, `ao handoff`, the sampler, the burn rate - reads the implementer's own adapter's account, never the first shipped adapter's (ACCOUNT-READERS).
- A returned review no longer changes the transcript age the watchdog's later checks read, a review record with no id is named by its file, and a review that ended with no verdict is told to be collected and submitted again (PIPELINE-HANDLE-2).
- Whether a notice may ring again is read from a record of each key's last times, so a window is counted whole however little the notices ledger still holds (NOTICE-WINDOW).
- One watchdog cycle runs at a time, and the hold is read again right before a wake, refill or nudge starts (CYCLE-SAFETY, #110).
- Architect presence is measured from the live process tree, so an interactive architect holds wakes back only while it is really there (#1).
- Human alerts keep the audience they were raised for, a red credit alarm reaches e-mail at once, and a dry cycle raises no alarm (f994ed9).
- Review rounds are counted per running slice rather than per HEAD (#2), and from the chained review ledger, so deleting or back-dating a review file no longer resets the round budget (ROUND-LEDGER, #65).
- `ao catchup` gives each waived review only its own slice's commits, instead of every later slice's (CATCHUP-RANGES, #104).
- A catch-up run no longer lets failing ranges hold the queue, starts no other review once the reviewer is unavailable, bounds only the reviews it starts with `--limit`, and exits 3 when its reviews decided nothing (OCT1-FIXES, CATCHUP-POLISH).
- `ao verify` no longer records the reviews directory's `.gitkeep` as the newest review (REVIEW-LISTING, #105).
- A gate's counts are read from the closing summary that ends last, where a `# pass` / `# fail` pair a test printed above pytest's own closing line was read as the run's result (GATE-SUMMARY-2).
- A reviewer that times out or is interrupted is stopped with its whole process group (REVIEWER-HONESTY, #65).
- A prompt too long for one argument reaches its CLI on standard input or in a private file where the adapter declares a way, and is otherwise refused before anything starts, instead of failing at spawn as an unreachable reviewer (REVIEWER-HONESTY, #65; PROMPT-CHANNEL).
- Reviewer discovery shares one deadline, and `ao init` probes the reviewer before it writes anything, leaving nothing behind when the probe fails (AO59b, AO59c, AO59d).
- The watchdog reaps a turn at the idle window once its transcript has closed it, and checks for foreign edits only while no turn runs (4b194a1).
- A process whose command line merely holds a path starting with an agent's name is not counted as that agent, and a runtime under a path with spaces still is (e0a43ac, 37a26a5).
- Processes are read through the platform's own interface - libproc and sysctl on macOS, `/proc` on Linux - instead of parsing `ps`, `pgrep` and `lsof` output (54401a7).
- A zombie process counts as dead, so a killed reviewer is not read as alive in a container whose first process never reaps (LINUX-LANE).
- A turn the watchdog spawned whose start could not be read is taken to be running by its pid for six hours at most, where a pid another process was given since kept the refill wake waiting for good (CYCLE-SAFETY-2).
- `ao remove` takes exactly one project's files, jobs and registry row - removing `proj` no longer deletes `bigproject`'s logs - and every job `ao watchdog install` schedules names a program that exists (SAFE-REMOVE).
- A commit hook falls back on the ao that installed it when `/bin/sh` finds none on `PATH`, and `ao hooks status`, `ao doctor` and `ao prove` print the symlink that fixes it (SAFE-REMOVE).
- Mail file names keep to letters, digits, dots, dashes and underscores, so a topic holding `/` no longer crashes `ao mail send` (SAFE-NAMES, #19).
- A process ao starts of itself - the review runner, the watchdog's hunt, the MCP server's verify - finds ao from a clone with nothing installed, instead of dying on "No module named ao" (SELF-REENTRY).
- A keyflip budget a person set now blocks a turn once it is breached: ao reads keyflip's own breach mark (SHELL-FREE).
- The quota reading is kept between processes for its five minutes, and a failed quota command is no longer cached as an empty reading (CYCLE-GIT).
- The report a present architect is told about is the implementer's own, not the watchdog's echo of it (977b73c).
- Stopping a submitted review's run stops the reviewer it started, which leads a session of its own and ran on for nobody; a run killed outright leaves its reviewer named for `ao reviews` and `ao review cancel` (REVIEWER-ORPHAN).
- The watchdog's state reads as a fresh one when its file holds `null`, a list or a string, where the MCP status tool and the watchdog's cycle stopped on it (STATE-SHAPE).

### Security

- A reviewer is known to run as the implementer when its command carries the implementer's session id as a word of a shell command, or - an id too long to be there by chance - anywhere in an argument, as in `sh -c "… --resume-id <id>"` or `-r<id>`; a harness store whose metadata holds no JSON object no longer stops `ao review` (REVIEWER-IDENTITY-2).
- A reviewer reads the candidate's own tree, unpacked outside the repository; a tar member is judged by where it lands, so `..\\x` cannot climb out on Windows, and a failed unpack leaves nothing half-written (REVIEW-TREE, REVIEW-TREE-2).
- An unattended grant admits the rtk-rewritten form of each command it names and nothing wider, and the allowlist check refuses a forbidden command in its rewritten form as it does the plain one (GRANTS-RTK).
- Commit authority is bound to the exact staged candidate - HEAD, the index's `git write-tree` and its status, digested - and verification, review and grant must all name that candidate ([ADR 0002](docs/adr/0002-immutable-index-candidate-authority.md); 56ea557).
- The tree digest covers untracked content and ignores staging, and `ao commit-ok` refuses an approval that names no tree and a grant it cannot persist (5b3cb26).
- Evidence ledgers are crash-safe JSONL - locked, fsynced, a torn tail repaired and never read as a row ([ADR 0001](docs/adr/0001-durable-jsonl-storage.md); 6e089cb) - and an append is all or nothing (LEDGER-ATOMIC, #68).
- The authority ledger is a hash chain (#3), and a ledger cut short or deleted is caught against a length record kept outside the repository, in `~/.ao/ledger-checkpoints.json` (LEDGER-LENGTH, #62).
- Hooks are installed where Git runs them: `core.hooksPath` and the common git directory are honoured, and a shared, external or global hook path needs `--allow-shared-hooks` (AO53, #53).
- A hook counts as installed only once Git has run it against a synthetic candidate and it returned ao's nonce-bound refusal; a stale, misplaced or fail-open hook reads as not installed (AO59a, #59).
- Enforcement is opted in by a tracked `.ao-project` marker and an incidental `.ao/` directory is inert (AO59a); `ao init` refuses a marker replaced during its reviewer probe, even one that copies its file metadata (AO59b; MARKER-FORGERY, #102).
- `ao doctor` says the merge ledger cannot be read, where a broken row took every recent merge for a checked one (MERGE-EVIDENCE-2).
- No shipped grant admits skipping or redirecting the commit hook, a push window, a gate waiver, removing the hooks, a command run through `ao lock` or an interpreter, and `ao doctor` names any configured actor whose grant does (ACTOR-GRANTS, #58).
- A harness granted every tool can still commit past the hook, so the watchdog checks every cycle for commits no grant covered and tells the architect once, and `ao doctor` says which guard holds for the configured implementer (ATTACK-SUITE, #109).
- `ao commit` compares the tree that landed with the tree the grant bound, and `ao doctor` reports commits no grant covered (LANDED-TREE, #64).
- A verification is bound to the digest of the gate definitions it ran, so a gate weakened after a passing run inherits nothing, and the verification ledger is chained (GATE-DEFINITIONS, #61).
- A gate's pass and fail counts are read only from the summary line its runner ends with, never from a log line the code under test printed (GATE-SUMMARY, #70).
- The newest recorded review of a candidate decides, from a chained review ledger: emptying, deleting or back-dating a rejection uncovers no older approval (REVIEW-SELECTION, #63).
- `ao review --timeout` is accepted and ignored: the timeout is the `review_timeout` setting, so the implementer cannot time out a reviewer that would reject (REVIEW-SELECTION, #63).
- A new project whose name the machine registry cannot record takes its path's own name, where two projects of one name both took the bare name and a push window allowed in one opened the other's (PROJECT-KEY-2).
- The reviewer's identity is read from ao's own evidence and compared as an identity, not as a substring (REVIEWER-IDENTITY, #60).
- A review artefact files the adjudicated verdict above the reviewer's words, kept verbatim; an APPROVED with blocker findings is filed as NEEDS_CHANGES, and `ao commit-ok` refuses it (REVIEW-VERBATIM, #54, #55, #57).
- A verdict is read from its anchored line, and verdicts and review statuses are closed sets the docs must match (AO25a; CLOSED-ENUMS, #4).
- A reviewer that runs as the implementer, declares its model family or runs its engine is refused, and an answer a person carried from a stand-in session is recorded as a fallback, which cannot supersede a rejection (REVIEWER-HONESTY, #65).
- Review routing may be declared as a versioned capability matrix in `.ao/config.json`: once present it fails closed, independence is judged from the declared bindings and model families, a fallback may follow an outage but never trade a rejection for an approval, and the matrix's digest is bound to the evidence ([docs/capability-matrix.md](docs/capability-matrix.md), [ADR 0003](docs/adr/0003-declarative-capability-routing.md); ed03184).
- An implementer's grant is asked whether it admits `ao config set` for each setting ao reads, not only `review_timeout`, so a grant to change its round budget or waiver limits is named by `ao doctor` (SETTINGS-2).
- A waived range is reviewed by a model family other than the one that wrote it, taken from the grant it landed under (CATCHUP-READY).
- A review waiver names one slice and a person, expires, and closes only on a recorded review of its own range (WAIVER-BOUND, #17, #67).
- The reviewer `ao init` writes starts no MCP server (`--strict-mcp-config` for Claude Code), and `ao doctor` names any reviewer or fallback that can reach beyond reading (REVIEWER-TOOLS, #24).
- Every Claude Code turn ao starts carries the permission mode the adapter pins, so a person's default mode cannot widen a nudge, a wake or a reviewer, and the architect's grant edits only `.ao/backlog.md`, `.ao/board.md` and `.ao/inbox/` (GRANTS-PINNED).
- A flag that turns a harness's approvals or sandbox off is declared in its adapter with the reason, and the watchdog nudges an adapter that needs one only after a person names it in `watchdog.bypass_adapters` (GRANTS-PINNED).
- What ao writes into the repository - review artefacts, mail, decision records, verification output - is scanned for credentials and redacted before a byte is written ([docs/safety.md](docs/safety.md)) (EVIDENCE-SCAN, #48).
- Nothing derived from a reviewer's output is persisted in reviewer state beyond closed values ao produced (AO59b, #99).
- The project config is written whole or not at all, so a crash cannot leave an empty config that silently drops an authority opt-in (CONFIG-DURABLE, #56).
- What ao writes into a repository is cleared of a secret assigned to the name an environment gives it - `DB_PASSWORD`, `CLIENT_SECRET`, `NPM_TOKEN`, `AWS_SECRET_ACCESS_KEY` - and of Slack's `xapp-` app-level tokens (EVIDENCE-SCAN-2).
- Everything a project keeps outside its tree is keyed by the project, so two checkouts with the same directory name no longer share a push window, watchdog state or scheduled jobs (PROJECT-KEY, #66).
- ao measures with a compiled git, never through a script or output filter standing in front of it, and records which git in `measured_by` (MEASURE-UNFILTERED, #51).
- `ao doctor` asks each shell-command filter in front of an agent's shell what it does to every measurement command - git diff, status and log, ao's own commands, the project's gates - and names each command it rewrites, instead of trusting its exclusion list; only a hook declared outside the project, of a program in `filters.probe_programs`, is ever run (FILTER-EXCLUSIONS, part of #52).
- No command reaches a shell unless it has to: `ao since 'HEAD;touch x'` no longer runs `touch`, and a project directory named with a space or `;` reaches launchctl as one argument (SHELL-FREE, CYCLE-GIT).
- A subagent path an adapter declares stays inside its session's directory: only plain names are accepted, a wildcard matches within one name, and a link that leaves the directory is not followed (SUBAGENT-BOUNDS).
- Adapters in the user's and the project's layers never decide authority: what does is read from the package's adapters only (HARNESS-STORES, #76).
- Ten named attacks on commit authority run with the suite, each failing closed (ATTACK-SUITE, #7, #90), and the watchdog's guard chain is judged in random worlds against its invariants (SCENARIO-FUZZ, #11).
- `SECURITY.md` says how to report a flaw privately and what ao does not claim to stop; `docs/privacy.md` lists what ao reads, keeps and sends. The suite runs with a home, PATH and environment of its own, and a test keeps every tracked file free of credentials and home paths (TRUST-HYGIENE, SECURITY-DOC-ACCURACY).
- `ao doctor` names a grant that trusts Kiro's shell tool through `--trust-tools` as one that runs every command, and asks each launch that carries a grant of its own what it admits, not the resume alone; `ao commit` says an empty message is empty (GRANTS-AUDIT-2).

### Windows

- A first cut: processes read through `Win32_Process`, process trees stopped with `taskkill /T`, and the watchdog and doctor jobs scheduled with Task Scheduler; [docs/windows.md](docs/windows.md) says what works and what does not (2e3a7ab).
- Every text file and subprocess is UTF-8, and binaries are found with `os.pathsep` and `PATHEXT` (af76348, f3472d3).
- What cannot be checked on Windows refuses and says so instead of degrading: the watchdog starts no turn beside an agent it cannot place, `ao hold` and `ao writers` exit 1 naming such agents, and a `.cmd` or `.bat` reviewer, a shared hook path and the telegram poller's jobs are refused (WINDOWS-FAIL-CLOSED, part of #71).
- The Claude Code transcript directory of a project on a drive path is found, and the doctor asks Task Scheduler rather than launchctl about the jobs (WINDOWS-FAIL-CLOSED, part of #71).
- `bin/ao.ps1` status takes the Kiro session whose workspace paths include the repository, counts only command lines that name an agent, and says UNKNOWN when the process table cannot be read (WINDOWS-FAIL-CLOSED, part of #71).
- A process's working directory is read from its environment block, so `ao hold` and the watchdog place an agent's turn in the tree (02b108c, part of #9).
- A desktop toast for what reaches a person, behind the `toast` feature switch, off by default (WINDOWS-TOAST, part of #9).
- `ao init` enrolls a project on Windows: the marker is written with LF, a CRLF checkout of it is accepted, and Python 3.12's changed `st_ctime` no longer reads as a replaced marker (WINDOWS-READY, WINDOWS-FOLLOWUPS, WINDOWS-LANE-3).
- Vendored skills keep their files and their pinned bytes, although Windows calls every file executable and converts line endings on checkout (WINDOWS-READY).
- Ids stay unique within Windows' coarse clock tick, a ledger append waits out a reader holding the checkpoint store, and a path written with backslashes counts as the write it is (WINDOWS-FOLLOWUPS, WINDOWS-LANE-3).
- The gate lock's liveness probe no longer sends its owner a signal (e4e6b7c), and a row store on a drive or UNC path opens (ef4ca79, 2b84483).
- Not done yet ([docs/windows.md](docs/windows.md)): the commit hook's execution proof fails closed on Windows until a new hook version reads a drive-letter index path, `ao hold` is still to be proven on the Windows lane, and ao needs `PYTHONUTF8=1` there to write a pipe in UTF-8 (part of #9, part of #71).
- Every entry point sets its standard streams to UTF-8 and every MCP reply is seven-bit JSON, so a default install no longer needs `PYTHONUTF8=1` (WINDOWS-CLOSE).
- The parent of a process is read from Toolhelp, so `ao lock -- ao verify` runs inside its parent's lock; where no parent can be read, verify says it cannot tell before it waits (ANCESTOR-WINDOWS).
- Whether a process runs is asked of the process itself, so a gate lock taken by a run started a moment ago is waited for, where a two-second-old snapshot read it as a dead run's and cleared it (WINDOWS-PID-ALIVE).
- The process backend stays Windows' own whatever its first reading gave, and a reading that lists no process is not kept, so processes are read after a first reading that failed; a file in the home is named `~/…` with slashes, and a lane another checkout started is known for one (WINDOWS-LANE-4).
- A tree ao stops on Windows is gone from the next reading of the process list, so `ao hold` no longer names the turn it has just stopped as one it could not place (WINDOWS-LANE-5).
- The commit hook reads an index path that starts with a drive letter, `C:/…` or `C:\…`, as absolute, where under Git for Windows' shell it put the working directory before one and ao refused, so the hook's execution proof can pass there; it is hook version 4, which each enrolled repository installs with `ao hooks install` (HOOK-V4).
- A batch program the watchdog starts on Windows, such as an agent CLI npm installs as a `.cmd`, is handed its prompt on standard input whatever its size where its adapter declares it, and is not started where it does not, so cmd.exe no longer ends the architect's wake at its first line (WINDOWS-CMD-CHANNEL).

### Adapters

- A reviewer can answer through ACP: with `ao config set review.transport acp`, a route whose adapter declares an ACP command and may review (kiro, qoder) reviews in an ACP session that may read and nothing else, and a turn that ran an edit is no review; spawning stays the default, and a route that cannot is spawned with a line saying why ([adapters.md](docs/adapters.md#what-a-harness-says-it-supports-asked-in-acp)) (ACP-REVIEWER, ACP-REVIEWER-2).
- Adapters load from three layers - the package, `~/.ao/adapters/`, then the project's `.ao/adapters/` - each overriding the one before by id; `ao adapters validate <id|file>` names what a candidate lacks and `ao adapters conform` runs the conformance checks (ADAPTER-LAYERS, #77).
- Eleven new adapters, all `untested`: qwen, grok, droid, kimi, kilocode, pi, hermes, omp and reasonix written from their own documentation (ADAPTER-SET, #89), trae (be3c724), and pr-agent (REVIEWER-TOOL, part of #86).
- `adapters/vendors.json` is the one vendor list: each shipped adapter is named by one vendor, a vendor without an adapter says why, and `ao doctor` names a configured actor whose command is not on the machine (ADAPTER-SET, #89).
- The core names no harness: setup, session stores, agent processes, accounts, provider windows and init profiles are read from what each adapter declares, and a test keeps harness names out of the core (HARNESS-SETUP, HARNESS-STORES, HARNESS-PROCESSES, HARNESS-ACCOUNTS, HARNESS-PROFILES, #76).
- Transcripts are read through the record shape each adapter declares - messages, turn start and end, tool calls, write tools, failures - so a new harness needs no code (TRANSCRIPT-SHAPE, #76).
- Every adapter declares whether it can run with no tools (`options.trust_none`), nine can, and a reviewer route is composed from its adapter, so any pair of harnesses can hold the two roles (REVIEWER-COMPOSE, #88).
- A reviewer route may be a tool ao runs over the exact staged diff on its own provider, outside any repository; `pip install 'ao-orchestrator[pr-agent]'` installs pr-agent where Python is 3.12 or newer (REVIEWER-TOOL, part of #86).
- Qoder is driven by what `qodercli` was measured to do - one turn per call, a session ao names and resumes, a prompt on standard input, a reviewer left only Read, Grep and Glob - so ao can wake a Qoder agent and review with one; the adapter is now `partial` (74f41cf).
- opencode's database transcript is read, so `ao status`, `ao tail`, `ao cost` and `ao doctor` watch an opencode session (a26695c), and a subagent session in that store keeps its parent at work (f3b5865).
- `ao adapters` and conformance tell a transcript ao can load from one that is only declared, marked `<kind>, not reached` (9b5e4e1).
- Claude Code: turns, tokens and turn ends are read from its nested records (SECOND-HARNESS-COST), messages, writes, spend and unattended turns as written (HARNESS-READINGS-2), and a subagent's spend, writes and activity count as its implementer's (SUBAGENT-SPEND, SUBAGENT-LIVENESS).
- Kiro: turns, writes and credits are counted the way its transcripts write them, so a prompt and its start marker are one turn, not two (TRANSCRIPT-READINGS).
- ECC's Kiro layer - steering and agent definitions - reaches a Kiro CLI user through `ao content add`, with no fork of its installer (KIRO-CONTENT, #15).

### Under the hood

- Test runs dispatched on one branch for different runners or interpreters all run, so the release checklist's runs on Python 3.9 and 3.12 no longer cancel each other (CI-DISPATCH-GROUP).
- Findings that needed no code change are held by tests or put right in the documents: the log an architect wake writes is one the log bound trims, each trace line the fuzz invariants read is one a cycle writes, a dead session the wake error does not name is not woken again, the Windows lane's fixtures name no person's home and write an early ao's hook as it wrote it, and a review run's first phase is read from its state (BOUNDED-STORES-2, SCENARIO-FUZZ-2, ESCALATE-DURABLE-2, HOME-FIXTURE, REVIEW-START-DELAY-2, HOOK-V4-2).
- `lib.py` and `cli.py` are split into `src/ao/parts/` as pure moves, each proven byte for byte by `ao split-check` (SPLIT-CHECK and fifteen move-only slices, #44).
- The suite runs on GitHub for every push to main and every pull request (Ubuntu, Python 3.9 and 3.12), on macOS and Windows every week, and a release tag runs it again before anything is published (CI-ON-GITHUB, CI-CONCURRENCY).
- The GitHub release is created only after the PyPI upload, so it never announces a version pip cannot install yet (CI-ON-GITHUB).
- The version is written once, in `src/ao/__init__.py`; `pyproject.toml` and `ao --version` read it (CLI-ROBUST).
- The dependency rule is policy in three tiers - the core on the standard library, optional extras, plugins outside the package - and a test holds the core to the first ([docs/upstream.md](docs/upstream.md)) (DEPENDENCY-TIERS, #82).
- The documents are held to the code: every `ao` command they show parses, and every MCP tool, `.ao` path and setting they name exists (DOCS-COMMANDS, DOCS-NAMES).
- Public files use neutral sample names and carry no machine-local path (NEUTRAL-NAMES, REDACT-LOCAL).
- Faster runs: adapters and settings are read once per run and status asks git without a shell (CLI-STARTUP), and macOS's git stub is skipped in ao and in the suite (SUITE-SPEED).
- The adversarial audit of 2026-09-08 (119 confirmed findings) is kept in [docs/audit/](docs/audit/2026-09-08-adversarial.md), and three architecture decision records in [docs/adr/](docs/adr/README.md).

## [0.4.0] - 2026-09-05

- Cost as a menu (`ao features`), a bypass on the record (`ao waive`, `ao catchup`), a credit burn-rate alarm, a dead man's ping (`ao pings`), the architect lock and the push hook; `ao cost` measures what the coordination spends (0fd2e6d).

## [0.3.0] - 2026-09-05

- The playbook ships with ao and `ao init` registers the MCP server and writes it; the watchdog's cycles are traced, alarms climb a ladder to e-mail, and `ao doctor --check` runs as a second scheduled job (4fbba07).

## [0.2.1] - 2026-09-05

- `ao writers` with orphan clearing, and the fan-out budget gate (`ao fanout`, `ao_fanout`) (323a709).

## [0.2.0] - 2026-09-05

- `ao digest`, decisions (`ao ask`, `ao decide`), `ao review` by an actor that did not write the code, the telegram phone channel, the A2A bridge, `ao init` and `ao since` (4a618e6).

## [0.1.1] - 2026-09-04

- The first published release, distributed as `ao-orchestrator` (da4d3ba).

## [0.1.0] - 2026-09-04

- The first release: attach to a coding agent already running, watch it, restart it when it stalls, run its gates independently, and decide from that evidence what may be committed (7fb15cf).

[Unreleased]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/hakkisagdic/agent-orchestrator/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/hakkisagdic/agent-orchestrator/releases/tag/v0.1.0
