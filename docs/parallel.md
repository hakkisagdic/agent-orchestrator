# Parallel execution

Two independent axes: many **projects** at once, and many **lanes** inside one project.
They have completely different hazards, so treat them separately.

## Axis 1 — many projects

```bash
ao projects                       # everything registered, with per-project state
ao projects add ~/work/voltrai --implementer kiro
ao use voltrai                    # set the active project for subsequent commands
ao watch --all                    # one panel, every project
```

State lives per project in `.ao/` (roles, policy, lane registry); the global registry at
`~/.ao/projects.yml` only maps names to paths. Nothing is shared between projects, so a
runaway lane in one cannot touch another.

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

```bash
ao lane start impl-updater  --role implementer --worktree
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
interleaved, unreviewable diffs. Write lanes get separate git worktrees; `ao` refuses to
start a second write lane in a workspace that already has one.

**3. The git stash stack is shared across worktrees.** This one surprises everybody: stash
is repository-global, so a `git stash pop` in one lane can restore another lane's work.
`ao` forbids bare stash in lane instructions and uses temporary WIP commits instead.

**4. Machine exhaustion.** Five parallel test suites will swap a laptop into uselessness,
and each lane individually looks reasonable. Before starting a lane or a gate run, `ao`
checks memory pressure and swap-in rate and refuses rather than thrashing:

```yaml
limits:
  max_write_lanes: 2
  max_concurrent_gates: 1          # test suites are serialised even when lanes are not
  refuse_above_swapins_per_sec: 1500
```

Gate serialisation is the important one. Lanes can *think* in parallel; they should not
all *run the test suite* in parallel.

## Integration: a merge queue, not a free-for-all

Parallel lanes converge serially. The architect owns a queue:

1. A lane finishes and reports; it does not merge itself.
2. The verifier runs the gates **on the merge result**, not on the lane in isolation —
   two independently green lanes can be red together.
3. Green merges; red goes back to its lane with the failure.
4. One merge at a time, in queue order.

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

## What not to parallelise

- **Contract decisions.** One architect. Two agents deciding boundaries independently
  produce two incompatible designs and no way to tell which is right.
- **Anything touching the same files.** Split by module boundary, not by task count.
- **The first slice of a new subsystem.** Establish the shape serially, parallelise after.

Parallelism buys wall-clock time and costs coherence. Spend it where the work is genuinely
disjoint, and keep the default at one write lane.
