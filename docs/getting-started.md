# Getting started

From an empty checkout to a first reviewed commit. Every step is a command you type, and the
last setup step proves the guarantees instead of describing them. A test runs this sequence
in a temporary repository, so this page cannot drift from what ao does.

## 1. Install and put ao on the repository

```bash
pip install ao-orchestrator
cd your-repository
ao init --profile claude-kiro        # or claude-claude
```

`ao init` writes `.ao/` (config, gates, board), the mailbox, the playbook and the MCP
registration, and the `.ao-project` marker, which you commit: it is what turns enforcement on.
It refuses when no reviewer answers or when the quick gates exercise none of your code.

## 2. Wire what makes the guarantees hold

```bash
ao hooks install                     # the pre-commit hook that refuses unauthorised commits
git add .ao-project && git commit -m "adopt ao"
```

Name a reviewer from another model family than the implementer in `.ao/config.json`
(`reviewer.argv`): a reviewer that is the same actor is refused ([roles](roles.md)).

## 3. Prove it

```bash
ao prove
```

Three checks, each run, not described:

- **the hook refuses an unauthorised commit** — Git runs the active pre-commit hook against
  a synthetic candidate and it must refuse;
- **the reviewer answers and is another actor** — it must echo a nonce, and it must not be
  the implementer's engine;
- **a throwaway slice lands end to end** — a one-line change in a temporary worktree goes
  through `ao verify`, `ao review` and `ao commit-ok`. Nothing is committed, the worktree is
  removed, and its review is kept under `~/.ao/archive/<project>/`.

Each check that fails says what would fix it. `ao init --prove` runs the same checks as its
last step. The throwaway review spends one short review from the reviewer's quota;
`--no-review` skips it and says that slice is not proven.

## 4. The first slice

Put it on `.ao/board.md` under `## queued` with its acceptance boundary, then the loop the
playbook describes:

```bash
ao board ready                       # what may start now
# work, then stage exactly the candidate
ao verify -p quick
ao review
ao commit-ok
ao commit -m "…"
```

No push: that stays a person's act. `ao doctor` shows the state of everything above at any
time.
