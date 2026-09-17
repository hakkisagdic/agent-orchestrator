# Profiles: who implements, who reviews, who judges

ao does not care which product plays which role; the roles are what it
enforces. A project chooses its cast in `.ao/config.json`, and `ao init
--profile` writes the three blocks so nothing has to be typed from memory.
Existing blocks are never overwritten — a project's cast is its own decision.

| profile | implementer | reviewer | architect |
|---|---|---|---|
| `claude-kiro` | Kiro (`kiro-cli`, headless, resumable session; model/effort optional) | Claude, `claude-opus-5`, read-only tools | a resumable Claude Code session, woken into absence |
| `claude-claude` | Claude Code headless (`claude -p`), `claude-sonnet-5` by default, one per worktree | Claude, `claude-opus-5` — a different model, so it is not the implementer reviewing itself | a resumable Claude Code session |
| custom | any adapter in `ao adapters` (`--implementer codex` …) | `--reviewer-model` | the same architect block |

```bash
ao init --profile claude-kiro --effort high        # a Kiro implementer at high effort
ao init --profile claude-claude --model claude-sonnet-5 --reviewer-model claude-opus-5
ao init --implementer codex --reviewer-model claude-opus-5
```

## What the blocks mean

- **implementer** — `adapter`, `session` (`auto`: discovered from the agent's own
  session store, below), `name` (the mail name), and optionally `model` and `effort`.
  The watchdog's nudge passes them through the adapter's `options` (`--model`,
  and `--effort low…max` where the adapter has one; Claude Code has no effort
  flag, so only the model applies).
- **reviewer** — its own `argv`; `ao commit-ok` refuses a review whose author is
  the implementer — its id is the implementer's session, or its command resumes that
  session. Different family or different model, never the same session.
- **architect** — resumable, `session: auto`, read-only tools plus `ao`; the
  watchdog wakes it only when nobody is at the keyboard.

## How `auto` finds a session

`auto` is resolved when ao loads the config, so `ao status`, `ao tail`, `ao cost`,
`ao fleet`, the nudge and the architect's wake all read the same session. A session id
written in place of `auto` is pinned and always wins.

1. **The store.** The role's adapter declares where its harness keeps sessions: Kiro's
   metadata names each session's workspace, and Claude Code keeps a directory's sessions
   under the escaped directory name. An adapter that declares no store cannot resolve
   `auto`; pin its session.
2. **A role alone in its store** takes the newest session there. With `claude-kiro` both
   roles are alone: every Kiro session for the workspace is the implementer's, every
   Claude Code session in the architect's directory the architect's. A newer session is a
   conversation someone opened, and `auto` follows it.
3. **Two roles in one store.** With `claude-claude` both roles run Claude Code in the
   project directory, and the newest transcript is whichever wrote last: a wake once
   resumed the implementer's own session as the architect. There ao takes a session only
   when it can tell: a pinned id, a session it recorded, or the one session the other role
   does not hold. The implementer is resolved first and takes a lone session; the
   architect never takes the implementer's. Two sessions ao cannot tell apart are
   **ambiguous**: ao names them and refuses to guess.
4. **What is resumed.** A session found beside a role that is neither pinned nor recorded
   is read for `ao status` and `ao tail`, but neither nudged nor woken until one of the two
   is pinned. Pin one session and ao settles the other.
5. **What is recorded.** A session settled that way is kept in `.ao/sessions.json`, which
   `ao init` adds to `.gitignore`, so later cycles keep it while its transcript exists;
   what is recorded there is this machine's, never the repository's. A newer session in a
   shared store does not replace it: pin the new one.

`ao doctor` prints each role's session and how it was found (`pinned`, `recorded`,
`discovered`), or why there is none (`ambiguous`, `unresolved`), and `ao doctor --check`
names an ambiguity as a problem. Sessions ao starts for a reviewer or a hunter run in a
directory of their own outside the repository, so they are never a role's.

## The same rules everywhere

Whatever the cast, the playbook (`ao skill install`) is the same: one writer per
tree, pre-authorised slices with invariant boundaries, independent review,
commit authority granted by `ao commit-ok`, push by a person. Sub-agents in
worktrees — several Claude implementers under one Claude architect — are the
`claude-claude` profile plus `ao fanout ok` before every fan-out.
