# Upstream notes — what ao borrows, from where, and how to re-sync

ao carries no runtime dependency on any of these projects. Borrowed behaviour is
ported into ao's own standard-library code; the entry here pins what was read,
so a later re-sync is a diff against a known commit, not a guess.

| Project | Licence | Pinned | Borrowed (ported, not linked) | Deliberately not borrowed |
|---|---|---|---|---|
| [affaan-m/ECC](https://github.com/affaan-m/ECC) | MIT | `e04ea0b9cc82` (main, 2026-09-03); release v2.2.0 | AgentShield's *check categories* for agent configuration files — secrets in agent files, overly permissive allow rules, missing deny list, hook safety, MCP package hygiene — re-implemented natively behind `ao doctor` (backlog #14); the "turn a repeated win into a skill" idea (instincts) as an `ao` lessons→playbook proposal; a Kiro install target contributed upstream (backlog #15) | the 286-skill/68-agent content pack, hooks, the `ecc-universal`/`ecc-agentshield` npm packages as dependencies |

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
