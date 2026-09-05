# Profiles: who implements, who reviews, who judges

ao does not care which product plays which role; the roles are what it
enforces. A project chooses its cast in `.ao/config.json`, and `ao init
--profile` writes the three blocks so nothing has to be typed from memory.
Existing blocks are never overwritten — a project's cast is its own decision.

| profile | implementer | reviewer | architect | where it runs today |
|---|---|---|---|---|
| `claude-kiro` | Kiro (`kiro-cli`, headless, resumable session; model/effort optional) | Claude, `claude-opus-5`, read-only tools | a resumable Claude Code session, woken into absence | Voltrai |
| `claude-claude` | Claude Code headless (`claude -p`), `claude-sonnet-5` by default, one per worktree | Claude, `claude-opus-5` — a different model, so it is not the implementer reviewing itself | a resumable Claude Code session | Dükkan Defteri (in preparation) |
| custom | any adapter in `ao adapters` (`--implementer codex` …) | `--reviewer-model` | the same architect block | — |

```bash
ao init --profile claude-kiro --effort high        # Voltrai's shape
ao init --profile claude-claude --model claude-sonnet-5 --reviewer-model claude-opus-5
ao init --implementer codex --reviewer-model claude-opus-5
```

## What the blocks mean

- **implementer** — `adapter`, `session` (`auto`: discovered from the agent's own
  session store), `name` (the mail name), and optionally `model` and `effort`.
  The watchdog's nudge passes them through the adapter's `options` (`--model`,
  and `--effort low…max` where the adapter has one; Claude Code has no effort
  flag, so only the model applies).
- **reviewer** — its own `argv`; `ao commit-ok` refuses a review whose author is
  the implementer. Different family or different model, never the same session.
- **architect** — resumable, `session: auto`, read-only tools plus `ao`; the
  watchdog wakes it only when nobody is at the keyboard.

## The same rules everywhere

Whatever the cast, the playbook (`ao skill install`) is the same: one writer per
tree, pre-authorised slices with invariant boundaries, independent review,
commit authority granted by `ao commit-ok`, push by a person. Sub-agents in
worktrees — several Claude implementers under one Claude architect — are the
`claude-claude` profile plus `ao fanout ok` before every fan-out.
