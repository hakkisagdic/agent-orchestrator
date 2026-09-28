# Privacy: what ao reads, keeps and sends

ao runs as you, on your machine, beside the agents it governs. What it knows it reads from files and
processes there, and what it keeps it writes there. It opens no network connection of its own
accord: each connection on this page belongs to a feature, and runs only once that feature is set
up, the account lookup by choosing an implementer whose adapter declares one and every other by a
command or a setting someone gave. There is no telemetry, no usage report, no update check and no
account.

*In ao since slice TRUST-HYGIENE: this page and the [security policy](../SECURITY.md). ao's own
repository holds no credential and no home directory of the machine it was written on, and a test
reads every tracked file to keep it so; ao's test suite runs with a temporary home and PATH and none
of your `AO_` variables but the two fuzz seeds, so running it reads none of your `~/.ao` or the files
those variables name and messages none of your channels ([SECURITY.md](../SECURITY.md#this-repository)).*

*In ao since slice SECURITY-DOC-ACCURACY: this page also names what the slices after TRUST-HYGIENE added: the reads
`ao pr watch` asks of GitHub, the requests of the tool `ao update` runs, the machine's event log, the
systemd units on Linux and the web view on 127.0.0.1.*

## What ao reads

- **The project.** Its git state, asked of git itself and never through an agent's terminal; the
  `.ao/` directory, the mailbox and the review artefacts; the files a gate or a review is about; and
  the agents' configuration in it (`.claude/`, `.kiro/`, `AGENTS.md`), which `ao doctor` reads for
  credentials and for grants that admit everything.
- **The agents' own stores, read-only.** Each adapter names where its harness keeps sessions and
  transcripts - under `~/.claude/projects/` for Claude Code, in a SQLite store for Kiro - and ao reads
  them to know what an agent is doing, what a turn cost and how full its context is
  ([adapters.md](adapters.md), [telemetry.md](telemetry.md)). A SQLite store is opened read-only, and ao
  writes to none of them.
- **The process table**, to know which agent runs in which directory, so a second writer never starts
  beside a first.
- **A harness's login, for one request.** Where the implementer's adapter declares an account lookup,
  ao reads the access token that harness's CLI already keeps on this machine and uses it for the
  request described under [What leaves the machine](#what-leaves-the-machine). The token is never
  written, logged or shown.
- **What keyflip says**, when it is installed: quota windows, budgets and which accounts exist
  (`keyflip usage --providers`, `keyflip budget status --json`, `keyflip surfaces`). keyflip never hands
  ao a secret ([keyflip.md](keyflip.md)).

## What ao writes

**In the project:**

- `.ao/` - the configuration, the board and backlog, the gate definitions, and the ledgers under
  `.ao/ledger/`: decisions, verifications, commit grants and refusals, reviews, waivers, notices and
  the mail log ([ledger.md](ledger.md)).
- `agent-mail/` - the mailbox; `ao init` keeps the messages out of git.
- `semantic-review/` - the review artefacts: what each reviewer answered about a candidate.
- `.ao-project`, the marker that enrols the project, and the git hooks ao installs.
- Where a person asks for it: the playbook for the agents (`ao skill install`), the pointer to it in
  their rules (`ao init --rules`), ao's MCP server in the file each detected agent's adapter names in
  the project (`ao init`, merged beside the servers already there), and the refs `refs/ao/backup/latest`
  and `refs/ao/mail` for a backup and the mail store.

A product repository commits its ledgers and reviews: they are evidence, and they travel with its
history. So what ao writes into a repository is scanned for credential shapes first, and a hit is
written as `[redacted:<rule>]` ([safety.md](safety.md#7b-what-ao-writes-is-scanned-before-it-is-written)).

**On the machine, in `~/.ao`, outside every repository:**

- `~/.ao/settings.json` and `~/.ao/projects.json` - the machine's settings, and the projects on it by
  name and path.
- `~/.ao/email.json`, `~/.ao/telegram.json` and `~/.ao/pings.json` - the channels' credentials and
  addresses. ao writes the first and the last readable by you alone (mode 0600); the Telegram file is
  the one you write yourself, and [telegram.md](telegram.md) asks the same mode of it.
  `~/.ao/telegram-offset` is the poller's place in the messages.
- For each project, files named after it: the watchdog's state and log and its recorded cycles
  (`~/.ao/watchdog-<project>.json`, `~/.ao/cycles-<project>.jsonl`), its heartbeat, and the logs of the
  turns it started (`~/.ao/nudge-<project>.log`, `~/.ao/escalate-<project>.log`). Those logs hold what
  the agent CLIs printed, model output included; they stay on the machine, and what a notice quotes
  from one is masked for anything shaped like a token first.
- `~/.ao/events.jsonl` - one line each time ao records a verification, submits a review or sees one
  end, grants commit authority, sends mail, or nudges or wakes an agent, for `ao events` to read; its
  oldest lines go past `retention.events_kb` ([surfaces.md](surfaces.md)).
- For the machine: the alarms (`~/.ao/alarms.json`), the gate lock (`~/.ao/gate.lock`), the quota and
  binary-version caches (`~/.ao/quota.json`, `~/.ao/binaries.json`), the ledgers' recorded lengths
  (`~/.ao/ledger-checkpoints.json`), pruned records under `~/.ao/archive/<project>/`, and the adapters
  a person adds, under `~/.ao/adapters/`.

**Scheduled jobs:** `ao watchdog install` and `ao telegram install` add launchd jobs on macOS, whose
files sit in `~/Library/LaunchAgents`; on Linux the watchdog and the doctor are systemd user units, in
`~/.config/systemd/user`; on Windows the watchdog is a Task Scheduler task. `ao uninstall` takes off
every one of them that is ao's ([getting-started.md](getting-started.md)).

The stores are bounded (`retention.observation_kb`, `retention.evidence_keep`, `ao prune`), and
`ao remove --yes` takes a project's state, hooks, scheduled jobs and own files in `~/.ao` off again,
in two steps whose dry run names each ([watchdog.md](watchdog.md)).

## What leaves the machine

Only these, each once someone has set it up; take the setup away and it stops.

| Feature | Set up with | Talks to | What goes out |
|---|---|---|---|
| E-mail, the red alarm | `ao email setup` | formsubmit.co, or your own SMTP server | an alarm's title and text, and the project's name ([alarms.md](alarms.md)) |
| Telegram | `ao telegram setup`, and `ao telegram install` for the poller | Telegram's bot API | alarms and notices, blocked reports, the questions `ao ask` poses, `ao handoff`'s summary, and the answers to `/status`, `/board`, `/credits`, `/notices`, `/fleet` and `/decisions` as those commands print them; the poller fetches the messages sent from the chats you allowed ([telegram.md](telegram.md)) |
| Dead man's switch | `ao pings setup --url …` | the address you give it | one request a watchdog or doctor cycle, carrying nothing ([alarms.md](alarms.md)) |
| A backup or the mail store on a remote | `ao backup --to remote:<name>`, `mail.sync_repo` | GitHub, asked through your `gh` whether the repository is private, then that remote through git | the governance files, or the mail records, scanned for credentials first; nothing unless the host says the repository is private. With `mail.sync_repo` set, each `ao doctor` run also asks the remote how far its copy is, and GitHub whether it is still private ([recovery.md](recovery.md), [messaging.md](messaging.md)) |
| The account lookup | an implementer whose adapter declares one; Kiro's does | that harness's provider | a request for the account's usage, with the token its CLI already holds, from `ao credits`, `ao cost`, `ao digest`, `ao handoff` and the watchdog every thirty minutes; `ao credits --offline` skips it ([telemetry.md](telemetry.md)) |
| Borrowed harness content | `ao content add` | the source you name, through git | a fetch of the one commit it is pinned to; `ao content verify` reads only the files on disk |
| Pull requests | `ao config set pr.watch on`, then `ao pr watch --once` | GitHub, asked through your `gh` | two reads a pass: `gh pr list` for this checkout's open pull requests, and `gh pr view` for each of this project's; nothing is written to GitHub ([pr.md](pr.md)) |
| Updating ao | `ao update`, which prints the command it will run and asks first | the clone's git remote, or the source of the tool that installed ao: Homebrew, pipx, uv or pip | that tool's own requests for the newer ao; ao adds nothing of its own ([getting-started.md](getting-started.md)) |
| A2A | `ao a2a serve`, `ao a2a-mcp serve` | 127.0.0.1 only; the agents in `.ao/a2a-agents.json` or `~/.ao/a2a-agents.json` | the board, to anything on this machine; the messages you send, to the agents you registered ([mcp.md](mcp.md)) |

`gh` is asked only these reads - whether a repository is private, and with `pr.watch` on the open
pull requests - the two refs above are all ao ever pushes, and ao never grants `push` to an agent
([safety.md](safety.md)). `ao watch --web` serves its pages on 127.0.0.1 alone and answers only a
request addressed to it there, so nothing of it leaves the machine, though anyone logged in to the
machine can read it ([surfaces.md](surfaces.md)). When keyflip is installed ao asks it for
quota windows and budgets, and what keyflip contacts to answer is keyflip's to say
([keyflip.md](keyflip.md)). The MCP server speaks to the agent that started it over stdin and stdout,
and a desktop notification or a Windows toast stays on the desktop.

## The agents ao starts

ao starts the agent CLIs a project configured - a nudge to the implementer, a wake to the architect,
a review, a bug hunt - and hands each its prompt: the nudge, the reports to read, the candidate's diff
and the files around it. Each CLI sends what it is given to its own provider, under that provider's
terms; ao opens no connection for it, and what a provider does with a prompt is outside what ao
governs ([safety.md](safety.md#9-what-this-model-does-not-cover)). The switches that spend a model's
quota, which are the ones that start these turns, are in [features.md](features.md).
