# Demo

`ao demo` shows what ao guarantees in about a minute, before ao is on any repository of yours: an
agent's change whose tests pass and whose code is wrong is found by a reviewer of another model
family, and nothing lands until the fix is reviewed - not through ao, and not through a plain
`git commit`. It needs ao and git and nothing else: no harness, no account, no model.

```bash
ao demo            # the scratch folder is removed at the end
ao demo --keep     # it stays, and the last lines say where
```

## What it plays

1. **A project of its own.** A folder in the temporary directory holds a home of its own, a git
   project - `calc.py` with `add(a, b)`, its test and a `.gitignore`, in one commit - and the
   stand-in reviewer.
2. **ao on it.** The reviewer and one gate, the project's tests run with `python -m unittest`, are
   written into `.ao/config.json` and `.ao/gates.json` first. `ao init --profile agy-codex`
   keeps both, composes the implementer and the architect from the profile, and its reviewer probe
   runs the stand-in. The marker and the `.gitignore` init added to are committed, and
   `ao hooks install` puts the commit hook in place.
3. **A change with a bug.** The agent adds `divide(a, b)`, which divides, and a test that divides 6
   by 3. `ao verify` passes. `ao review` says NEEDS_CHANGES with a HIGH finding: nothing in
   `divide` handles `b == 0`. `ao commit-ok` refuses, and so does the hook at a plain `git commit`.
4. **The fix.** `divide` refuses `b == 0` with a `ValueError`, and a test says so. `ao verify`
   passes, `ao review` says APPROVED, `ao commit-ok` grants, and `ao commit` lands the candidate
   with the Ao- trailers that name its grant. `ao commit-check --range HEAD~1..HEAD` holds the
   commit to them, and `ao digest` reads the ledgers back: two verifications, a review of each
   verdict, one grant and one refusal.
5. **The scratch folder** is removed, or kept with `--keep`.

Each step says what it expects of every command it runs. The demo exits 0 only when each one
behaved so; otherwise it stops, exits 1 naming the step and what the command did instead, and
removes the folder unless `--keep` asked to keep it.

## What stands in

**The reviewer is a script.** `reviewer.py` in the scratch folder, run as
`python reviewer.py <prompt>`, echoes the nonce of the probe `ao init` sends, and answers a review
in ao's verdict format ([protocol](protocol.md#verdicts)). It asks one question of the staged
`calc.py`, the copy of the candidate's tree ao unpacks where a reviewer runs: does `divide` handle
a divisor of 0? It is declared in `.ao/config.json` as any reviewer is, with an id and a family of
its own, `stand-in`, and ao holds it to the rules it holds every reviewer to: it is not the
implementer's session, the implementer states no family it shares, and it runs another program than
the implementer's harness, so each of its reviews stands in the tier "another model family"
([review tiers](roles.md#review-tiers)). Nothing is waived and no setting is changed.

**The implementer is a name.** The profile names the harness that implements; the demo stages each
change in its place, and no harness runs or needs to be installed.

## What it touches

Every ao command runs as `python -m ao` in the scratch project, with HOME and USERPROFILE naming the
scratch home: `~/.ao` - the registry, the settings, the gate lock, the event log - and git's identity
are the folder's, never yours. ao's own variables, git's and the XDG directories are left out of
each command's environment, temporary files go into the folder too, and no Python it starts writes
bytecode beside the ao it imports. `ao init` runs with `--no-mcp`: registering an MCP server can
run a harness's own command where it is installed, and the demo starts none. An `ao` in the
folder's `bin`, first on PATH, is the ao that runs the demo, so the commit hook runs that ao too,
from a clone as from an installation. Without `--keep` the folder is removed at the end, and only a
folder the demo made in the temporary directory is ever removed.

## A run

Trimmed: the playbook and next-step lines `ao init` prints are cut, and so are this machine's
paths.

```text
ao demo: an agent stages a change whose tests pass and whose code is wrong. A reviewer of another
model family finds it, and nothing lands until the fix is reviewed: not through ao, not through
a plain git commit. Nothing outside a scratch folder is touched, no model is called, and nothing
is spent: the reviewer is a stand-in script.

1. a project of its own
   scratch folder  /tmp/ao-demo-to9zzux1  (written <scratch> from here on)
   home            <scratch>/home: ~/.ao and git's identity are these, not yours
   project         <scratch>/project: calc.py with add(a, b), test_calc.py, .gitignore, one commit
   reviewer        <scratch>/reviewer.py: a stand-in script, not a model

2. ao on it, the reviewer a stand-in of another family
   .ao/config.json  the reviewer: stand-in-reviewer, family stand-in, runs <scratch>/reviewer.py
   .ao/gates.json   one gate, the project's tests: python -m unittest
   $ ao init --profile agy-codex --agent antigravity --no-mcp
     reviewer: python
     reviewer stand-in-reviewer exited 0 after 0.0s
       wrote  .ao/config.json (+implementer, architect)
       wrote  .ao-project
       wrote  .ao/board.md
       …
       wrote  .gitignore (+6)
       kept   .ao/config.json
       kept   .ao/gates.json
       …
       commit hook not installed — active pre-commit intent is absent / untracked
       reviewer probe ok — stand-in-reviewer via python (3.14.7); exact nonce echoed
       …
   the next steps ao init names are for a repository of your own; the demo goes on without them
   $ git add .ao-project .gitignore
   $ git commit -q -m "adopt ao"
   $ ao hooks install
     installed pre-commit — <scratch>/project/.git/hooks/pre-commit
     installed pre-push — <scratch>/project/.git/hooks/pre-push
     current (behavior unverified) — static hook intent installed; run `ao hooks status` for execution proof
   from here on a commit lands only with a grant for its exact staged tree

3. the agent stages a change with a bug
   the agent adds divide(a, b) to calc.py, and a test that divides 6 by 3:
     + def divide(a, b):
     +     return a / b
   $ git add calc.py test_calc.py
   $ ao verify
     ▶ tests  python -m unittest -q
       pass  exit 0  0s

     PASS  recorded as V-1791663271
   $ ao review --boundary "calc.divide(a, b) returns a divided by b and raises ValueError when b is 0"
     size: product 1 path(s) +4/-0, tests 1 path(s) +4/-1
     reviewer: python
     reviewer stand-in-reviewer exited 0 after 0.1s

     NEEDS_CHANGES  BLOCKER 0 · HIGH 1 · MEDIUM 0 · LOW 0
     semantic-review/2026-10-10-231432-88d8f85.md
       - [HIGH] calc.py:9 - divide(1, 0) raises ZeroDivisionError: nothing in divide handles b == 0, where the boundary asks for a ValueError
   $ ao commit-ok
     REFUSED
       · no APPROVED prospective review is bound to this staged candidate
   $ git commit -m "add divide"
     COMMIT REFUSED
       · latest authority decision refused this commit
   nothing landed: the tests passed, the review found the bug, and HEAD is still 88d8f85c4bf8

4. the agent stages the fix
   the agent makes divide refuse b == 0, and tests that it does:
     + def divide(a, b):
     +     if b == 0:
     +         raise ValueError("divide: b must not be 0")
     +     return a / b
   $ git add calc.py test_calc.py
   $ ao verify
     ▶ tests  python -m unittest -q
       pass  exit 0  0s

     PASS  recorded as V-1791663275
     newest review: NEEDS_CHANGES (2026-10-10-231432-88d8f85.md)
   $ ao review --boundary "calc.divide(a, b) returns a divided by b and raises ValueError when b is 0"
     size: product 1 path(s) +6/-0, tests 1 path(s) +8/-1
     reviewer: python
     reviewer stand-in-reviewer exited 0 after 0.0s

     APPROVED  BLOCKER 0 · HIGH 0 · MEDIUM 0 · LOW 0
     semantic-review/2026-10-10-231436-88d8f85.md
   $ ao commit-ok
     GRANTED  C-1791663277067680000
       verified V-1791663275 · review 2026-10-10-231436-88d8f85.md
       index tree fee8909bda50bfccb07a607ec406c70638454dae

       This grant names only the staged index above; push is never covered.
   $ ao commit -m "feat: add divide, which refuses b == 0"
     AUTHORIZED  fee8909bda50bfccb07a607ec406c70638454dae · C-1791663277067680000
     [main 71b3308] feat: add divide, which refuses b == 0
      2 files changed, 14 insertions(+), 1 deletion(-)
   $ git log -1 --format=%B
     feat: add divide, which refuses b == 0

     Ao-Grant: C-1791663277067680000
     Ao-Tree: fee8909bda50bfccb07a607ec406c70638454dae
     Ao-Verification: V-1791663275
     Ao-Review: 2026-10-10-231436-88d8f85.md
   $ ao commit-check --range HEAD~1..HEAD
       matches   71b33089c0d9  C-1791663277067680000 · tree fee8909bda50 · on record
     TRAILERS MATCH  1 commit(s) checked in HEAD~1..HEAD
   $ ao digest
     calc  last 24 hours

       state    unknown

     ── LANDED WORK ──────────────────────────────────────────
       3 commit, 3 unpushed
         71b3308 feat: add divide, which refuses b == 0
         88d8f85 adopt ao
         5bcc29d calc: add(a, b) and its test

     ── GATES ────────────────────────────────────────────────
       verify     2 passed
       review     1 APPROVED, 1 NEEDS_CHANGES
       commit-ok  1 granted, 1 refused

     board:   |  alerts: 0 sent, 2 suppressed

5. the scratch folder
   removed /tmp/ao-demo-to9zzux1

DEMO PASSED  every step behaved as it says. To put ao on a repository of your own:
docs/getting-started.md, or `ao init --profile agy-codex` in it.
```

AUTHORIZED is `ao commit`'s check, the one the commit hook runs. `ao commit` then writes the commit
itself, the tree the grant bound on the commit it was built on, and runs no hook.

*In ao since slice DEMO: `ao demo` plays the guarantee on a project of its own. The README states
it and `ao prove` checks it on a project that has ao, so the first refusal anyone saw was on their
own repository. The demo shows one before that: in a scratch folder with a home of its own, through
the command line a person types, with a reviewer that is a script and still held to every rule a
reviewer is held to.*
