# Configuration

ao decides nothing on your behalf that differs between people, projects or machines.
Each such value is a setting with a default, and `ao config` shows and changes them:

```
ao config list                               # every setting: value, default, where it came from
ao config get round_budget
ao config set round_budget 3                 # this project's .ao/config.json
ao config set quota.block_percent 90 --machine   # ~/.ao/settings.json, for every project here
ao config unset round_budget
```

A **project** setting is read from the project's `.ao/config.json`, then from the
machine's `~/.ao/settings.json`, then its default. A **machine** setting governs state
every project on the machine shares - alarm episodes, provider windows, heartbeats, the
event log, where binaries are found, how busy the machine may be when a suite starts - and is
read from `~/.ao/settings.json` only. Both files are written whole or not at all. A dotted name
is nested in the file: `alarms.red_after_minutes` is `{"alarms": {"red_after_minutes": 60}}`.

Project writes check the final UTF-8 bytes against the reader's 1,048,576-byte limit and
64 levels of container nesting before replacing the file. An oversized or over-nested change
leaves the old file intact. For both project and machine settings (`--machine`), if replacement
succeeds but syncing the directory fails, `ao config` says the file changed and durability is
unconfirmed; read it and retry after fixing storage. No project opt-in is recorded on that
failure (TRIAGE-3-LOW).

A value that is set but cannot be used - text where a number belongs, a number out of
range - is passed over for the next layer, and `ao doctor` names it with the value ao
uses instead. So does a name under one of these groups that ao does not read, and a
machine setting written into a project.

| Setting | Default | Scope | What it decides |
|---|---|---|---|
| `round_budget` | `5` | project | review rounds a slice may spend before the watchdog stops nudging and tells a person |
| `review.max_inflight` | `2` | project | submitted reviews that may run at once; a submit beyond it names what to collect |
| `review.unhandled_minutes` | `30` | project | minutes a returned review may wait uncollected before the watchdog raises it |
| `review.prune_after_days` | `30` | project | days after which ao prune moves a review artefact nothing rests on out of the repository |
| `review_timeout` | `900` | project | seconds one reviewer may take; never taken from the command line |
| `stall_minutes` | `60` | project | minutes a staged candidate may wait to land before throughput calls the slice stalled |
| `size.guideline_paths` | `5` | project | paths a candidate may change before its boundary must say why it is one slice |
| `size.guideline_product_lines` | `400` | project | product lines a candidate may change before its boundary must say why it is one slice |
| `size.refuse_product_lines` | `4000` | project | product lines above which no review is credible and ao review refuses before spawning |
| `size.small_overshoot_pct` | `25` | project | percent over the guideline small enough that verified code is never reshaped to meet it |
| `boundary.inline_max_chars` | `400` | project | characters of acceptance a board row may carry before its boundary belongs in a file |
| `merge.link_paths` | `['.venv', 'node_modules']` | project | untracked dependency directories a merge result borrows from the project to run its gates |
| `merge.check_days` | `7` | project | days back ao doctor looks for merges no passing run of their result vouches for |
| `mail.unseen_yellow_minutes` | `15` | project | minutes a needs-decision message may wait unseen before the architect is told |
| `mail.unseen_orange_minutes` | `60` | project | minutes unseen before a person's desktop and phone are told |
| `mail.unseen_red_minutes` | `240` | project | minutes unseen before it rings red and goes to e-mail |
| `retention.observation_kb` | `2048` | project | kilobytes an observation store (notices, progress, cycles, logs) keeps before its oldest records go |
| `retention.evidence_keep` | `500` | project | newest rows a chained evidence ledger keeps live when ao prune --evidence seals the rest |
| `repository.kind` | `product` | project | product: the architect does not implement beyond a named hotfix; tool: roles may rotate per slice |
| `review.lenses` | `declared` | project | declared: lenses only where a slice names them; auto: defaults from what the candidate touches; off: none |
| `review.stall_minutes` | `10` | project | minutes a reviewer may spend no CPU before it is killed as stalled, its partial answer kept; not on Windows, which has no process group to read the CPU from - the review says so, and its timeout ends a hung reviewer ([windows.md](windows.md)) |
| `review.context_bytes` | `100000` | project | bytes of commit-message claims and read-only context a review prompt may carry beside its diff, where no reviewer route holds it to one argument |
| `review.transport` | `spawn` | project | spawn: each reviewer route runs as its command; acp: a route whose adapter's ACP command was measured to hold a reviewer to reads answers through ACP, and any other is spawned with a line saying why; a change is the reviewer's, held until a person accepts it with `ao harness seen` ([adapters.md](adapters.md#what-a-harness-says-it-supports-asked-in-acp)) |
| `review.same_family` | `refused` | project | refused: a reviewer of the implementer's model family is refused; labeled: another model of that family may review, labeled weaker independence, once a person opts in with --by, on the record |
| `mode` | `full` | project | full: independent review, commit hooks and grant-checked `ao commit` enforced; lite: basic setup without enforcement (`enforcement: off - lite` — no reviewer probe, no commit hooks, `ao commit` refused), once a person opts in with --by, on the record ([safety.md](safety.md)) |
| `hunter.every_hours` | `24` | project | hours between bug hunts the watchdog starts, when the hunter feature is on |
| `hunter.files_per_run` | `8` | project | tracked files one hunt reads, going round the tree run by run |
| `hunter.bytes_per_run` | `60000` | project | bytes of source one hunt reads |
| `hunter.max_file_kb` | `512` | project | kilobytes of a tracked file past which no hunt reads it: a lock file, a bundle, a data set |
| `hunter.max_leads` | `5` | project | new leads one hunt may send |
| `hunter.argv` | `none` | project | the hunter's command with {prompt}, unable to write; none: no hunt runs |
| `hunter.id` | `hunter` | project | the name a hunt's runs and leads are recorded under |
| `pr.watch` | `off` | project | on: ao pr watch --once reads this checkout's pull requests through gh and mails the implementer what needs it; off: it reads nothing |
| `mail.store` | `deletion` | project | deletion: handled mail is deleted; append-only: messages are kept and handling is a record |
| `mail.sync_repo` | `none` | project | the one private repository the message store is pushed to as refs/mail/<project>: a URL, a path or a remote's name, judged by the URL it pushes to; none: not synced |
| `gates.default_timeout` | `600` | project | seconds a gate may run when its own definition names no timeout, at most a week |
| `gates.coverage_min_files` | `5` | project | source files of one toolchain a top-level tree must hold before a gate must exercise it |
| `watchdog.idle_minutes` | `6.0` | project | minutes of implementer silence before the watchdog acts |
| `watchdog.max_attempts` | `3` | project | nudges without progress before a person is told the implementer is stuck |
| `watchdog.resume_gap_hours` | `2` | project | hours without a watchdog cycle after which the next one is a resume: one notice names what stands and its clocks restart |
| `watchdog.quota_retry_hours` | `5` | project | hours a slice parked on a usage limit that named no reset waits before one nudge tries again |
| `decisions.human_after_minutes` | `15` | project | minutes an open decision waits before it rings a person |
| `waivers.default_hours` | `24` | project | hours a review waiver stays open when --hours is not given |
| `waivers.max_hours` | `168` | project | the longest a review waiver may be granted for |
| `alarms.red_after_minutes` | `60` | project | minutes an orange alarm stands before it turns red and sends one e-mail |
| `fanout.max_agents` | `12` | project | the most agents one fan-out may start |
| `fanout.per_agent_tokens` | `50000` | project | the token budget each fanned-out agent is given |
| `fanout.window_reserve_pct` | `30` | project | percent of the provider window a fan-out must leave unused |
| `implementer.name` | `none` | project | the implementer's name in mail file names; none: its adapter's actor name |
| `architect.name` | `architect` | project | the architect's name in mail file names: its role's, never a model's (#72) |
| `language` | `en` | project | en: the files ao init writes and the markers ao writes into mail are English; tr: Turkish. The markers of both are read in every project |
| `lane.link_paths` | `none` | project | paths ao lane start links into a new lane from the main checkout, such as untracked dependency directories; none: nothing is linked |
| `lane.env` | `none` | project | NAME=value lines ao lane start writes into a new lane's lane.env_file and gives its post-create command; {item}, {lane} and {path} stand for the lane's own |
| `lane.env_file` | `.env` | project | the file at the top of a new lane that lane.env is written to; never one the lane already has |
| `lane.post_create` | `none` | project | the command a new lane runs before it is reported ready, as an argument list and never shell text; {item}, {lane} and {path} stand for the lane's own; none: nothing runs |
| `lane.post_create_timeout` | `600` | project | seconds a new lane's post-create command may run before the lane is reported not ready |
| `limits.max_write_lanes` | `2` | project | lanes ao lane start makes in this checkout, in any state, before it refuses another until one is removed |
| `keyflip.rotation` | `off` | machine | on: before an actor starts on a spent window, keyflip rotates the machine's account; off: never |
| `alarms.red_repeat_hours` | `6` | machine | hours before a red alarm that still stands e-mails again |
| `alarms.reset_after_hours` | `2` | machine | hours of quiet after which an alarm episode is over |
| `quota.block_percent` | `97` | machine | a provider window used at or above this percent stops a wake or a nudge |
| `architect.quota_window_hours` | `5` | machine | the architect's usage window, unless its limit names a week or a month; a reset named further away is not the one meant |
| `heartbeat.retired_days` | `7` | machine | days of watchdog silence after which a project counts as retired, not dead |
| `fleet.window_reserve_pct` | `20` | machine | percent of the machine's provider window kept free before a report wake |
| `limits.refuse_above_swapins_per_sec` | `1500` | machine | swap-ins a second - the lower median of four one-second samples - above which a gate run and ao lock wait for the machine, then refuse, and a lane start refuses; swap used is never read |
| `limits.refuse_below_free_memory_pct` | `15` | machine | percent of memory free - memory_pressure's figure on macOS, MemAvailable on Linux - below which a gate run and ao lock wait for the machine, then refuse, and a lane start refuses; 0: never |
| `limits.refuse_below_free_disk_gib` | `8` | machine | GiB free on the volume that holds the home, never /, below which a gate run and ao lock wait for the machine, then refuse, and a lane start refuses; 0: never |
| `binaries.extra_dirs` | `none` | machine | directories searched for agent binaries after PATH, before the usual install locations; a relative one is not searched |
| `watchdog.bypass_adapters` | `none` | machine | adapters whose implementer the watchdog may nudge with the flag that turns off their own sandbox; none: such a nudge is refused |
| `review.api_providers` | `none` | machine | OpenAI-compatible providers the API reviewer may ask, each `<word> <base URL> <variable holding its key>`; a reviewer names one as `<word>/<model>`; none: no provider |
| `filters.probe_programs` | `['rtk']` | machine | programs ao doctor may run as a user-level shell-command filter hook, to prove it leaves measurements alone |
| `hooks.probe_timeout_seconds` | `60` | machine | seconds Git may take to run ao's pre-commit hook on the synthetic candidate that proves it is installed |
| `filters.probe_timeout_seconds` | `5` | machine | seconds a probed filter hook may take to answer for one measurement command before ao stops asking it |
| `retention.events_kb` | `2048` | machine | kilobytes the machine's event log, ~/.ao/events.jsonl, keeps before its oldest lines go |

The scheduled watchdog reads `watchdog.idle_minutes` when it is installed; run
`ao watchdog install` again after changing it. Everything else is read when it is used.

`language` decides the words ao writes into a repository: the files `ao init` writes - the
authority, board and backlog under `.ao/` and `agent-mail/README.md` - and the markers ao writes
into mail, such as `## URGENT` and `## DECISION REQUIRED` in English and `## ACİL` and
`## KARAR GEREKLİ` in Turkish ([protocol.md](protocol.md#markers) lists them all).

It decides what ao writes for a person as well: `ao digest`, the note `ao handoff` writes and sends,
the notes `ao decide` and `ao hold release --note` leave, a decision's free-text option, the watchdog's
lines to the phone, its architect-at-quota, parked-on-quota and failed-wake alarms, the lines a red
alarm's mail adds, and every reply the phone is sent. `ao email setup` and `ao telegram setup` set up a
channel the whole machine shares and print their steps in the machine's choice. What a person types on
the phone - a command, an answer - is read the same in either language, and an alarm is keyed the same
in both, so a change of language rings no standing alarm again.

```
ao config set language tr --machine          # every project on this machine that sets none
ao config set language tr                    # one project, in the .ao/config.json ao init wrote
ao init --language tr                        # a new project, before init writes a file
```

`ao init` writes its files in the language in force when it runs, and a project has no config of
its own until then: `ao init --language tr` sets the project's choice in the config it writes, before
any other file, and a machine's choice set first serves as well.
`ao init` never rewrites a file that exists, so a project keeps the files it was given, and a
change of language changes what ao writes from then on. The markers of both languages are read in
every project whatever it chose: mail written before a change, or by an agent following an older
playbook, is still found, surfaced and escalated.

`language` decides the instructions ao gives agents as well: the reviewer's prompt, the headings it
asks for and the markers between its parts, a stand-in review request, the bug hunter's prompt, and
the watchdog's nudge, wake and refill. A project that chose Turkish is given the Turkish texts it
always was, byte for byte, and what ao reads back from an answer - a verdict with its counts and
findings, a lead, a nonce - is spelled the same in both ([pipeline.md](pipeline.md)).

`review.context_bytes` is what a waived range's commit messages and a test-only candidate's
read-only context share in a review prompt, and they never take more than the 400 KB diff budget
leaves beside the diff. A prompt so bounded costs at most what a largest diff does, some 100 to 130
thousand tokens, which leaves a 200-thousand-token window room for the harness's own instructions and
the answer. 100000 is the context a review could already carry on its own; the claims share it rather
than add to it, so no call asks more of a reviewer's window than one did before, and a sectioned review
pays it once per question. Every route of the reviewer chain is handed the same prompt, built to the
least a route that may run can carry. A route whose command would not fit with the claims and context
and whose adapter declares no other channel for its prompt - and on Windows any route that takes its
prompt in its argument - holds them to one argument's worth: 120 KB with the diff, 30 KB on Windows
([adapters.md](adapters.md)).

The `lane.*` settings prepare each worktree `ao lane start` makes ([parallel.md](parallel.md)), in
this order: `lane.link_paths` are linked from the main checkout, `lane.env` is written into
`lane.env_file`, and `lane.post_create` runs in the lane with those variables, what it prints kept
in `.ao/lanes/<lane>.log`. A lane is reported ready only when every step passed, and a `lane.*` value
ao cannot use - one string where a list belongs, `"npm ci"` for `["npm", "ci"]` - refuses the start
before anything is made. A list is set with commas - `ao config set lane.post_create npm,ci` - and a
word that holds a comma is written into `.ao/config.json` itself. `lane.post_create` runs what the
project's own config names, as a gate in `.ao/gates.json` does, so read it before starting a lane in
a project someone else set up.

The two filter settings are machine settings on purpose: they decide which programs
`ao doctor` may run, unattended, and a project's own files are writable by the agents it
governs ([gates.md](gates.md#a-filters-exclusions-are-proved-not-trusted)).

The three limits that hold the machine are machine settings for the same reason: a gate run,
`ao lock` and a lane start read memory, paging and disk before they start, no flag skips the
reading, and a value written into a project's `.ao/config.json` is not used
([gates.md](gates.md#serialisation-and-machine-pressure)). A person who wants a limit looser,
or none at all - a ceiling no machine reaches, a floor of 0 - changes it for the machine:

```
ao config set limits.refuse_above_swapins_per_sec 3000 --machine
ao config set limits.refuse_below_free_disk_gib 4 --machine
```

`limits.max_write_lanes` is the project's own: how many lanes `ao lane start` keeps in one
checkout, a failed one among them until `ao lane remove` retires it ([parallel.md](parallel.md)).

`review.same_family` weakens a guarantee, so it is a person's act on one project, on the
record, as a waiver is. `ao config set review.same_family labeled --by <name>` (and `unset`)
needs `--by` naming a person, never an agent or a role, refuses `--machine`, and appends the
name, the login and whether a terminal was attached to `.ao/ledger/opt-ins.jsonl`. The
setting is in force only while the project's own `.ao/config.json` reads `labeled` and the
newest record says the same: a value written into the file by hand - which an agent that edits
files can do - or into the machine's settings is not, and `ao doctor` names it. A record is one
ao appended on this machine, as its checkpoint store shows: a row written into the ledger by hand
is nobody's, and ao appends nothing after one. What each value
means is in [review tiers](roles.md#review-tiers); `ao init --review-tier same-family --by <name>`
opts a new project in.

`mode` (`full` or `lite`) is a project setting recorded in the same way, switched with `ao mode`
(`ao config set` and `unset` refuse `mode` and name `ao mode lite --by <name>` / `ao mode full`).
Switching a project to `lite` weakens enforcement, so `ao mode lite --by <name>` requires `--by`
naming a person, never an agent or a role, removes AO's own installed hooks, and records the opt-in
in `.ao/ledger/opt-ins.jsonl`. A `"mode": "lite"` written into `.ao/config.json` without a matching
opt-in record stays in `full` mode and is named by `ao doctor`, `ao status` and `ao mode show`; if
the config leaves `lite` while the newest attested `mode` row is `lite`, reading the mode appends
an attested `full` row by `ao` so writing `"mode": "lite"` back later stays `full` until a person
records `ao mode lite --by <name>` again. `ao mode full` checks what `full` needs - a reviewer
route that passes the independence check and probe (or the `person` review tier), and commit hooks
that install and pass the execution probe - records `full` before writing the config, and refuses,
naming what is missing, rather than half-switching; `ao mode show` prints the mode and what each
mode does and does not protect ([safety.md](safety.md)).

An implementer whose tool grant admits `ao config set` or `ao config unset` could lengthen or shorten what
governs its own review, and `ao doctor` names such a grant even when it fixes a value or includes
`--machine`. The machine's `implementer` and `architect` objects are settings groups: their `name`
keys are recognized, and unknown keys are named. Project role objects also hold adapter and command
fields, which are not settings. Credentials - the e-mail relay
token, the Telegram bot token - are not settings; they stay in their own files under
`~/.ao`, outside any repository.
