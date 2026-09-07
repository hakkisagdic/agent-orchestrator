# Upstream notes — what ao borrows, from where, and how to re-sync

ao carries no runtime dependency on any of these projects. Borrowed behaviour is
ported into ao's own standard-library code; the entry here pins what was read,
so a later re-sync is a diff against a known commit, not a guess.

| Project | Licence | Pinned | Borrowed (ported, not linked) | Deliberately not borrowed |
|---|---|---|---|---|
| [affaan-m/ECC](https://github.com/affaan-m/ECC) | MIT | `e04ea0b9cc82` (main, 2026-09-03); release v2.2.0 | AgentShield's *check categories* for agent configuration files — secrets in agent files, overly permissive allow rules, missing deny list, hook safety, MCP package hygiene — re-implemented natively behind `ao doctor` (backlog #14); the "turn a repeated win into a skill" idea (instincts) as an `ao` lessons→playbook proposal; a Kiro install target contributed upstream (backlog #15) | the 286-skill/68-agent content pack, hooks, the `ecc-universal`/`ecc-agentshield` npm packages as dependencies |
| [Fredrin](https://fredrin.com/) | proprietary, closed source, cloud control plane | site read 2026-09-07 (landing + [pricing](https://www.fredrin.com/pricing)) | **Ideas only, nothing ported yet.** Its goal→ticket→dependency model: a goal holds tickets, the tool derives the dependency order and runs every unlocked ticket in parallel. That is ao's weakest surface — our board is hand-maintained prose and READY is judged by a human reading it (backlog #33–#35). Also: one board visible from desktop, browser and phone, and BYO subscription with model calls going straight from the machine to the provider (which is already ao+keyflip's split) | The cloud control plane and hosted board — ao is local-first and the umbrella platform's recorded invariant is no central identity. "Review and auto-merge" as a product feature: landing without evidence of an independent review of the exact candidate is the failure ao exists to prevent. Also its agent-usage billing, which duplicates keyflip |
| [Bernstein](https://github.com/andyrewlee/awesome-agent-orchestrators) and the goal→DAG orchestrators surveyed with it | mixed, mostly MIT | surveyed 2026-09-07 via awesome-agent-orchestrators | One principle worth keeping: **scheduling decisions are made in code, never by a model** — same inputs, same order, however the agents' replies interleave. ao already holds this (`ao features` all-off is deterministic); the decomposition work in #33–#35 must not break it by asking a model what is READY | Their planner agents deciding task order at runtime, and merge steps that land work without an authority grant |

## Re-sync procedure

1. `git -C <ecc checkout> diff <pinned>..origin/main --stat -- packages/agentshield src/` and read only the
   parts that touch a borrowed category.
2. Port a change only when it maps to an ao invariant; update the pinned commit here in the same commit.
3. Never vendor files verbatim; ao stays dependency-free and its tests own the behaviour.

## Known false positives of AgentShield (kept out of the port)

- Reversed-text heuristic flags the phrase "backward compatible".
- `env -u VAR cmd` (unsetting a variable) is reported as "dumps environment variables".
- A deny rule that *mentions* `--no-verify` is reported as a dangerous flag; the port must inspect the
  rule's list (allow vs deny) before scoring.

## Overlap watch

- ECC 2.0 (`ecc2/`, Rust alpha) is a multi-session control plane: SQLite session store, daemon, worktree-aware sessions, risk scoring, orchestration and review controls. That is ao's layer, not the skills layer. Re-check on every re-sync; ao's differentiators stay authority, receipts and independent review of the exact candidate.
