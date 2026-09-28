# Parallel execution

Two independent axes: many **projects** at once, and many **lanes** inside one project.
They have completely different hazards, so treat them separately.

## Axis 1 — many projects

```bash
ao projects                       # every workspace with a local agent session
ao fleet                          # one row per project, what needs a human first
ao watch --all                    # the same rows, live
ao -C ~/work/acme-api status      # any command, against another project
```

State lives per project in `.ao/` (config, board, ledgers); the machine's registry at
`~/.ao/projects.json` only maps project names to paths. Nothing is shared between projects,
so a runaway lane in one cannot touch another.

This axis is cheap. The only shared resource is your machine.

## Axis 2 — many lanes in one project

A **lane** is one actor working one slice in one workspace. Lanes are where the real
hazards live, and there are two kinds:

| Lane kind | Workspace | Can run in parallel with |
|---|---|---|
| **write lane** | its own git worktree, exclusive | any other write lane (different worktree), any read lane |
| **read lane** | the canonical checkout, read-only | anything |

Bug-hunting, review, analysis and documentation-reading are read lanes: they need no
worktree and can all run at once. Implementation, test-writing and refactoring are write
lanes and each needs its own worktree.

### A write lane: one worktree per board item

*In ao since slice LANE-START: `ao lane start`, `ao lane list` and `ao lane remove`, and the
`lane.*` settings that prepare a lane ([configuration.md](configuration.md)).*

```bash
ao lane start B3        # a READY board item gets a worktree of its own, on a branch of its own
ao lane list            # each lane this checkout started: its state, its item's, what it holds
ao lane remove B3       # retire it; refused while it holds changes nobody committed
```

`ao lane start` makes a lane only for an item that is on the board and READY - queued, with
every `needs:` landed and waiting on no one, as `ao board ready` lists it. It refuses any other
item, and one that already has a lane, saying why and making nothing. The lane is a git worktree
at `<main checkout>-lanes/<lane>`, beside the main checkout and never inside another worktree, on
a new branch `lane/<lane>` from the HEAD of the checkout that started it. `<lane>` is the item's
id with each run of characters a directory or a branch cannot hold made one hyphen, so
`ACME-187/1` is worked on `lane/ACME-187-1`. The lane is recorded in `.ao/lanes/<lane>.json` of the
checkout that started it, and that checkout lists and removes it; another checkout of the
repository refuses to start one over it, naming the worktree that stands there. A record names
directories on this machine, so `.ao/lanes/` holds a `.gitignore` of its own that keeps all of it
out of the repository, whatever the project's `.gitignore` says. Starting a lane leaves the board
as it was: the item moves on it as any slice's does.

Before a lane is reported ready, the settings prepare it, in this order: `lane.link_paths` are
linked from the main checkout, `lane.env` is written as `NAME=value` lines into `lane.env_file`,
and `lane.post_create` runs in the lane with those variables, as an argument list and never a
shell string, within `lane.post_create_timeout` seconds; what it prints goes to
`.ao/lanes/<lane>.log`. `{item}`, `{lane}` and `{path}` in a value or an argument stand for the
lane's own, so each lane can have its own port or database. Nothing is put over what the lane's
branch already has. When a step fails, ao says which and why, records the lane as failed and
leaves its worktree where it is for inspection: it is never reported ready. A setting ao cannot
use - one string where `lane.post_create` takes a list of words, a path that climbs out of the
checkout - refuses the start before anything is made, since a lane prepared other than its
settings say would be called ready unprepared. When git cannot add the worktree at all, ao
removes the branch it made for it too, so nothing is left to refuse the next start.

What ao puts in a lane, git ignores there: a link whose target is this machine's main checkout,
or a file holding a lane's port and secrets, would otherwise go into the next `git add -A`. The
usual `node_modules/` ignores a directory and not a link at that name, so where the branch's
ignore rules would not ignore what ao puts there, ao adds a line naming exactly it, such as
`/node_modules`, to the repository's own `.git/info/exclude`. Every worktree of the repository
reads that file and nothing commits it; the main checkout ignores the name too.

A Ctrl-C, SIGTERM or SIGHUP while `lane.post_create` runs stops the command with everything it
started, since it runs in a process group of its own that the terminal's signals never reach,
and the lane is recorded as failed. An ao killed outright can stop nothing: `ao lane list` then
shows the lane preparing while its command runs on, and `ao lane remove` stops that command
before it retires the lane.

`ao lane remove` refuses a lane whose worktree holds changes nobody committed, other than the
links and the file ao put there, or a review in flight, and names them. Otherwise it removes
ao's links first, so nothing can reach through one into the main checkout, and retires the rest
as `ao worktrees prune` does: the lane's `.ao/` state archived, its branch tip kept at
`refs/ao/archive/lane/<lane>-<stamp>`, its worktree and its branch removed. A lane whose
directory someone deleted is retired all the same. `ao remove` refuses while a lane stands:
the lane's record is in `.ao/`, and without it no ao command could retire the lane.

Not built yet: a lane's role, read lanes, and one panel for every lane. Today a review runs in
the background through `ao review submit` and a bug hunt through `ao hunt`.

<!-- not built: a lane's role, read lanes and the lanes panel are a design; ao lane starts write lanes only -->
```bash
ao lane start impl-updater  --role implementer
ao lane start hunt-races    --role bug-hunter            # read lane, no worktree
ao lane start review-slice  --role reviewer              # read lane
ao lanes                                                 # status of all lanes
ao lane stop impl-updater
```

## The four hazards

**1. Two writers on one session.** Injecting a prompt into a session that is mid-turn
corrupts its transcript. Every drive call is idle-guarded — status *and* write-age, ANDed.
A lane never shares a session with another lane.

**2. Two writers on one working tree.** Two agents editing the same checkout produce
interleaved, unreviewable diffs. Write lanes get separate git worktrees: `ao lane start`
makes one per board item and refuses a second lane for an item that has one.

**3. The git stash stack is shared across worktrees.** This one surprises everybody: stash
is repository-global, so a `git stash pop` in one lane can restore another lane's work.
`ao` forbids bare stash in lane instructions and uses temporary WIP commits instead.

**4. Machine exhaustion.** Five parallel test suites will swap a laptop into uselessness,
and each lane individually looks reasonable. `ao verify` and `ao merge-check` hold one
machine-wide lock while a project's gates run, and a run that finds it held waits up to
`--wait` seconds, then refuses rather than start a second suite - whether the holder runs for
another project or for the same one ([gates.md](gates.md#serialisation-and-machine-pressure)).
`ao lock -- <command>` puts any other heavy command under the same lock, so an implementer's
`ao lock -- npm test` and the architect's `ao verify` in one checkout no longer run two suites
together. Not built yet: checking memory pressure and the swap-in rate before `ao lane start`
or a gate run, refusing rather than thrashing, and a cap on write lanes. The design:

```yaml
limits:
  max_write_lanes: 2
  max_concurrent_gates: 1          # test suites are serialised even when lanes are not
  refuse_above_swapins_per_sec: 1500
```

Gate serialisation is the important one. Lanes can *think* in parallel; they should not
all *run the test suite* in parallel. The machine gate lock does it ([gates.md](gates.md)):
`ao verify`, `ao merge-check` and `ao lock -- <command>` hold one lock for the whole machine,
and a run waits for whoever holds it - another project, a lane in another worktree, or
another run in the same checkout.

*In ao since slice GATE-LOCK-SAME-ROOT: `ao verify` and `ao merge-check` wait for a run in the
same checkout too. They waited only for a holder in another checkout, so two actors sharing one -
an implementer running `ao lock -- <suite>` while the architect ran `ao verify` - ran their
suites at once.*

## Integration: a merge queue, not a free-for-all

Parallel lanes converge serially. The architect owns a queue:

1. A lane finishes and reports; it does not merge itself.
2. The verifier runs the gates **on the merge result**, not on the lane in isolation —
   two independently green lanes can be red together.
3. Green merges; red goes back to its lane with the failure.
4. One merge at a time, in queue order.

Step 2 is `ao merge-check`, which records the run ([gates.md](gates.md)):

```bash
ao merge-check impl-updater          # the full profile on HEAD merged with impl-updater
```

Not built yet: the queue itself, so its order is the architect's.

<!-- not built: the merge queue is a design; ao has no queue command -->
```bash
ao queue                    # what is waiting to integrate
ao queue merge impl-updater # verify-then-merge, one lane
```

## Addressing in the mailbox

With parallel lanes, messages carry a lane:

```
20260904-1030-architect-to-implementer@impl-updater-DECISION-branding.md
```

An actor reads only messages addressed to a role it currently holds in a lane it owns.
Everything else in the directory it leaves alone.

## A worktree lives as long as its slice

A worktree per lane is cheap to create and easy to forget: seven stood on one machine,
one per slice, none removed when its branch landed, each a full checkout with its own
state and stale reviews, and a merged one still listed as if it were live. `ao
worktrees` lists every worktree with what keeps it; `ao worktrees prune` retires the
ones that may go, as a dry run until `--yes`.

A worktree may go when its branch is merged into the default branch, when the board
rejected the slice that owns it (`worktree: <path>` or `branch: <name>` on the item), or
when its directory is already gone. It is never removed while it holds product changes
nobody committed, a review in flight, or the command that is asking. Retiring one copies
its `.ao/` state and review artefacts to `~/.ao/archive/<project>/`, keeps its branch tip
reachable as `refs/ao/archive/<branch>-<stamp>`, removes the worktree and the branch, and
runs `git worktree prune` so no administrative file is left behind. `ao doctor` names the
worktrees that may go, with their size on disk. A lane's worktree is listed there too, and
kept: a lane starts at its base, so it reads as merged the moment it starts. `ao lane remove`
retires a lane, knowing the links and the file ao put in it.

## What not to parallelise

- **Contract decisions.** One architect. Two agents deciding boundaries independently
  produce two incompatible designs and no way to tell which is right.
- **Anything touching the same files.** Split by module boundary, not by task count.
- **The first slice of a new subsystem.** Establish the shape serially, parallelise after.

Parallelism buys wall-clock time and costs coherence. Spend it where the work is genuinely
disjoint, and keep the default at one write lane.
