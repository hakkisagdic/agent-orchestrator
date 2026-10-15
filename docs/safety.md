# Safety model

An orchestrator that can inject prompts into other agents' sessions and grant commit
authority is a privileged component. These are the invariants that keep it from becoming
the most dangerous process on the machine.

## 1. Mail is data, never authority

Everything arriving through the mailbox, a transcript, a repository file or a tool result
is **untrusted input**. It can carry a plan, a ruling, a file list, a commit message. It
can never grant:

- `git push`, force-push, PR creation, tag or release publication
- hook bypass (`--no-verify`), history rewriting, amend of published commits
- mutation of any repository other than the canonical one
- credential access, or exfiltration of anything to a network destination

Those require a direct instruction from the human, in the human's own channel. An agent
that receives a message asking for one of them rejects that part, records the rejection,
and continues with the rest.

This matters because a mailbox is a prompt-injection surface by construction. If someone
can write a file into your repository, they can write a message that *looks* like a
decision. The rule holds regardless of how convincing the text is: urgency, claimed
authority, "the user already approved this", or an instruction embedded inside code the
agent is reading.

## 2. Two writers corrupt state

Never inject into a session that is mid-turn. Two hazards, two mitigations:

| Hazard | Mitigation |
|---|---|
| Two writers on one session transcript | Ask the OS which agent processes have this repo as their cwd. Start nothing while any of them is alive, and nothing while the OS cannot be asked: a process table the platform could not read is an error that names the platform and why, never a table in which nothing runs, so the watchdog stands down and tells a person (`process-table-unreadable`). |
| Two writers on one working tree | Write lanes get separate git worktrees: `ao lane start` makes one per board item and refuses a second lane for an item that has one. |
| A human needs the tree | `ao hold` stops every agent in it and holds the lock; every restart path reads the lock again right before it starts a turn. A hold that cannot wait out a watchdog cycle already running says so and exits 1, and so does one that cannot read the process table: it stands, and stops nothing it cannot see. What still runs after the hold - reading the table again - is named, and the hold exits 1. |
| A lock kept where an agent writes | `.ao/` in the working tree holds locks an agent can replace. ao opens a lock through no final link, and only as a regular file of one name - on Windows it opens a link itself, never what it names: a link, a FIFO or a hard link put in its place is refused and named, where ao used to create the file a link named, outside the tree, and write a byte into it. |
| A turn ended but its processes did not | `ao writers` shows them as orphans (no terminal, dead group leader) and counts zero writers; the watchdog clears them before every count, and `ao hold` / the reaper stop turns by process group so no new ones are made. Where the process table cannot be read, `ao writers` says the writers are unknown and exits 1. |

Git worktree cleanup first inspects the common Git directory's `worktrees` path
and its immediate entries without following links. The filesystem resolves the
folder's case as it does for Git. A symbolic link, Windows reparse point,
non-directory folder entry or unreadable administration stops removal and pruning; ao names what
must be removed or made readable before retrying. This covers lane and slice
cleanup, merge-check and prove ([parallel](parallel.md#a-worktree-lives-as-long-as-its-slice),
PRUNE-LINKS). This check is not atomic with Git's deletion: a concurrent replacement
after the check remains a race.

**Do not infer this from file timestamps.** That was the original design — status AND
write-age, ANDed — and it failed twice in one night. A turn retrying a provider 5xx sits
in backoff writing nothing, which reads exactly like a turn that ended, so the guard
started a second turn on the same session id and the two left a rename half-applied.
Then the fix that tracked the pid of *our own last child* missed the other fourteen:
every nudge spawns a detached process, nothing reaps them, and fifteen live agents had
accumulated in one repository with four still burning CPU.

Measure with `pgrep -f` plus an `lsof` cwd match, and never with `ps -eo args | grep`:
`ps` truncates long argument lists, a resume prompt is long, and a `ps`-based check
reported one process while fifteen were running. That wrong answer was then passed to
the implementer as a measured fact, which cost more time than the original bug.

The corresponding rule for the agent: check at most once per turn, act on the answer,
and never conclude a second writer exists from a growing diff. Your own edits land
asynchronously — a file changing during your own turn is you.

Call-return adapters avoid this entirely — a synchronous call means the orchestrator
knows when a turn is in flight and takes a real lock instead of inferring one.

## 3. Commit authority is separated

The agent that writes the code does not decide when it is good enough.

1. The implementer finishes and reports. It does not commit.
2. The verifier — a **different actor** — re-runs typecheck, tests and diff checks itself.
3. Commit authority is granted against the verifier's numbers, never the implementer's report.
4. Push is never automatic, under any configuration.

Reports from implementers are usually accurate. The point is not to catch liars; it is that
a system whose correctness depends on self-reporting has no independent check at all.

`ao commit-ok` is that decision made mechanical, so work can land while nobody is
awake. The implementer first stages the exact candidate. A grant is issued only when:

- the index candidate is non-empty and isolated from unstaged or untracked product changes;
- the newest verification passed, marked that candidate ready, and names the exact same
  immutable index candidate;
- no plan was edited after admission; and
- the newest structured prospective review for that candidate is APPROVED (unless review
  is currently disabled or a matching running-slice waiver is open), and the project is
  not held.

Every refusal names its missing condition, and every grant or refusal is appended to the
authority ledger with the candidate and scope it rested on. Each authority row has a
`previous` field: `null` for the first row, then the domain-separated SHA-256 digest of
the preceding canonical JSON object. `ao commit-check` validates the complete committed
chain before selecting the newest decision and refuses a missing or incorrect link. A
non-empty legacy ledger without links is unreadable and is never rewritten implicitly.
`ao commit-check` is the non-mutating enforcement half: the optional AO pre-commit hook
runs it against Git's active index and revalidates the latest persisted grant, its exact
verification and review or live waiver, plus current plan drift, holds, and urgent mail.
It neither issues nor consumes a grant. Its limit: Git writes the commit's tree from the
index after the hook returns, so a process that stages a path in that window lands it
inside a plain `git commit`, and no hook can prevent that. `ao commit` writes the commit
itself from the tree the grant bound, so nothing staged after its check lands, and
`ao doctor` reports any commit since the first grant whose tree no grant bound, and the
watchdog tells the architect about one within a cycle - which is what holds when the
implementer's harness is granted every tool and could commit with `--no-verify`.
`tests/test_attacks.py` names ten attacks on commit authority, each of which fails closed. A review
waiver (`ao waive review`) is a person's
act and is bounded: it names one slice and a person, expires (24 hours unless `--hours`
says otherwise, at most a week), stands in for the review of one candidate only, and is
opened and retired by chained appends. `ao catchup` retires it only on a review that was
recorded for the exact commit granted under it, and that review may not come from the model
family that wrote the commit: the grant records who landed it where ao can establish that (a
turn ao started names its role), and where it could not, a person names the family with
`ao catchup --author-family <family> --by <name>`, on the record. That commit is looked for
after the head its grant was built on, or, when history rewritten since has left that head off
HEAD's line, among the commits made from a day before the waiver was opened; a waiver whose
granted tree landed nowhere there stays open as UNRESOLVED. That reviewer judges the
commit against its own message, handed over as claims to verify rather than facts, scanned for
credentials first. The one other way such a waiver retires is a proof: the grant for a slice the
board marks `move-only` records the `ao split-check` proof it stood on only when its
authorising waiver or review names that slice and it is the only running move-only slice.
`ao catchup` accepts that record only when its slice matches the waiver's, runs that
proof again on the commit that landed, and the waiver closes on it, with the proof as the
closing record's evidence, or stays open with the reason. A grant that recorded no proof is
closed by one only when a person states that its slice only moved code, with
`ao catchup --move-only <slices> --by <name>`, and then only when the proof holds; the statement
is recorded beside the proof, and no actor's grant admits it. A retrospective
`ao review --commits` artifact can reconcile landed work but can never authorize a candidate.

*In ao since slice LEADS-GATE: `ao commit` writes the commit itself: `git commit-tree` of the tree
the grant bound, on the commit the grant was built on, then the branch moved from that commit only,
compared and swapped, with no hook run. `git commit` read the index again once its hooks returned, so
a path another process staged in between landed inside the granted commit, as did one a hook staged
into the index Git handed it, or one written into a copy of it; the branch could be switched under it;
and the hooks it ran were files an implementer edits where a repository keeps them in its tree, run
as the person committing. A path staged after the check now stays staged, uncommitted, and a branch
that moved meanwhile is left where it is, with nothing landed. The message is cleaned as `git commit
-F` cleans one, by `commit.cleanup`; `commit.gpgSign` signs it; and a merge, cherry-pick, revert or
rebase in progress is sent back to git: a grant binds one parent, so ao lands no merge, and a lane
lands squashed ([gates](gates.md#a-commit-names-its-grant)). Git's own commit hooks - a `commit-msg` a project keeps among them - do not run under
`ao commit`; they run under a plain `git commit`, which the commit hook checks.*

Two more acts weaken what a review is independent of, and both are a person's in the same way
([review tiers](roles.md#review-tiers)). `ao person-review --by <name>` records a person's verdict on
the exact bytes it showed them, judged against the slice, boundary and criteria it showed with them, and
bound to the candidate as a model's review is.
`ao config set review.same_family labeled --by <name>` lets another model of the implementer's family
review, labeled as weaker independence wherever its reviews show, and it is in force only while a
person's record in `.ao/ledger/opt-ins.jsonl` says so. Each records the name, the login and whether a
terminal was attached, refuses a name that belongs to an agent or a role, and is on the list of commands
no actor's grant may admit; ao still cannot prove that no agent typed it.

*In ao since slice LEADS-OPEN: the commands printed after `ao person-review`
quote the person's name and every scope argument for the platform's shells.
On Windows, a value that cmd.exe or PowerShell could expand suppresses both
copyable commands; ao keeps the diff and digest visible and asks the person
to enter a command using their shell's literal quoting.*

Hook ownership is content- and topology-sensitive, not marker-based. Project
enrollment is marker-based: only a root `.ao-project` with exact
`ao-project-v1\n` bytes in HEAD or the active index activates enforcement. The
active index supports first adoption; HEAD keeps a staged marker deletion
protected until its authorized commit. `GIT_INDEX_FILE` is part of that active
candidate; an explicitly empty override is invalid, while relative overrides are
resolved against the repository root where the Git query runs, so direct
`commit-check` and the installed hook measure the same index. A marker with
missing, unreadable, malformed, non-object, or empty `.ao/config.json` state
refuses rather than falling back to auto-discovery. Every write of that file replaces
it whole — a temporary file beside it, fsync, rename — so a crash or kill leaves the old
document or the new one, never an empty file that has lost its `capability_matrix`. The
temporary file, for this file and every other ao replaces whole, is made under a name no file
had and never through a link: at the `.<name>.<pid>.tmp` it had, a link an agent left for a
likely pid had ao truncate and write the link's target (STORAGE-TMP);
a writer refuses a config it cannot read instead of rebuilding it. Both pre-dispatch loading and
commit enforcement use one bounded reader: it reads at most 1,048,576 bytes and
rejects container nesting deeper than 64 before recursive JSON decoding. A
repository with neither HEAD nor index marker is uninitialized, so an incidental
`.ao/` directory does nothing.

`ao hooks status`
asks Git for the effective path and preserves foreign, ambiguous, symlinked, and
tracked content. Exact generated bytes remain labelled `behavior unverified` because
static intent is only the safety precondition for a runtime probe; it is never the
installed verdict. The positive verdict comes only from `git hook run pre-commit`
executing the active path with a temporary index containing a nonce-named gitlink to
the existing HEAD commit, then receiving the same nonce in `commit-check`'s
fail-closed response. The four `AO_HOOK_PROBE_NONCE`, `AO_HOOK_PROBE_PATH`,
`AO_HOOK_PROBE_HEAD`, and `AO_HOOK_PROBE_INDEX` fields are one reserved internal
challenge namespace. A process carrying all four is treated as a probe, never as a
live commit: the namespaced index must be absolute and exactly equal to
`GIT_INDEX_FILE`, and the branch always refuses after validating it. Callers that
start a real commit must not inject the complete reserved challenge. A partial
challenge is inert, while a complete malformed challenge refuses without emitting
proof. The real index, worktree, refs, and object store are unchanged.
`hooks status`, both doctor modes, and init use that one result. A missing executable,
wrong path, altered body, stale AO binary, scoped route that fails open, timeout, or a
hook that merely exits nonzero without the nonce is reported `not installed`.
Install, uninstall, and remove preflight the complete mutation set and refuse all
eligible shared/external or global/system-configured mutations unless a person
supplies `--allow-shared-hooks`. The flag authorizes the location, never foreign or
protected content. `ao remove --yes` decommissions in two authorized phases:
its first invocation removes only the working-tree marker and preserves state and
enforcement; after that marker deletion is staged, granted, and committed, the
second invocation may remove `.ao/` and the remaining AO-owned surfaces. Pre-push
remains informational rather than a doctor alarm because a repository may
intentionally retain its own custom push hook.

*In ao since slice LEADS-OPEN: hook uninstall reads ownership, tracking and
location again just before each removal, and compares the file's identity and
bytes across that reading. A changed or newly protected hook stays, with exit
1 and a request to inspect `ao hooks status`. This narrows the stale-plan race;
there is still no atomic compare-and-unlink operation, so a replacement in the
instant after the last comparison can be removed.*

*In ao since slice LEADS-GATE: the commit hook is version 5. It stands aside only where Git answers
that the project's `.ao-project` is neither in the index nor in HEAD, asked for by its path from the
top of the working tree; any other answer goes on to ao, and a hook that finds no ao refuses. The
version 4 hook stood aside whenever its two queries failed, and a project in a subdirectory failed
both twice over: with its directory moved away, and with its marker's deletion staged, since
`HEAD:.ao-project` names a marker at the top of the repository. Either let a commit through
unchecked. Nor does it stand aside on a branch not born yet - `git switch --orphan` with the marker
unstaged: there it goes on to ao, which refuses the commit until the marker is committed on that branch
where the repository's history tracks the marker on another branch, or where this machine recorded the
project as enrolled. `ao hooks install`, which puts that hook in place, and each check of a commit of
an enrolled project - one read as broken, an orphan branch of it among them, too - record it in
`ao-enrolled.json` in the repository's common git directory, the one each of its linked worktrees
shares, outside the tree: not under a project key, which a `.ao/project-key` in a tree can claim, nor
in `~/.ao`, found through a `HOME` a commit can be given; and they refuse where they cannot write it,
since a record missing is that branch read as a new repository's: an agent that deleted every ref whose
history tracked the marker, and the tree's `.ao/` with them, left Git no answer that the repository
was ever enrolled, and the hook stood aside for its unreviewed root commit. A born branch that tracks no
marker - one made from a commit before the marker's - goes on to ao too while any ref's history tracks
one, and ao refuses it where the record stands and its history never tracked the marker; a history that
did is a removal committed, and passes. A project a person removed by hand, its hook left in place,
asks ao too, and `ao hooks uninstall` takes the hook; `ao remove --yes` takes the hook and the record
with the project. A repository's first commit therefore needs ao, as every commit of an enrolled one
does; an ao that finds nothing tracked and nothing recorded lets it in. `ao doctor` reads a version 4 hook as
legacy, and each enrolled repository reinstalls with `ao hooks install`. Install also replaces ao's
exact hook without its owner's execute bit, which Git does not run and ao read as current, now
`legacy (not executable)`; it puts a hook on a path it found
free only while the path is still free - a hard link, or Windows' rename - and over ao's own hook only
after reading it again, and only while the path still names the file it read - the same device, inode,
size, times and bytes, the time its inode changed among them, which no tool sets back - so a hook another
tool wrote or renamed into place before that last look is kept. One
put there in the instant between that look and the write is replaced: no file operation replaces a
file only while it holds what was read, and taking ao's hook aside first would leave the path with no
hook while a commit runs, an unchecked commit where the other costs another tool its hook. A Git query ao makes, the
execution proof's `git hook run` among them, leads a process group of its own and is stopped with
what it started when it runs past its time.*

The chain is tamper-evidence, not writer authentication. It detects an edit, deletion,
reorder, or append whose successor/predecessor links were not recomputed. It cannot
cryptographically exclude a process with the same user's write access: that process can
append with the correct predecessor, recompute a replacement suffix, or truncate a valid
tail and edit the recorded length to match. The length record outside the repository
makes a cut tail visible to anything that did not also rewrite that record; it is not a
trusted anchor. There is no trusted external head anchor, signing key, or authenticated append
service in AO, so the ledger must not be described as proving who wrote a row or as
detecting every same-user rewrite.

The grant never covers `push`, and no configuration makes it. Deciding and acting stay
in different hands, which is the only reason the decision is worth anything.

## 4. Separation of duties

`reviewer ≠ implementer` for the same slice, enforced rather than advised. A model
reviewing its own output shares its own blind spots. Where only one engine is available,
the reviewer must at least run a different model family.

Who reviewed, the verdict and the counts are read from the evidence line ao writes into
the artefact, never from its text, where the implementer's boundary also appears. Without
a capability matrix a reviewer is the implementer when its id is the implementer's
session id (`auto` resolved the way the watchdog resumes it) or its command carries that
id as an argument. Such a route is never run, and `ao commit-ok` refuses a review that
records one. A label is not an identity: a reviewer called `s10` is not the implementer
`s1`, and one called `independent-auditor` that resumes `s1` is.

The same applies to `verifier ≠ implementer`.

## 5. Trust flags are a decision, not a default

Every adapter exposes something like `--trust-all-tools` or
`--dangerously-skip-permissions`. Driving an agent with those flags hands it unattended
shell access on your machine.

- Default to **scoped trust** — the narrowest tool set the slice needs.
- Reserve trust-all for sandboxed workspaces or work you would run unattended anyway.
- Never combine trust-all with a prompt assembled from untrusted content.

The orchestrator records which trust level each turn ran at, so "what could that turn have
done?" has an answer after the fact: a flag ao adds to a turn's command is written, with why, to
`.ao/ledger/actor-flags.jsonl`.

### A turn runs in the mode ao pins, not in a person's default

A harness that takes its permission mode from a person's settings when a command names none lets
that setting widen every turn ao starts. Claude Code does: a `-p` run given no `--permission-mode`
starts in `permissions.defaultMode` from the settings files, and `claude -p --resume` in the mode a
new `-p` run would, so an `acceptEdits`, `auto` or `bypassPermissions` default reached every nudge,
every architect wake and the reviewer ao treats as read-only (code.claude.com/docs/en/permission-modes
and /docs/en/sessions, read 2026-09-17).

An adapter declares, per role, the flags every turn in that role carries (`options.pin`,
[adapters.md](adapters.md#a-turns-grant-is-declared-and-so-is-every-flag-that-turns-approvals-off)).
The implementer's nudge, the architect's wake and refill, every reviewer route and the bug hunter
carry them. A command composed before they were pinned, or written by hand, has them appended when
its turn starts - recorded for a wake, printed for a review or a hunt - and one that names the flag
with another value keeps it, and `ao doctor` names it. Claude Code pins `dontAsk` for every role:
whatever would prompt is denied instead, so the grant ao composes is the whole grant, except the
allow rules a person writes into their own settings, which apply in every mode. A reviewer and the
bug hunter also get `--tools Read,Grep,Glob`: no other tool exists in their session, so no allow rule
anywhere can hand them Edit or Bash. `plan` is no reviewer's mode, since where auto mode is available
the classifier approves shell commands while planning.

A stand-in session is a person's own: ao starts none, and its answer reaches a review only through
`ao collect-review`, which checks the candidate, not the session ([standin.md](standin.md)).

### Each role is granted what its playbook asks of it, and nothing more

The implementer's grant names every `ao` command and ao MCP tool that the playbook's loop, the
mailbox and measurement sections and the nudge tell it to use; the architect's names what its routine
and the wake and refill prompts ask for, and it edits `.ao/backlog.md`, `.ao/board.md` and
`.ao/inbox/` and no other file. `tests/test_grants_pinned.py` reads those playbook sections and the
prompts and fails when either names a command or a tool outside that role's grant. `ao lock -- <command>`
is in no unattended grant: it runs any command, and the declared gates take the same machine lock
through `ao verify`, which waits for an `ao lock` run in its own checkout as it waits for another
project's gates. A rule is read the way the harness reads it - a `*` stands for any text, spaces
included - so `Bash(rm agent-mail/*)` admits `rm agent-mail/x src/app.py`, and `ao doctor` names a
rule in an architect's grant that writes outside ao's coordination files, and an architect grant
composed before its adapter's current one (`architect-grant`).

*In ao since slice ALLOWLIST-NORMALIZE: a rule is also read as the command it runs. Each program that
runs the command after it - `env`, `xargs`, `timeout`, `nice`, `nohup`, `time`, `command`, `exec`,
`stdbuf`, `caffeinate`, `sudo` - is taken off with its own options, as are a rewriter's wrappers
(`rtk proxy`, `rtk git`) and git's global options (`-C`, `-c`, `--no-pager`, `--git-dir`,
`--work-tree`). So `Bash(git -C:*)`, written to read a status elsewhere, is named for admitting
`git commit --no-verify`, and `Bash(timeout 60 pytest:*)` for admitting `pytest -p x`; `ao doctor`
names such a rule once, for the first forbidden command it admits that way, "as the command it runs".
A rule already named for a form it admits as it stands is not named again, and an everyday rule behind
the same words - `Bash(git -C . status:*)`, `Bash(timeout 60 git status:*)` - is not named at all.*

*In ao since slice GATE-LOCK-SAME-ROOT: `ao verify` and `ao merge-check` wait for a holder of the
machine lock that runs for their own project. They used to wait only for another project's, and ran
their suite beside an `ao lock -- <command>` in the same checkout.*

### A flag that turns approvals off is declared with its reason

A test fails when a command ao starts from a shipped adapter carries a flag that approves everything,
sandboxes nothing or checks no permission - `--yolo`, a `--dangerously-` flag, `--trust-all-tools`,
`-y`, `-f`, `--auto`, a mode of full access - without a reason for that role in the adapter's
`options.bypass`, and `ao adapters validate` reports the same of any adapter. Where a harness documents
a narrower grant that an unattended implementer can run its gates under from the command line, the
adapter uses it instead (`options.unattended`); [adapters.md](adapters.md#a-turns-grant-is-declared-and-so-is-every-flag-that-turns-approvals-off)
lists each flag that stays and why.

### A turn that would turn a harness's own sandbox off waits for a person

`codex exec` runs every command in a sandbox of its own and asks nobody. Its workspace-write sandbox
keeps `.git` read-only and the network off, so an unattended implementer in it could edit and test but
never stage, commit or run its reviewer, and the only flag that lets it is
`--dangerously-bypass-approvals-and-sandbox`, which turns the sandbox and the approvals off together.
The watchdog therefore starts no such turn: it tells a person once and stands down until someone names
the adapter in `watchdog.bypass_adapters`, a machine setting, because a project's own files are
writable by the agents it governs:

```bash
ao config set watchdog.bypass_adapters codex --machine
```

Until then `ao doctor` names the refusal and what allowing it costs; after it, the implementer's grant
finding says its turns run outside the sandbox, with the network, where the commit hook and `ao commit`
hold commits and nothing holds anything else.

### Harness configuration an agent changed waits for a person

*In ao since slice HARNESS-CONFIG: `ao verify`, `ao doctor` and the watchdog name every change to what a
harness reads from a project, and nothing ao starts in the tree runs while one stands that no person has
seen; `ao harness seen` is how a person reads them and says so.*

A harness started in a folder reads its hooks, plugins, MCP servers and instructions from there. A
reviewer is never handed a candidate's ([pipeline.md](pipeline.md), REVIEW-TREE-3), but the same files
act wherever else a harness starts in a tree an agent writes: a hook the implementer puts in
`.claude/settings.json` runs in its own next nudge, outside the permissions that hold its commands, and
in the architect's session when the watchdog wakes it in the same checkout - each before any review has
seen it. A harness's own folder, `.claude/` or `.kiro/` among them, is coordination state that no
candidate carries, so no review ever would.

What counts is what the adapters ao ships declare their harness reads from a project - `project_config`
and the steering files ([the adapter guide](../src/ao/adapters/README.md)) - wherever in the tree it is,
read from the package alone, since a layer an agent can write must not say what is watched. A change is any of
those paths that differs from HEAD: modified, staged or not, added, deleted, untracked, ignored by git -
a harness reads a gitignored `.claude/settings.local.json` all the same - or a tracked file git is told
to leave out of its status with `git update-index --assume-unchanged` or `--skip-worktree`, which is
compared with HEAD's blob by content. An ignored folder git lists without descending into it is read
only when it is configuration itself, so a `node_modules/` is never walked. A link is read through, so
what it leads to cannot change unseen; a FIFO or a device is named for what it is and never opened to
wait on. A repository cloned into a configuration folder, such as a plugin, is read like any folder; a
worktree of the project's own repository is not, since it is a tree of its own, checked where something
runs in it. Past 5,000 changes ao stops and says it cannot tell what changed, and a change it cannot
read holds as one nobody has seen.

- `ao verify` prints each change nobody has seen with the command that records it, and keeps them in its
  verification record as `harness_config`. It does not fail for them: a slice may change the rules its
  implementer works under.
- `ao doctor` shows them on a `harness config` line, and `ao doctor --check` names them as
  `harness-config`; its scheduled run pages a person under the watchdog's own alarm.
- The watchdog names them to a person each cycle, in one alarm that rings once for what the changes hold
  and climbs the ladder while they stand ([alarms.md](alarms.md)). Before each turn it would start it
  reads them again, as it reads the hold: no architect wake, refill, nudge or bug hunt starts while one
  stands unseen, and the cycle's trace says why ([watchdog.md](watchdog.md)).
- `ao lane start` refuses while one stands, and a lane's `lane.post_create` command is not run when one
  appears while git makes the lane ([parallel.md](parallel.md)).

A person reads them, then records that they have:

```bash
ao harness seen --by <name>                     # each change as it stands, its diff from HEAD, and the digest of those bytes
ao harness seen --by <name> --digest <digest>   # records exactly those bytes as seen
ao harness seen --by <name> --ask               # at a terminal: shows each change, and records exactly them on your yes
```

The first records nothing. It shows each change - its diff from HEAD where it is text, with control,
zero-width and bidirectional characters escaped so nothing can hide in them - and the digest of exactly
the bytes shown. The second records them, and refuses when anything changed since they were shown, as
`ao person-review` binds a verdict to the diff it showed. A change is seen by its path and its content:
staged after it was seen it is still seen, one that lands leaves the others seen, and one that changes
again is unseen again. The record is kept outside the tree an agent writes, in
`~/.ao/harness-seen-<project>.json`, with the login and whether a terminal was attached beside the name.
It is a person's command: `--by` names a person, never an agent or a role, and no grant ao checks admits
it (`allowlist.FORBIDDEN`).

*In ao since slice LEADS-MCP: each command it prints to copy holds the name quoted as one word for the
shell - `--by 'Ada Lovelace'`, and on Windows `--by "Ada Lovelace"`, which cmd.exe and PowerShell read
alike - where a name with a space was copied as two words and refused, and a `;` in one ran a command
of its own. A name holding a sign PowerShell or cmd.exe expands inside double quotes - `$`, a backtick,
`%`, `!`, a quote - is put in no command; you type it as you write your name. A line feed in a path or
in a turn's command is escaped too, as `\x0a`, where it started a line that read as ao's.*

*In ao since slice HARNESS-SEEN-OWN: what ao writes into what a harness reads is seen already, and a
person's own tool approvals are seen in one step.*

*In ao since slice LEADS-OPEN: the record of ao's own writes and a person's
acceptance share a per-project lock outside the tree. Each reads and merges the
record while holding it, so concurrent writers keep each other's entries. A
person's new snapshot still replaces the previous person's snapshot; ao's own
writes stay alongside it. A write that cannot acquire the lock records nothing.*

`ao init` and `ao skill install` record each file they write from ao's own declarations - the playbook,
the coordination steering, the MCP entry - as seen, by the digest of what ao renders, while the file holds
exactly that: a fresh one, or one an earlier run left as it was. No turn waits on what ao wrote itself,
and both commands say which files they recorded. A file that holds anything else is left for a person to
see: ao's pointer added to a person's rule file (`ao init --rules`), an MCP file that lists another
server, a playbook a project's own adapter declares - a layer an agent can write - a file changed after
ao wrote it, and an MCP file a harness's own `mcp add` command wrote in its own words. `ao harness seen`
says which changes ao wrote itself and which a person recorded.

Claude Code writes `.claude/settings.local.json` each time you allow a tool for good, and ao cannot tell
your write from an agent's: an allow rule there widens every turn ao starts on that harness, so it is a
change like any other. At a terminal, `ao harness seen --by <name> --ask` shows each change and asks; a
yes records exactly what it showed, and is refused when anything changed while you read. A pipe, a
scheduled job or an agent's tool call is no terminal and is asked nothing, and a grant that admits
`--ask` is named as one that admits `--digest` is.

Keep the architect's own sessions in a checkout of their own. The watchdog's wakes and refills start in
the project's root, where the implementer works, and wait there for a person to see what it wrote; a
person who runs the architect interactively in a second clone or worktree of the repository, one no
agent writes in, never starts a harness on an agent's configuration at all, and meets the implementer's
work only as it lands, reviewed. `ao ask --codebase` runs its provider in a copy of the tree without
that configuration, as a review does (CODEBASE-COPY).

### What Python runs as it starts from inside the tree

*In ao since slice PYTHON-ENV: a `.pth` file, a `sitecustomize` or a `usercustomize` that Python runs as
it starts from inside the tree is harness configuration, and `ao doctor` names an installation of ao, a
`PYTHONPATH` folder or an `AO_` variable that lies inside it.*

A `.venv` in the repository is ignored, so an implementer can put a file there that every Python started
from that environment runs first: a `.pth` file in a site directory, each of whose lines that begins
with `import` is run as code, or a `sitecustomize` or `usercustomize` module. Every ao that environment
runs runs it before ao does anything - the commit hook, which runs the `ao` on the committer's `PATH`,
the watchdog's job, a person's own command - and before any review.

These files are read as harness configuration is, by path and content, under the state `run by Python as
it starts there`: in each site directory of each environment git names in the tree - by the `pyvenv.cfg`
a venv, virtualenv or uv writes, or the `conda-meta/` conda keeps - every `.pth` file, and `sitecustomize`
and `usercustomize` in any form the import system loads, with their compiled copies in `__pycache__`;
in the site directories of the interpreter running ao and in every other folder on its import path that
lies inside the tree, the same, a `.pth` file only where Python reads one. `ao verify`, `ao doctor` and
the watchdog name each one no person has seen, no wake, refill, nudge, hunt or lane starts while one
stands, and `ao harness seen` records it as it records any. A new one - a package installed into the
environment brings its own - holds until a person has read it. One the repository tracks unchanged is
HEAD's, reviewed with it.

What this does not do, said plainly: a `.pth` file in the environment that runs ao has run by the time ao
can read it. ao names it, so a person sees it before the next turn ao starts and before their own next
command, and that naming is all the protection that run had. Nor are the modules such a file imports, or
an installed package's own files, read: an environment inside the tree is one an agent can change
throughout. So run ao from an installation outside the tree - `pipx`, `uv tool`, Homebrew, or a clone
of its own - and `ao doctor` names the case it cannot cover, an `environment-in-tree` problem: ao
running from a Python environment, or from its own code, inside the tree, a `PYTHONPATH` folder there,
or an `AO_` variable that names a program, an adapter folder or a record there (`AO_GIT`,
`AO_USER_ADAPTERS`, `AO_SETTINGS`, `AO_LEDGER_CHECKPOINTS`, `AO_PROJECT_REGISTRY`, `AO_EVENTS`). It is
read from the environment the check runs in - a person's shell for `ao doctor`, the job's for the
scheduled check - and holds no turn: only the person who set it up can move it.

### The command a turn starts with is the package's, not the tree's

*In ao since slice TURN-ARGV: a wake, a refill and a nudge are composed from the adapter ao ships and
checked against it before they start, and a change to what a role's block says of its command waits for a
person.*

*In ao since slice ARCH-PROBE-ORDER: the architect's program is asked its version - which runs it - only
where a wake would ask it. The watchdog's check of whether a notice may wait for the architect, which every
cycle that weighs one runs, and `ao doctor`'s architect line resolved the block's `argv[0]` first, a program
an agent left in the tree among them. Both now share the wake and refill's command preparation: resolve
the architect's own session, plan the prompt channel, hold a command no person accepted, and refuse a
program inside the repository or a session or configured prompt that reads as a flag, before looking for
a program. No candidate comes from inside the tree. A refused command leaves notices for a person, and
the doctor's architect line says `not asked` with the reason; a person corrects the named value or command
before another wake can start.*

A dry cycle reports anomalies without delivering their alarms, so it does not prepare or probe an
architect wake merely to count which requests those alarms cover. It still builds the implementer's
nudge in the project's language. A dry refill still prepares and checks the architect's command without
probing its program (ARCH-PROBE-ORDER-2).

The watchdog composed the implementer's nudge from its adapter as every layer loads it - the project's
`.ao/adapters/` among them - and started the architect's wake and refill with the argv its block in
`.ao/config.json` names. Both are files an agent can write, so a change to either could start a command the
agent chose: another program in the harness's place, a grant that admits `git commit --no-verify`, a model
that reads as a flag.

- A nudge is composed from the adapter the package ships under the implementer's `adapter`: its resume, the
  grant it declares for a turn nobody attends, its pin, and the model and effort the block names, spelled as
  that adapter spells them. A user's or a project's layer changes nothing in it, and an implementer whose
  adapter only such a layer declares is not nudged.
- Before a wake, a refill or a nudge starts, its command is checked against that adapter. Its program is the
  harness's: one of the adapter's binaries, named alone and found on the path ao searches, or named by a path
  where ao finds that harness - never a file inside the repository, nor one named by a path from where it
  starts. No value ao fills in from the role's block or its session - a model, an effort, a refill prompt, a
  session id - starts with `-`, where a harness would read it as a flag. And its grant admits nothing ao
  forbids the role (`allowlist.problems`: a hook bypass, a person's act, arbitrary code, every tool) that the
  command ao composes for the role from the shipped adapter does not admit too, so a grant `ao doctor` only
  named before is refused at spawn. A refused turn does not start, and a person is told once for what
  refuses it, under `turn-refused:<role>`, which `ao doctor --check` names too, red and under the same alarm,
  and `ao doctor` shows on a `turn command` line. No record lets it start: its configuration must change.
- What a role's block says of its command - the implementer's adapter, model and effort, the architect's
  adapter, argv and refill prompt - is accepted once as it stands: as `ao init` composed it for a block it
  wrote, else at the first reading, and a role with no block as having none. A change from it is harness
  configuration nobody has seen, named as `the implementer's command (changed)` by `ao verify`, `ao doctor`
  and the watchdog, and no wake, refill, nudge, hunt or lane starts until a person reads it with
  `ao harness seen`, which shows the command the turn would start and what changed since it was accepted, and
  records it as the one accepted from then on. A role reassigned with `ao role set` is such a change. The
  watchdog judges the command it composed, not the file read again, which an agent can put back in between.
  What is accepted is kept in `~/.ao/harness-turns-<project>.json`.

### A review's and a hunt's command is held as a turn's is

*In ao since slice ROUTE-ARGV: the reviewer's routes and `hunter.argv` are checked against the adapter ao
ships before a review or a hunt starts one, and a change to them waits for a person.*

The reviewer's routes - its command, its fallbacks, a strict project's capability matrix - and the bug
hunter's `hunter.argv` are named in `.ao/config.json` too, a file the implementer writes. A route could run
another program under a shipped adapter's name, a program left in the tree, or a stand-in that approves
whatever it is shown, and `ao ci review` composed its reviewer from the `.ao/adapters/` of the pull request it
reviewed.

- Every route a review starts - from `ao review`, a submitted review's runner, `ao catchup`, `ao ci review`,
  `ao prove`, and the reviewer probe of `ao init` and `ao doctor`; spawned, run through ACP, or the API tool
  reviewer - and the hunter's command, from `ao hunt` or the watchdog's scheduled hunt, are held to what a
  turn is held to, as each rule applies to a route. The adapter a route names and the harness it runs agree:
  a route that names an adapter ao ships runs that adapter's own program, and one that runs a shipped
  harness's program names that harness or none, since the name says its family, its prompt channel and its
  ACP command. No program is named by a path from where it starts or lies inside the repository - a
  candidate inside the tree, reached through a PATH entry that leads there, is not even asked its version,
  which runs it - and no value ao fills in, a capability matrix's model argument or a route's model, starts
  with `-`. A refused route is a configuration to change: the review tries its next route, and says why.
- Two rules a turn keeps are adapted. A route that runs a program no adapter ships is written by hand, the
  exception [adapters.md](adapters.md) keeps, whatever label it gives itself - a capability matrix names its
  tools' adapters in its own words - and it starts only as a person accepted it. An absolute program is taken
  as written, as a reviewer's always was, for the same reason. Whether a route can write is the reviewer's
  rule's to say where it starts (`allowlist.reviewer_problems`, and every `options.trust_none` flag of a
  shipped harness), as before; the hunter must run a shipped harness that can review.
- A reviewer is composed from the adapter the package ships alone, by `ao role set reviewer` and `ao ci
  review` alike; one only a user's or a project's layer declares is refused, and says so. The hunter's
  program is found as a reviewer's is, by ao's own search: it was started by its name, and on Windows the
  current directory comes before PATH.
- What the routes say of their commands - every field of the reviewer and each fallback, the capability
  matrix, and how a review reaches them once `review.transport` is not spawn - and the effective
  `hunter.argv` are accepted as a role's block is: by `ao init` for a reviewer it
  composes, else at the first reading, and by a person with `ao harness seen`, which shows the command a review
  or a hunt would start and what changed. A change is harness configuration nobody has seen, named as
  `the reviewer's command (changed)` or `the hunter's command (changed)`. While it stands, a review starts
  none of its routes and says why, a hunt does not run, and, as for any change nobody has seen, the
  watchdog's wakes, refills, nudges and hunts and a lane wait too. It is judged by what ao read, never the file
  read again: `ao review` is the implementer's to run, and a route it wrote could be put back between ao
  reading it and the reviewer starting. `ao role set reviewer` writes such a change: run `ao harness seen`
  after it.
- The architect's wake and refill now ask whether their command is accepted, and refuse one inside the
  repository, before they look for its program, which runs each candidate for its version; no candidate is
  taken from inside the tree for a turn either (ROUTE-ARGV).

*In ao since slice REVIEW-PATH-HIGHS: what a person approves is bound where the implementer cannot rewrite
it, and an ACP reviewer only reads. A stand-in request's binding and the text a person carries are kept in
`~/.ao/review-requests/<project>/`, not in `.ao/review-requests/`, where an implementer that rewrote one had
a person's genuine approval recorded for bytes it staged after, and the review compares the bytes it
measures with that binding before it records anything ([standin.md](standin.md)). The digest a person
quotes to `ao person-review` covers the slice, boundary and criteria shown with the diff, which a board
moved meanwhile no longer matches. `review.transport` is part of the reviewer a person accepts and is
judged as ao read it; only an ACP command measured to hold a reviewer to reads answers over ACP, a read is
let run only inside the directory the review runs in, and a write the agent reports voids its answer
whatever it ended as ([adapters.md](adapters.md#what-a-harness-says-it-supports-asked-in-acp)).*

## 6. Capability gating on MCP

Every MCP tool has an access class ([mcp.md](mcp.md)): `read`, `write` or `run`. The one `run`
tool, `ao_verify`, is off unless the server is started with `--allow-verify`. No tool injects a
prompt into another agent's session or requests commit rights, the two things an attacker
would want most. Not built yet: switching each class on by itself.

## 6b. Escalation lifts scope, never authority

When the implementer refuses work because of a standing instruction, the architect stops
and asks the human. An approved `ESCALATION` message carries the human's authorisation
verbatim and unlocks exactly one scope lock — review-only becomes implement, a paused lane
resumes.

It can never unlock anything in the prohibited list of §1. An escalation that appears to
grant push, force-push, hook bypass or foreign-repository mutation is malformed by
definition and must be rejected whole, not partially honoured.

## 7. Secrets

Secrets never enter the mailbox, the event log, a message, a commit, a status line or a
report. Adapters record *where* credentials live, never their values. If a transcript
contains one, that is the vendor's bug and the orchestrator does not propagate it — the
dashboard renders opaque handles and commitments only.


A Python installation with a standard library of its own inside the tree - a conda environment, a venv whose
`lib` holds more than `site-packages`, an environment whose `pyvenv.cfg` makes a folder in the tree its Python's
home, or a Python found by its standard library's signature (`lib/pythonX.Y/os.py`) among what git lists or
tracks - runs from itself whatever it imports, in any form. ao does not read one file by file: it reads the
configuration as unreadable and names the installation, the watchdog holds every turn, and the way on is to move
the installation outside the tree. Each `pyvenv.cfg` in the tree is read as configuration too. ao does not search
every ignored folder for an installation, and a person who runs a program an agent left in the tree runs that
agent's code: what ao holds is what it, and the turns it starts, would run.

A child ao itself starts as `python -m ao` - the watchdog's hunt, a submitted review's runner, the MCP server's
verify, init's watchdog install - starts in ao's own folder in the person's home and asks Python for its safe path:
`-m` puts the working directory first on the import path, and started in the project such a child imported a
`json.py` or an `ao/` package left at its root in place of the standard library's or ao's own (CHILD-CWD).

### 7b. What ao writes is scanned before it is written

On 2026-09-08 a boundary asked for a reviewer's raw output to be kept as evidence, and the
decision had to reverse it: review artefacts are committed, and a model's stdout can carry a
credential. The class is covered now (#48). Review artefacts, mail, decision records (`ao ask`,
an answer, `ao decide`) and verification records pass `lib.scan_evidence` before a byte is
written, and a hit becomes `[redacted:<rule>]`, naming what was removed. A review artefact's
recorded digest is of the scanned bytes.

| rule | matches |
|---|---|
| `private-key` | a PEM private key block |
| `anthropic-key` | `sk-ant-` and 16 or more key characters |
| `openai-key` | `sk-` or `sk-proj-` and 20 or more |
| `github-token` | GitHub's personal, OAuth, user, server and refresh token prefixes with 20 or more characters, and fine-grained personal tokens |
| `slack-token` | Slack's app, bot, user, refresh and session tokens |
| `aws-access-key` | `AKIA` or `ASIA` and 16 upper-case letters or digits |
| `jwt` | three dot-separated base64url parts starting `eyJ` |
| `bearer-token` | `Bearer` and a 16-character or longer token |
| `assigned-secret` | `api_key`, `secret`, `password`, `passwd` or `access_token` assigned a 12-character or longer value |

False positives, and why the rules are shaped as they are: a commit id or a `sha256:` digest is
hexadecimal and matches no rule - the generic long-token rule the watchdog's log masking uses
would erase every digest commit authority reads, so it is not one of these. A word like
`password` in prose is untouched unless a long value is assigned to it. A test fixture that must
hold a token-shaped string builds it at run time.

*In ao since slice TRUST-HYGIENE: ao's own repository is held to these rules too, every one of them.
`tests/test_tracked_hygiene.py` reads every tracked file for them, for a long generated value
assigned to any name that says it is secret, such as `ENCRYPTION_KEY`, and for home directories such
as `/Users/<name>`. An assigned value that is called is code - `password = os.environ.get(...)` names
where a secret is read - and neither assignment rule takes it. A deliberate fixture stands in
`tests/hygiene-allowlist.txt`, a credential there named by the start of its SHA-256
([SECURITY.md](../SECURITY.md#this-repository)).*

## 8. Blast radius of the machine itself

Parallel lanes are cheap to start and expensive to run. Five simultaneous test suites will
swap a laptop into uselessness, and each lane individually looks reasonable. `ao verify` and
`ao merge-check` hold one machine-wide lock while a project's gates run; another project's run
waits up to `--wait` seconds for it, then refuses rather than start a second suite - and so does a
run of the same project ([gates.md](gates.md#serialisation-and-machine-pressure)). Before a gate
run, `ao lock -- <command>` or a lane starts, ao reads the machine's memory, paging and disk, and
waits or refuses rather than queue into a thrash; `ao lane start` also refuses a lane past
`limits.max_write_lanes` ([parallel.md](parallel.md)). The limits on memory, paging and disk are
machine settings, read from `~/.ao/settings.json` and never from a project's own files, and no
flag skips the reading. Not built yet: the reading on Windows, where the gate lock alone keeps
two suites apart, and a queue - a lane start that is refused is not started later.

*In ao since slice MACHINE-PRESSURE: the machine is read before a gate run, `ao lock` and a lane
start, and lanes are capped per checkout; `ao lane start` started a lane whatever the machine's
load.*

## 9. What this model does not cover

- **Cross-machine trust.** Delegated to [keyflip](keyflip.md): origin authentication,
  replay guards, encrypted rendezvous, consent-gated exec. agent-orchestrator adds no
  crypto of its own and should not.
- **Vendor-side data handling.** What a provider does with a prompt is outside this
  boundary. Decide that before you send the prompt.
- **Malicious adapters.** An adapter file is executable configuration; it names commands
  that will be run. Review one before installing it, the same as any script.

## 4b. The reviewer was the implementer, and nothing checked

Section 4 says `reviewer ≠ implementer` is "enforced rather than advised". It was
not enforced anywhere. The implementer wrote its own review, `ao commit-ok`
required only that a review say APPROVED, and authority was granted on a verdict
the implementer had produced about itself. A model reviewing its own output shares
its own blind spots, so that verdict measured nothing it did not already believe.

`ao review` runs a separate actor against the immutable staged candidate and the
slice's acceptance boundary. It records who reviewed and embeds structured candidate
and scope evidence. `commit-ok` refuses when:

- the review names no reviewer — nobody can tell who wrote it;
- the reviewer is the implementer;
- the newest structured prospective artifact for that candidate is not APPROVED or is
  marked non-authorizable; or
- the candidate or scope no longer matches the active index.

Unrelated and retrospective artifacts do not supersede a candidate review, and a
retrospective review never grants commit authority.

*In ao since slice LEADS-REVIEW: a reviewer is shown every byte the grant is bound to. The candidate's
diff was decoded with replacement characters, so each byte of it that is not UTF-8 read as the same
U+FFFD: two candidates that differed only there were one prompt, and a reviewer approved bytes it was
never shown. A diff that is UTF-8 is handed over as it always was. One that is not is shown escaped,
whole - each backslash as `\\` and each byte that is not UTF-8 as `\xNN` - which reads back to that diff
alone, under a note above the candidate that says so, in the project's language; the review's evidence
records `diff_escaped`, and `ao person-review` shows a person the same text with the same words. It is
not refused: a file a project keeps in Latin-1 or Shift JIS is its own, and a refusal would leave a
change to it no review at all, in CI or by catch-up. A tool reviewer is handed the bytes themselves, as
before, and refuses what its contract cannot read.*

The first independent run returned NEEDS_CHANGES with a BLOCKER on work whose
self-review had been APPROVED with zero findings: three of the acceptance
boundary's four items were not covered. That is the argument for the separation,
made on its first use.

The reviewer's identity also goes into `.ao/ledger/authority.jsonl`, because the
review file is evidence and evidence gets cleaned up — `semantic-review/` was
untracked and a routine cleanup slice removed it. The record of what authority
rested on has to outlive the document it cites.
