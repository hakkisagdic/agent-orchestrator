# Getting started

From an empty checkout to a first reviewed commit. Every step is a command you type, and the
last setup step proves the guarantees instead of describing them. A test runs this sequence
in a temporary repository, so this page cannot drift from what ao does.

## 1. Install and put ao on the repository

```bash
pip install ao-orchestrator
cd your-repository
ao init --profile agy-codex          # or codex-agy, claude-codex
```

`ao init` writes `.ao/` (config, gates, board), the mailbox, the playbook and the MCP
registration, and the `.ao-project` marker, which you commit: it is what turns enforcement on.
It refuses when no reviewer answers, when the reviewer is of the implementer's own model family,
or when the quick gates exercise none of your code. Its authority, board, backlog and mailbox
files are in English, and so is what ao tells you afterwards - the digest, the handoff, alarms and
the phone; `ao init --language tr` has them in Turkish ([configuration](configuration.md)).

*In ao since slice LEADS-ADAPTERS: `ao init` writes in the project alone, and keeps files already there.*
Each file it makes - the marker, the first configuration, the board, backlog, authority and gates of
`.ao`, the mailbox's README, a harness's coordination - is made exclusively: a file, folder or link
that appears before the open is kept. A configuration that appears after init planned without one is
named, and init exits 1 before writing the rest; run init again to plan from it.
Existing configuration changes and the manifest are replaced whole. On POSIX a link at the manifest's
name is replaced without following it; Windows refuses it. The same tree primitives write
`.ao/project-key`; if that write is refused, the machine's registry keeps the name.

What init adds to - the playbook, a rule file with `--rules`, an MCP registration - is read and replaced
whole through no link. A rule link such as `CLAUDE.md -> AGENTS.md` is named and accepted when it resolves
inside the project to another rule file this run writes successfully: the pointer is written once,
through the target's own path. A link outside the project or to a file this run does not write is refused.
Other links where ao adds text are named, left alone, and init exits 1 having written the rest.

On macOS and Linux each folder is opened from the project's root down, relative to the one before it
with `O_NOFOLLOW`; the leaf is opened relative to the held folder. On Windows native handles hold every
folder without delete sharing until the file operation ends, so no folder can be renamed or replaced
meanwhile; reparse points, including links and junctions, are refused ([windows](windows.md)).
A platform offering neither safe way refuses the operation. The opt-in ledger used by
`--review-tier same-family`, its lock and its seal use the same held folders, preserving the chain and
machine checkpoint. Known JSON MCP formats are merged by ao itself; for another format ao refuses to
run a vendor command that could be redirected, names the command for you to run, and exits 1.
Init appends only to a `.gitignore` that is a regular file with one name, opened through no link;
otherwise it names the lines for you to add. `ao skill install` writes the playbook as init does,
and exits 1 when it did not write one.

With one harness for both roles, a same-family reviewer is refused until you choose a
[review tier](roles.md#review-tiers): another model of the same family, labeled as weaker
independence wherever its reviews show, or no model reviewer at all and a person reviewing each
candidate.

```bash
ao init --implementer claude-code --review-tier same-family --by <name>
ao init --implementer claude-code --review-tier person
```

## 2. Wire what makes the guarantees hold

```bash
git add .ao-project .gitignore && git commit -m "adopt ao"
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

*In ao since slice TRIAGE-HIGHS: such a project whose `.ao/config.json` cannot be read is refused
by `ao hooks install` too: its old hook refuses every commit, and the current one would have let
each through. `ao hooks install`, `ao doctor` and `ao prove` name what cannot be read, to make
readable first, and then `ao init --adopt`, which refuses the project until then.*

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

*In ao since slice LEADS-INSTALL: each run keeps its review in an archive of its own,
`prove-<time>`, then `-2` and on for another run in the same second, which was merged into the
first; an archive that cannot be written is said, and the worktree is removed all the same, where
it stayed registered in the checkout. The throwaway borrows the untracked dependency directories
`merge.link_paths` names (`.venv` and `node_modules` unless set) from this checkout, as
`ao merge-check` does, so that its gates can run: a gate that writes into one - an install, a
cache - writes into this checkout's.*

## 4. The first slice

Put it on the board with the boundary it will be judged against, then the loop the playbook
describes:

```bash
ao board add FIRST "what the slice does" --acceptance "what it must meet; the suite passes"
ao board ready                       # what may start now
ao slice start FIRST                 # it runs, and the move is recorded
# work, then stage exactly the candidate
ao verify -p quick
ao review
ao commit-ok
ao commit -m "…"
```

An id is what the board carries whole between a line's brackets and what another item's `needs`
can name: no space, comma, `]`, backslash or `·`, and no `(` or `/` first. A phase of a plan, such
as `ACME-187/1`, is baselined on `.ao/plans/ACME-187.md` as it stood when the item was admitted, so
a later edit of the plan shows as drift. `--needs A,B` is read the way the board reads a needs note,
and `--role` names the role that takes the item when that is not the implementer.

*In ao since slice BOARD-ADD-2: ids such as `ACME-187/1` are admitted, and `--needs` is checked as
the board will read it; before, the first were refused as ids no line could carry, and a dependency
could pass the check and be written as two ids the board did not hold.*

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

*In ao since slice UPDATE-UNINSTALL-2: an MCP entry is ao's when it runs `ao … mcp serve`, with nothing after
it but ao's own `--role` and `--allow-verify`, whatever its command is called. An entry named `ao` whose
program was another one called ao was taken for ao's on its name alone, and removed. `ao remove --yes` finds
a project's entries as the uninstall does, under the key each adapter declares, and a file it cannot read
stays, named, and the removal exits 1.*

*In ao since slice LEADS-INSTALL: an entry is the project's only when the `-C` right before `mcp serve`
names that project, as every registration `ao init` wrote does: one serving another project, or any
program whose arguments end in `mcp serve`, was taken and removed with it. The file is found again as it
is written, and must lie in the project: a link swapped in after the removal began sent the write
outside. On macOS and Linux each directory from the project down is opened without following a link;
Windows pins each folder with native handles that prevent its replacement. MCP edits and project state
deletions use these held folders and refuse where neither backend is available. A change another program made to the file meanwhile
is not written over: the new file is written and synced first, and the file is read again as the last
thing before it is replaced or deleted. A file that changed stays as it now is, named, and the removal
exits 1. What files cannot close is the instant between that reading and the rename, a few system calls
long: a program that replaces the file in it loses its change, and no lock helps, since the harnesses
and editors that write these files take none. A file emptied of ao's entry is deleted with its
directory synced on macOS and Linux, as a file ao replaces is.*

## 6. Shell completion

```bash
mkdir -p ~/.zfunc && ao completion zsh > ~/.zfunc/_ao    # and fpath=(~/.zfunc $fpath) before compinit
mkdir -p ~/.local/share/bash-completion/completions && ao completion bash > ~/.local/share/bash-completion/completions/ao
ao completion fish > ~/.config/fish/completions/ao.fish
```

In PowerShell the line `ao completion powershell | Out-String | Invoke-Expression` goes into the
profile `$PROFILE` names. bash loads the file above through the bash-completion package; without it,
source the file from `~/.bashrc`.

*In ao since slice SHELL-COMPLETION: `ao completion <shell>` prints a script generated from ao's own
parser each time it runs - the commands, their long options and the choices each declares - so a
script is as current as the ao that printed it: print it again after `ao update`. Completing a word
runs no program, ao included, and reads no project. Every word from the parser is written into the
script as a literal quoted for its shell, and every word the script completes is quoted again on the
command line, so a choice holding a quote or a `$` can neither break the script nor run anything.
Where a word has no choices, the shell completes file names.*

*In ao since slice LEADS-OPS: a script knows how many words each option takes - the next alone, an
optional one that does not begin with `-`, a count, each word up to the next option, as
`ao review --paths` takes, or every word after it - and completes each of them as that option's.
Every option that took any word was taken for one that takes the next alone, so the word after
`ao review --paths a.py` was offered `ao review`'s actions instead of a file name.*
