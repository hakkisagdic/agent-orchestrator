# Cloud agents

Cloud coding agents — Kiro cloud sessions, Codex cloud tasks, Cursor background agents,
GitHub Copilot's coding agent, Jules, Devin — are often the cheapest capacity you have.
Several are included in a plan you are already paying for and sit idle because managing
them means a browser tab per task.

They fit this system well, but they are not local CLIs with a different address.

## Five ways they differ

| | Local CLI | Cloud agent |
|---|---|---|
| Unit of work | a turn | a **task**, minutes to hours |
| Output | your working tree | a **branch or pull request** |
| Observation | transcript on disk | API poll, or the branch itself |
| Busy detection | file write age | service-reported status |
| Runs when your laptop sleeps | no | **yes** |

That last row is the reason to bother. Cloud lanes are also the *cleanest* parallel lanes
in the whole model: their workspace is remote, so there is no worktree contention, no
shared stash, no local resource pressure. The limits in [`parallel.md`](parallel.md) that
force write lanes to serialise do not apply to them.

## Three integration tiers

Use the highest tier a service supports.

**Tier 1 — native API.** The service exposes dispatch, status and result endpoints. The
adapter maps them directly. Best observation, real status, cancellable.

**Tier 2 — vendor CLI dispatch.** The service has a CLI that can start a task and list
tasks, even without a rich status API. Dispatch is exact; observation is coarse.

**Tier 3 — the pull request is the interface.** No API, no CLI. Dispatch happens however
the vendor requires (assign an issue, click once in an IDE), and everything after that is
observed through git:

```bash
gh pr list --search "author:app/<agent> is:open" --json number,title,headRefName,updatedAt
gh pr diff <n>            # what it actually did
gh pr checks <n>          # what CI thinks
```

Tier 3 is provider-agnostic, works today with every cloud agent that delivers via branch,
and needs no vendor cooperation. It is the fallback that makes the feature real rather
than aspirational.

## Status of specific services

| Service | Tier available today | Note |
|---|---|---|
| Kiro cloud sessions | **3** | Sessions exist in the IDE; `kiro-cli` exposes no dispatch or poll surface, and `executionTarget` in the local session list only ever reads `local`. Dispatch from the IDE, observe via branch. Revisit when the CLI grows a cloud flag. |
| GitHub Copilot coding agent | 3 | Assign an issue; it opens a PR. Pure tier 3, and it works well. |
| Codex cloud | 2–3 | Task list via CLI in recent versions; result arrives as a branch. |
| Cursor background agents | 2–3 | Dispatch from the editor or CLI; delivery via branch. |
| Jules / Devin | 3 | PR-delivered. |

Marked from documented behaviour and one local inspection; correct any row you can verify.

## The cloud lane

A cloud lane is a lane whose workspace is remote and whose deliverable is a branch:

```bash
ao lane start impl-migrations --role implementer --cloud kiro \
   --brief "Epic 19 analytics migrations, fixture-only, no schema changes outside src/db"
ao lanes                       # local and cloud lanes side by side
ao queue                       # branches waiting to be verified and merged
```

Lifecycle: **dispatch → poll → branch arrives → verify locally → merge queue.**

The poller writes the same events as any other producer, so the dashboard and the MCP
tools show cloud lanes without knowing they are cloud:

```jsonc
{"kind":"cloud_dispatch","actor":"kiro-cloud","lane":"impl-migrations","task":"…"}
{"kind":"cloud_progress","status":"running","elapsed_s":840}
{"kind":"cloud_result","branch":"agent/impl-migrations","pr":128,"files":14}
```

## Verification does not move to the cloud

This is the rule that makes cloud agents safe to use at volume:

**A branch from a cloud agent is untrusted code, and its own report of success carries no
more weight than a local agent's.** The gates run on your machine, against the merged
result, before anything lands. Never enable auto-merge for agent PRs; never let a green
check written by the same agent that wrote the code stand as the gate.

Everything in [`safety.md`](safety.md) applies unchanged — separation of duties in
particular. A cloud implementer with a local reviewer is a good pairing. A cloud
implementer that also reviews itself is the thing we built this system to avoid.

## Which work belongs in the cloud

Good candidates: well-specified, mechanically verifiable, parallelisable, tolerant of
latency. Dependency bumps, test backfill, mechanical refactors, documentation sweeps,
one-file bug fixes with a reproducing test.

Poor candidates: anything needing tight iteration, anything whose acceptance boundary you
cannot write down before dispatch, and the first slice of a new subsystem — you will spend
more on re-specifying than you save.

The scheduling rule follows from cost, not capability: **cloud capacity is usually included
and idle; local capacity is metered and scarce.** Push the parallelisable, well-specified
work to the cloud and keep the expensive local engine for the work that needs judgement.

## Dispatch brief

A cloud agent cannot ask a follow-up question cheaply, so the brief carries what a
conversation would have supplied: the exact acceptance boundary, the files it may touch,
the gates that will judge it, and the explicit statement that fixture or partial evidence
does not count as done. `ao lane start --cloud` refuses a brief without an acceptance
boundary, for the same reason it refuses a slice without one locally.
