# Gates

A gate is a check the **verifier runs itself**. Not a check the implementer reports having
run — that distinction is the whole point, and everything else here follows from it.

## Declaring them

```jsonc
// .ao/gates.json
{
  "gates": {
    "typecheck":      {"run": "npm run typecheck", "expect": "exit_zero", "timeout": 600},
    "focused":        {"run": "node --test test/claims", "expect": "exit_zero", "timeout": 600},
    "full":           {"run": "npm test", "expect": "exit_zero", "timeout": 1800, "min_tests": 300},
    "diff-check":     {"run": "git diff --check", "expect": "exit_zero", "timeout": 60},
    "artifact-sweep": {"run": "git ls-files --cached --others --exclude-standard -- \".tmp-*\" \"scratch-*\"", "expect": "empty"}
  },
  "profiles": {
    "quick": ["typecheck", "focused", "diff-check"],
    "full":  ["typecheck", "focused", "full", "diff-check", "artifact-sweep"]
  },
  "default_profile": "quick"
}
```

Declared once, in the repository, so a gate is not something the orchestrator improvises
differently each time it asks. `ao init` writes this file from the toolchains it finds and
leaves one that exists alone. A gate passes on exit code zero, or with `"expect": "empty"` on
printing nothing; `timeout` is in seconds, at most a week, and `gates.default_timeout` when a gate
names none.

Detection checks the root and its immediate child directories for Node, Python, .NET,
Go, Rust, Maven, Gradle and Swift manifests. A child gets commands run in that directory
and `inputs` scoped to it. Children with no detected gates need no shell quoting. If a
child with gates cannot be quoted safely, init refuses before writing files: rename it
or supply `.ao/gates.json` by hand. An existing gate file bypasses detection entirely.
Coverage recognizes runners at command starts, including after shell separators and
`npx` or `uv run`. These wrappers also count with flags or packages such as
`npx --no-install tsc`, `npx eslint .` and `uv run --frozen pytest`; printing
`pytest` with `echo` covers nothing.
`ao doctor` reports `gates-unusable` for a missing, unreadable or malformed gate file,
including malformed gates outside the selected profile and an absent or empty default
profile, so other problems still reach its notification check. Fix the definitions before relying on the coverage report.

A gate may declare the paths it reads as `inputs`, a list of globs (`"inputs": ["src/**", "tests/**"]`).
An unstaged or untracked path refuses verification and commit authority only when it is part of
the staged candidate or a gate reads it; when **every** gate declares inputs, a dirty path outside
all of them is listed as `worktree_noise` instead. One gate without inputs reads the whole tree,
and then every dirty path counts, as before. Inputs are part of the gate definitions a
verification is bound to, so narrowing them after `ao verify` withdraws its authority.

*In ao since slice LEADS-GATE: an `expect: empty` gate passes on printing nothing only where its
command exited 0, or with a code the gate declares in `ok_exit`: grep and `git grep` exit 1 when they
find nothing, so a sweep with them declares `"ok_exit": [0, 1]`. No other code is taken for an
answer: grep exits 2 for an error, and `grep -R '[' src 2>/dev/null` printed nothing, measured
nothing and passed, as did a command that could not run or was not found, and a program whose silent
exit 1 is its failure. GNU `ls` exits 2 for a glob that matched no file and for
an error alike, so the sweep above asks git, which answers 0 on every platform; a `timeout` of zero is
refused, where it was read as no timeout and replaced by the default, as is one past a week, where `1e308`
overflowed the wait once the machine lock was held, and a `gates.default_timeout` past a week where a gate
runs under it, which was passed over for 600 seconds; a profile that is not a list
of gate names - `""` too - is said as the configuration ao cannot run. A gate that runs past its
`timeout` is stopped with what can be found of it - its process tree, its process group and, on POSIX,
its session, read again until nothing of it is found, so a worker that moved into a group of its own is
reached, its parent gone or not - where its shell alone was killed and its test workers ran on after the
run had let go of the machine lock; what the stop leaves running is named, and a process table that cannot
be read is said. It is a cleanup, not a boundary: a gate is code the implementer wrote, run as theirs by
design, and one written to outlive the stop - a worker in a session of its own - can
([threat model](threat-model.md#out-of-scope)). A
`.ao/gates.json` that `ao verify` or `ao merge-check` cannot run - not JSON, not an object, a profile
that is not a list of gate names, a gate it names that is missing or has no `run`, a `timeout` that is
not a number of seconds up to a week - is said in one line and exits 2 before the machine lock is taken, where it
raised in the middle of the run. A gate whose command line the system cannot start - one past `ARG_MAX` -
fails with exit 127 and what the system said, where it raised once the lock was held.*

## The artifact sweep earns its place

That fifth gate looks like housekeeping and is not. In the source run, fault-injection
tests left four scratch directories in the repository root. The implementer's own file
search did not report them — dot-directories whose contents are git-ignored are invisible
to most agent search tools — so it truthfully reported a clean tree.

An agent cannot reliably see its own mess. `ls -d` can. Sweep from the outside.

## Running

```bash
ao verify                 # runs the gates, writes a verification record
ao verify --profile full  # a named profile; without one, gates.json's default_profile or quick
```

Two properties that are not negotiable:

- **The verifier executes the commands.** It never parses a claim out of a report. Reports
  are usually accurate; a system whose correctness depends on that has no check at all.
- **The result is recorded** in `.ao/ledger/verifications.jsonl` with the exact numbers and
  the actor who measured them ([`ledger.md`](ledger.md)).
- **The result is read structurally.** The exit code decides pass or fail. Counts come
  only from the summary a runner ends with - node's `# pass`/`# fail` lines, pytest's
  closing line, or the regex a gate names in `summary` with `pass` and `fail` groups -
  read from the last lines of output, so a test name printing `# pass 900` earlier
  counts for nothing. Standard output and standard error share one pipe, preserving the
  order the processes write them. Pytest's `error` or `errors` count as failures even
  when no tests passed or failed. An invalid `summary` regex, or one missing either
  named group, is refused before the gate runs. Both groups must capture numbers;
  a missing capture or a summary that cannot be found is recorded as `counts: null`,
  and a gate with `min_tests` then fails.
- **The result names what ran.** The record carries each gate's command and the digest of
  the profile's definitions, and the ledger is hash-chained like the authority ledger.
  `ao commit-ok` and `ao commit-check` refuse when `.ao/gates.json` no longer matches that
  digest, so a gate weakened after the run cannot inherit its pass; a row appended
  without a valid link makes the ledger unreadable. Rows written before the ledger was
  chained stay as history and never authorise.
- **The measurement is not filtered.** ao takes its numbers itself - candidate paths and
  size, `git status`, `rev-parse`, `write-tree`, gate output - never from an agent's
  terminal, and runs git as the first compiled `git` on `PATH` or in the system
  directories, so a script standing in front of git is passed over. An executable
  `AO_GIT` overrides that choice, including a script; `ao doctor` names a non-compiled
  override as a possible filter and asks for a compiled git. On
  macOS `/usr/bin/git` is a stub that asks xcrun for the real git on every call; ao asks
  `xcrun --find git` once per process instead and runs the compiled git it names. The
  record says how in `measured_by`. What an agent reads through its own shell can be
  rewritten by a token-saving proxy; `ao doctor` names such a hook or wrapper, and asks
  each filter hook it may run what it does to the commands an agent measures with (below).
  Every shell PreToolUse command hook it does not recognise is listed as unverified,
  with its settings file and command, for a person to inspect; reporting it grants no
  permission to run it.

*In ao since slice HARNESS-CONFIG: `ao verify` names each change to what a harness reads from the tree -
hooks, plugins, MCP servers, instructions - that no person has seen, with `ao harness seen --by <name>`,
which records it, and keeps them in the verification record as `harness_config`. They fail no gate: a
slice may change the rules its implementer works under, and what holds is elsewhere, since no wake,
refill, nudge, hunt or lane starts in the tree until a person has seen them
([safety.md](safety.md#harness-configuration-an-agent-changed-waits-for-a-person)).*

## A filter's exclusions are proved, not trusted

A token-saving filter installed as a harness hook rewrites the shell commands an agent
runs; one reported a 5,844-line diff as 533 lines. It can stay for everything else only if
its exclusion list leaves every measurement alone, and a configuration that says so proves
nothing: one release of such a filter kept rewriting the commands its exclusion list named,
because the list was read on only one of its two rewrite paths. So `ao doctor`, and
`ao doctor --check` every fifteen minutes, ask the filter.

Each hook that runs before the shell tool in a settings file an adapter declares in
`directives.command_hooks` ([adapters.md](adapters.md)), and whose command the doctor
recognises as a filter or whose program is in `filters.probe_programs`, is run once for each
measurement command: `git diff` (plain, `--cached`, `--stat`, `--numstat`,
`--cached --numstat`, `--name-only`), `git status` and `git status --porcelain`, `git log`
and `git log --oneline`, `git show --stat`, `git rev-parse HEAD`, `git write-tree`,
`git hash-object --stdin`, `ao status`, `ao verify`, `ao review`, `ao board`, and the `run`
of each gate in `.ao/gates.json`. It is given the JSON the harness sends (`hook_event_name`,
`tool_name`, `tool_input.command`, `cwd`), and its answer is read as the harness reads it:
an `updatedInput` with another command is a rewrite, whether or not the answer also allows
it; a `deny` or `ask` decision, `continue: false` or exit code 2 blocks the command; no
answer, or the same command, passes it through. The command a hook answers with is never
run.

| `ao doctor` reports | when |
|---|---|
| exclusions in force | the hook passed every measurement command through unchanged |
| `measurement-filter:<program>` naming each command and what it became | it rewrote or blocked any of them: add those to the filter's exclusions |
| `measurement-filter:<program>`, not verified | the rule below does not let ao run it, or it did not answer within `filters.probe_timeout_seconds`, wrote more than 64 KiB, exited with another error or answered with something that is not JSON |

Both are advisories under `ao doctor --check`: recorded for the architect, never paged.

**Which hooks ao runs.** `ao doctor --check` runs on a timer with nobody watching, and a
project's own settings file is writable by the agents ao governs: a doctor that ran what
such a file names would run whatever an agent wrote there. ao runs a hook only when all of
these hold, and reports any other filter as not verified, naming the rule it fails:

- it is declared in a user-level settings file - one an adapter names under `~` - that does
  not lie inside the project. No list of programs could make a project's hook safe to run:
  a filter is itself a program that runs commands, so the words after its name matter as
  much as the name;
- its program is named in the machine setting `filters.probe_programs` (default `rtk`),
  which no project can widen, and resolves - through the agents' `PATH`, leaving out any
  directory inside the project - to an executable outside the project; no argument names a
  path inside the project, whether or not it is there yet (a word with a separator, or a file
  name with an extension, is taken as a path; an address on another host is not, and a `file:`
  address is read as the path it decodes to), and a batch
  file, which only a shell runs, is refused;
- it needs no shell: `command` with `args`, or a command that is only words, with no `$`,
  backquote, pipe, redirection, glob, `;`, `&` or backslash. Declare anything else as
  `command` and `args`.

It then runs without a shell, in the project directory as the harness runs it, in a process
group of its own, given only `PATH`, the home, user, configuration, temporary and locale
variables and the project directory variable its adapter names - no credentials, no `GIT_*` -
and it is killed with everything it started when it does not answer in time. Name filter
programs in `filters.probe_programs`, not interpreters or shells: `python3 -m` and `node`
load modules from the directory they run in, which here is the project.

## A merge is gated on its result

Two branches can each be green and their merge red: on 2026-09-07 one changed a
signature another still called, and main went red after four merges. Hosted CI runs
only when dispatched by hand, so the gate is local and costs no Actions minutes:

```bash
ao merge-check feature/x                 # the full profile on HEAD merged with feature/x
ao merge-check feature/x --into main -p quick
```

git computes the merge result without touching your checkout; ao checks it out in a
temporary worktree, borrows the untracked dependency directories named in
`merge.link_paths`, runs the profile there and records the run in
`.ao/ledger/merges.jsonl`, chained. The record names both parents and the tree the merge
makes, so it vouches for exactly that merge: after either side moves, it vouches for
nothing. `ao doctor` reports a merge on the current branch within `merge.check_days` that
no passing run vouches for, and one whose recorded run failed.

## Commit authority is bound to a verification

```bash
ao commit-ok                  # the newest verification passed on exactly the staged candidate
ao commit-ok --review R-<id>  # and a finished submitted review pinned that same tree
```

`ao` refuses to grant commit authority against a stale verification — one taken before the
last file change — or against a verification produced by the same actor that wrote the
code. Both refusals are enforced, not advised; see separation of duties in
[`safety.md`](safety.md).

*In ao since slice CRITERIA-VERDICTS: when the boundary lists criteria, an approval is not
enough. The review records a verdict for each criterion ([pipeline.md](pipeline.md), §2c), and
`ao commit-ok` refuses unless every one is `met`, naming each criterion that is not met and each
that has no verdict, with the reviewer's evidence; `ao commit-check` holds the grant to the same
record. What the review recorded is read, never worked out again from a boundary that may have
moved since. A review of a single-sentence boundary records no criteria, and grants as before.*

Push is not a gate outcome and never becomes one. It stays a direct human act.

## A commit names its grant

```bash
ao commit -m "land the slice"                        # the message ends with the grant's Ao- trailers
ao commit-check --range origin/main..HEAD            # each commit has the tree its trailers name
ao commit-check --range HEAD --since-first-trailer   # all of history from the first commit carrying them
```

`ao commit` ends the message with git trailers naming the grant it held the index to: `Ao-Grant`,
the grant's id; `Ao-Tree`, the index tree the grant names; `Ao-Verification`; and `Ao-Review` or
`Ao-Waiver`, what the grant rested on, `Ao-Review: off` where the review switch was off. They join a
trailer block the message already ends with, such as its Co-Authored-By, and form one of their own
otherwise, so `git interpret-trailers --parse` reads each. A message that already carries Ao-
trailers is refused, not rewritten: they could only be another commit's.

`ao commit-check --range` holds each commit of a range to its trailers. It is read-only and needs no
`.ao/`: a commit amended, rebased or rewritten after its grant has another tree, and fails. Where the
checkout's authority ledger can be read, a grant on record that names another tree, verification,
review or waiver fails the commit too; a grant it does not hold fails nothing, since a grant stays in
the ledger of the checkout that made it. A merge matches nothing (below). The
tests workflow runs the check on every push to main, over the pushed range.

*In ao since slice COMMIT-TRAILER: the trailers and the range check. The commit carries its authority
where a hook cannot follow it: a hook runs only on the machine that commits, `--no-verify` skips it,
and a command rewriter once broke the allowlist that kept it. Requiring the check is a person's step,
as publishing is: a ruleset on main that requires the `commit trailers` status check, and signed
commits where you sign them. ao never sets a ruleset.*

*In ao since slice LEADS-GATE: a merge is checked too, and matches nothing after the first commit
that carries trailers. It was skipped as git's, and a merge's tree is whatever was committed: a
conflict resolved by hand, or an edit made while merging, landed unreviewed and passed the check CI
runs. Even git's own merge of two reviewed parents is no grant's - two changes each reviewed alone can
act together where neither review looked, and `ao merge-check` runs gates, which are no review - and
a grant binds one parent, the commit its candidate was built on, so trailers on a merge, copied or
written over the granted tree, say nothing of what else it merges. A lane lands squashed:
`git merge --squash` stages it as one candidate, verified, reviewed and granted as any, which
`ao commit` lands on one parent.
The check merges nothing to say so: `git merge-tree` runs the merge drivers a repository configures,
and a range check reads and runs nothing.*

## Serialisation and machine pressure

Lanes think in parallel; gates do not run in parallel. `ao verify` and `ao merge-check` hold
one machine-wide lock while a project's gates run, and `ao lock -- <command>` holds the same
lock for a heavy command outside the declared gates. A run that finds the lock held waits up
to `--wait` seconds for its holder, then refuses rather than start a second suite - whether
the holder runs for another project or for the same one.

*In ao since slice GATE-LOCK-SAME-ROOT: `ao verify` and `ao merge-check` wait for a holder
running for the same project. They waited only for another project's, so an implementer's
`ao lock -- <suite>` and the architect's `ao verify` in one checkout ran two suites at once.
They also take the lock before reading anything of it, so two runs that find it free at the
same moment no longer both start. The one holder they do not wait for is their own run's -
the process they run in, or one that started it, as `ao lock -- ao verify` starts its verify:
that holder is waiting for them to end, so they go ahead inside its lock.*

*In ao since slice VERIFY-LOCK-LATE: `ao verify` takes the lock once it knows the gates it will
run, as `ao merge-check` did. A project with no `.ao/gates.json`, or a profile it does not
declare, is told so at once, where it waited out another suite first. The candidate is read
inside the lock, so an index changed during the wait is not taken for one changed while the
gates ran.*

Once a run holds the lock it reads the machine, and waits while the machine is short rather
than thrash. Five simultaneous test suites will make a laptop unusable while each one
individually looks reasonable — and the failure mode is a machine that appears hung, which
is the most expensive kind of confusion to debug. The lock keeps ao's own suites apart; the
reading catches whatever else loads the machine. Three figures are read, each held to a
machine setting ([configuration](configuration.md)):

| figure | read from | a run waits while it is |
|---|---|---|
| memory free | macOS: the system-wide free percentage `memory_pressure` prints; Linux: MemAvailable over MemTotal | under `limits.refuse_below_free_memory_pct`, 15% |
| paging | the swap-in counter - vm_stat's Swapins on macOS, pswpin in /proc/vmstat on Linux - as the lower median of four one-second samples | over `limits.refuse_above_swapins_per_sec`, 1500 a second |
| disk free | the volume that holds your home, in GiB | under `limits.refuse_below_free_disk_gib`, 8 GiB |

A machine that has room costs one reading, about four seconds. One that is short is read
again every ten seconds for up to `--wait` seconds more - the lock's own wait, given again -
and when it stays short the lock is let go and the run exits 2, naming what it read, the limit
and what to do: let the machine settle, or free disk, and run it again; or a person changes the
limit with `ao config set <setting> <n> --machine`. A run inside a lock its own holder took, as
`ao lock -- ao verify` starts its verify, reads nothing: the holder read the machine when it took
the lock. No flag skips the reading. The limits live in `~/.ao/settings.json`, outside every
repository an agent writes, and a project's `.ao/config.json` cannot loosen them. On Windows,
and wherever a figure cannot be read, it is not measured: the run says so once and is never
refused on its account. `ao doctor` shows the reading on one line.

A word on measurement, learned the hard way: do not gate on swap *usage*. macOS does not
release swap files after a heavy job, so an idle machine can read 93% and the gate is red
forever — and a gate that is always red teaches everyone to ignore gates, which is worse
than having none. Gate on **swap-ins per second**, which reflects present pressure, and take
their median: paging spikes by an order of magnitude for a second whenever anything starts.
Four one-second samples of an idle machine read 96, 185, 237 and 1482, and the mean of a
window holding the last closed the gate on a machine with 49% of its memory free; the median,
the second smallest of four, lets a burst of a second or two pass and still catches sustained
thrash, which runs in the thousands where idle to busy stays under 200. Take memory as the
system reports it: worked out from vm_stat's page counts, it read 1.1 GB free on a machine the
system called 47% free. And read the disk where the home is, never `/`, which on macOS is the
sealed system volume and reads 100% full when all is well. The floors come from what was
measured: healthy work ran at 40 to 49% of memory free, far above 15%, and at 10 GiB of disk,
which 8 GiB passes, where it could not run at a third of a GiB. Raise a floor if a run ever
dies above it.

*In ao since slice MACHINE-PRESSURE: a gate run and `ao lock` read memory, paging and disk once
they hold the lock, and wait up to `--wait` while one is past its limit, then refuse, where they
started their suite into whatever else loaded the machine; `ao lane start` reads them before it
makes a lane and refuses at once ([parallel.md](parallel.md)).*

## Reporting a failed gate

A red gate is data, not an accusation. The record keeps the exact output, the slice returns
to `changes-requested`, and the round counter increments — which is how a slice that cannot
converge eventually trips its budget in [`slices.md`](slices.md) instead of quietly
consuming an afternoon.


## The board carries edges, and the review loop is measured

`needs: B3, B4` on a queued item is a dependency: the item becomes READY the moment
those land. `unlocks:` is the same edge written from the other end. The ids are
checked every time the board is read: one that is not on the board, one listed twice
and a cycle are named by `ao board`, `ao board ready` and `ao doctor`, and no item they
touch is READY; neither is an item marked `waiting:` on someone. READY is only ever
derived - `ao board ready` prints exactly that set - so a hand-written `## ready`
section is refused. On a blocked item `needs:` stays the reason in words. This is the
useful core of "backend done, now the frontend" — the next item becomes eligible
without the implementer choosing its own scope, which is the one authority it must
not hold. `role:` tags an item for a particular actor; routing is a detail on top
of the graph, not a mechanism of its own.

A round budget counts rounds. It cannot see that round four's blocker is round
two's blocker with the line numbers moved — the actual shape of a slice that is
not converging. `ao` fingerprints each finding on its file and first clause across
consecutive NEEDS_CHANGES reviews and raises a `review-loop` anomaly when one
recurs three times: more rounds will not fix it; it needs re-specifying or a
different actor.

`ao digest` reads all of this back from the ledgers rather than from memory.
Refusals repeated across a week are surfaced as a process signal, because a
commit-ok refused five times for "re-run ao verify" is not five incidents — it
is one habit.
