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
git add .ao-project && git commit -m "adopt ao"
ao hooks install                     # the pre-commit hook that refuses unauthorised commits
```

The marker goes first. Until `.ao-project` is tracked the current hook stands aside, so
`ao hooks install` refuses a project whose `.ao/config.json` already governs its commits: put in
place before the marker, the hook would switch that authority off.

The reviewer is another model family than the implementer's, another model of its family where
a person opted in, or a person with `ao person-review --by <name>`; a reviewer that is the same
actor, or of no [review tier](roles.md#review-tiers), is refused.

### A project an older ao set up

A project set up before `.ao-project` existed has everything but the marker: `.ao/` with its
config, board, ledgers and decisions, a commit hook that runs `ao commit-check`, and no
`.ao-project` anywhere in its history. Its config governs its commits, `ao hooks install`
refuses it for the reason above, and `ao doctor` names the one command that adopts it:

```bash
ao init --adopt
```

It writes the exact marker if the working tree has none and stages it, so the project is
enrolled from that moment. It then installs the current hooks the way `ao hooks install` does,
has Git run the commit hook to prove that it refuses, and says what it wrote, what it kept and
what is left to commit: the staged `.ao-project`, landed through `ao verify`, `ao review`,
`ao commit-ok` and `ao commit`. Keep it staged until then; the current hook enforces while the
marker is in the index or in HEAD. Where Git runs this repository's hooks from a shared or
external place, as it does for linked worktrees, `ao init --adopt --allow-shared-hooks`
authorizes the install.

*In ao since slice INIT-ADOPTION: `ao init --adopt` enrolls AO state that no tracked
`.ao-project` covers - a project an older ao set up, or one whose marker `ao init` wrote and
nobody staged - in one step, where the commit hook's refusal, `ao hooks install` and `ao doctor`
each spelled out a recipe of their own. Adoption writes nothing under `.ao/`, so every ledger and
decision stays as it was; a `.ao-project` that is not exactly `ao-project-v1` is refused, never
replaced, and a staged marker that does not read back as one is unstaged again, which leaves the
project governed as it was. Run again, it leaves the marker where it is and only makes sure of the
hooks. It exits 0 only when Git was seen to run the commit hook. The options of a new setup, such
as `--profile`, are refused beside it; `ao doctor`, `ao doctor --check`, `ao prove`, the commit
hook's refusal and `ao hooks install` name it wherever they meet such a project.*

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
