# Gates

A gate is a check the **verifier runs itself**. Not a check the implementer reports having
run — that distinction is the whole point, and everything else here follows from it.

## Declaring them

```yaml
# .ao/gates.yml
gates:
  typecheck:      { run: "npm run typecheck", expect: exit_zero }
  focused:        { run: "node --test test/{slice_tests}", expect: all_pass }
  full:           { run: "npm test", expect: all_pass, timeout: 30m, serialise: true }
  diff-check:     { run: "git diff --check", expect: exit_zero }
  artifact-sweep: { run: "ls -d .tmp-* scratch-* 2>/dev/null", expect: empty }

profiles:
  quick: [typecheck, focused, diff-check]      # during iteration
  full:  [typecheck, focused, full, diff-check, artifact-sweep]   # before commit authority
```

Declared once, in the repository, so a gate is not something the orchestrator improvises
differently each time it asks.

A gate may declare the paths it reads as `inputs`, a list of globs (`"inputs": ["src/**", "tests/**"]`).
An unstaged or untracked path refuses verification and commit authority only when it is part of
the staged candidate or a gate reads it; when **every** gate declares inputs, a dirty path outside
all of them is listed as `worktree_noise` instead. One gate without inputs reads the whole tree,
and then every dirty path counts, as before. Inputs are part of the gate definitions a
verification is bound to, so narrowing them after `ao verify` withdraws its authority.

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
  counts for nothing. A summary that cannot be found is recorded as `counts: null`, and
  a gate with `min_tests` then fails.
- **The result names what ran.** The record carries each gate's command and the digest of
  the profile's definitions, and the ledger is hash-chained like the authority ledger.
  `ao commit-ok` and `ao commit-check` refuse when `.ao/gates.json` no longer matches that
  digest, so a gate weakened after the run cannot inherit its pass; a row appended
  without a valid link makes the ledger unreadable. Rows written before the ledger was
  chained stay as history and never authorise.
- **The measurement is not filtered.** ao takes its numbers itself - candidate paths and
  size, `git status`, `rev-parse`, `write-tree`, gate output - never from an agent's
  terminal, and runs git as the first compiled `git` on `PATH` or in the system
  directories (or `AO_GIT`), so a script standing in front of git is passed over. The
  record says how in `measured_by`. What an agent reads through its own shell can be
  rewritten by a token-saving proxy; `ao doctor` names such a hook or wrapper, and asks
  each filter hook it may run what it does to the commands an agent measures with (below).

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
  path inside the project, and a batch file, which only a shell runs, is refused;
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

Push is not a gate outcome and never becomes one. It stays a direct human act.

## Serialisation and machine pressure

Lanes think in parallel; gates do not run in parallel. `serialise: true` marks the
expensive ones, and `ao` holds a machine-wide lock across them.

Before starting any gate run it also checks memory pressure and swap-in rate, and **refuses
rather than thrashes**. Five simultaneous test suites will make a laptop unusable while
each one individually looks reasonable — and the failure mode is a machine that appears
hung, which is the most expensive kind of confusion to debug.

A word on measurement, learned the hard way: do not gate on swap *usage*. macOS does not
release swap files after a heavy job, so an idle machine can read 93% and the gate is red
forever — and a gate that is always red teaches everyone to ignore gates, which is worse
than having none. Gate on **swap-ins per second**, which reflects present pressure.

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
