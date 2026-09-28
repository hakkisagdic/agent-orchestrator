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
It refuses when no reviewer answers, when the reviewer is of the implementer's own model family,
or when the quick gates exercise none of your code. Its authority, board, backlog and mailbox
files are in English, and so is what ao tells you afterwards - the digest, the handoff, alarms and
the phone; `ao init --language tr` has them in Turkish ([configuration](configuration.md)).

With one harness for both roles, `claude-claude` is refused until you choose a
[review tier](roles.md#review-tiers): another model of the same family, labeled as weaker
independence wherever its reviews show, or no model reviewer at all and a person reviewing each
candidate.

```bash
ao init --profile claude-claude --review-tier same-family --by <name>
ao init --profile claude-claude --review-tier person
```

## 2. Wire what makes the guarantees hold

```bash
ao hooks install                     # the pre-commit hook that refuses unauthorised commits
git add .ao-project && git commit -m "adopt ao"
```

The reviewer is another model family than the implementer's, another model of its family where
a person opted in, or a person with `ao person-review --by <name>`; a reviewer that is the same
actor, or of no [review tier](roles.md#review-tiers), is refused.

## 3. Prove it

```bash
ao prove
```

Three checks, each run, not described:

- **the hook refuses an unauthorised commit** — Git runs the active pre-commit hook against
  a synthetic candidate and it must refuse;
- **the reviewer answers and is another actor** — it must echo a nonce, and a review tier must
  admit it; a same-family reviewer is proven and labeled, and a project reviewed only by a person
  has no reviewer to prove;
- **a throwaway slice lands end to end** — a one-line change in a temporary worktree goes
  through `ao verify`, `ao review` and `ao commit-ok`. Nothing is committed, the worktree is
  removed, and its review is kept under `~/.ao/archive/<project>/`.

Each check that fails says what would fix it. `ao init --prove` runs the same checks as its
last step. The throwaway review spends one short review from the reviewer's quota;
`--no-review` skips it and says that slice is not proven.

## 4. The first slice

Put it on the board with the boundary it will be judged against, then the loop the playbook
describes:

```bash
ao board add FIRST "what the slice does" --acceptance "what it must meet; the suite passes"
ao board ready                       # what may start now
# work, then stage exactly the candidate
ao verify -p quick
ao review
ao commit-ok
ao commit -m "…"
```

No push: that stays a person's act. `ao doctor` shows the state of everything above at any
time.

## 5. Update and uninstall

```bash
ao update --dry-run                  # how ao was installed, and the command that updates it
ao update                            # shows that command and asks; --yes runs it without asking
ao uninstall                         # a dry run: each job, hook and MCP entry it would take
ao uninstall --yes                   # take them off; --purge also deletes ~/.ao
```

`ao update` needs nobody to remember how ao went on. A git clone - the README's symlink to
`bin/ao` - is fast-forwarded with `git pull --ff-only`, and refused while it has uncommitted
changes, is on no branch or tracks nothing; a Homebrew, pipx or uv install is upgraded by its own
tool; a pip install by the pip of the interpreter running ao, with `--user` when it went into the
user's site directory. The command is printed before it runs.

`ao uninstall` takes off what ao set to run or load without anyone asking: its scheduled jobs, its
hooks in every project the machine registry knows - only files that are ao's exact bytes and
untracked, so a hook you edited stays - and the `ao` entry `ao init` put into their MCP files. Run
it before removing the program: once ao is gone, an enrolled project's hook fails every commit with
"ao not found". It names what it leaves: each project's `.ao/`, ledgers, mailbox and marker
(`ao remove --yes` in the project takes those, while ao is installed), `~/.ao` unless `--purge`,
and the program itself, with the command that removes it.

*In ao since slice UPDATE-UNINSTALL: `ao update` and `ao uninstall`. Update asks about the ao that
runs it, not the first one on PATH. Uninstall takes every launchd job in ao's namespace, those of
projects deleted since included, and on Linux every systemd user unit in it the same way
(LINUX-SCHEDULER); on Windows, where Task Scheduler has no namespace to list, the tasks of the
projects the registry knows. A systemd unit you wrote to run ao yourself is not ao's and stays, and
the dry run says so. A hook ao cannot prove untracked - one in a hooks directory outside
every repository - stays too, named, and the uninstall exits 1, as it does for anything it leaves
behind; `--purge` then keeps `~/.ao`, whose registry is how the next run finds what was left.*
