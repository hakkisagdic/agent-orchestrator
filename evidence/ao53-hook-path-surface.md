# AO53 hook-path surface inventory

Inventory revision ID: `AO53-I3`. The stable evidence path is intentional. Every
review request and artefact must record both this revision ID and AO's exact
candidate digest/tree; either mismatch makes the request stale. The file cannot
embed its own candidate digest without a recursive self-hash.

## Pre-implementation status and exact boundary

This is a pre-implementation inventory. “Required test” entries are acceptance
obligations, not completed proof; only the measured-defect table describes the
current implementation.

Human decision `D-1788908734=a` keeps backlog #53 as one slice. AO53 fixes where
AO resolves, reads, creates, upgrades, reports, and removes Git hooks. Any
mutation in a shared or external hook directory, or at a target selected by a
winning system/global `core.hooksPath`, is refused by default and needs explicit
`--allow-shared-hooks`. Backlog #59 separately proves execution and enforcement.
AO53 does not inspect an existing file's execute bits, alter a hook to probe it,
run a synthetic Git commit, or call static presence `installed`.

## Effective-path resolver

1. Run `git -C <cfg-root> rev-parse --show-toplevel`.
2. From top run `rev-parse --git-dir`, `--git-common-dir`, `--git-path hooks`,
   `git config --null --show-origin --show-scope --get core.hooksPath`, and
   `git worktree list --porcelain`.
3. Path/repository commands require exit 0/non-empty output. Config exit 1 plus
   empty output means unset. Config exit 0 must be exactly three NUL-terminated
   fields — scope, origin, value — with no remainder; NUL separation preserves
   tabs/newlines in origin or value. The value may be empty and is still set:
   Git resolves an empty value through `--git-path hooks` (the admitted Git
   returns `./`), so AO uses that non-empty path result rather than treating the
   config as unset. Malformed output or any other status is failure.
4. Preserve absolute output; normalize relative output against top. Never guess
   `.git/hooks`. Use normalized paths for I/O/display; use `realpath` plus
   `commonpath` (never string prefix) for identity.

`--git-path hooks` runs from top, so nested explicit `ao -C` cannot rebind a
relative hooksPath. Without configuration Git supplies common hooks, not a
linked-worktree private dir. Tests cover the installed Git and capture argv; no
older-binary qualification is claimed and `--path-format` is not used.

Every Git command uses argv and a copied environment with inherited repository,
config-injection, discovery, and pathspec overrides removed: `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_PREFIX`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_CONFIG`,
`GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_SYSTEM`, `GIT_CONFIG_NOSYSTEM`, all
`GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*`, `GIT_CEILING_DIRECTORIES`,
`GIT_DISCOVERY_ACROSS_FILESYSTEM`, and all Git pathspec-mode variables.
Repository path queries pass Git global `--literal-pathspecs`.

Resolution contains AO root/top/project-relative path; active/private/common
dirs and canonical identities; worktree records; each reachable worktree's
effective hooks; raw hooksPath plus winning scope/origin; family identity;
sharing and global-configuration authorization facts; deduplicated legacy and
repository-source dirs. A `system` or `global` winning scope is
`globally-configured` regardless of where its effective directory happens to
resolve; `local` and `worktree` are not.

## Sharing and legacy inventory

Worktree facts avoid newer porcelain lock/prune fields:

- a `bare` record is a valid base despite no `.git` child;
- a non-bare path with `.git` marker is reachable;
- missing non-bare path maps via admin `gitdir`: regular admin+`locked` is
  unavailable/shared; regular unlocked missing target is stale;
- symlinked/unreadable/unmappable admin is unknown, conservatively shared, never
  followed for mutation, and blocks remove;
- base main/bare counts once and is never unknown.

For reachable non-bare worktrees query each effective hook dir. Directory class
order is:

1. `shared`: same canonical dir effective for 2+ reachable worktrees; common
   hooks with live/locked/unknown count >1; or absolute hooksPath with count >1
   while unavailable worktree prevents comparison.
2. `external`: sibling-private hooks or any dir outside invoking worktree/current
   private metadata.
3. `project-local`: remaining dirs inside invoking worktree/current private
   metadata, including per-worktree relative hooksPath.

Output names facts, e.g. `shared (2 reachable; 1 locked; 1 stale ignored)`.
Family identity is canonical common-git-dir. Directory class and the
`globally-configured` authorization fact are independent: a system/global value
still requires `--allow-shared-hooks` even if it resolves project-locally.

Old resolver wrote `<git-dir>/hooks` from any worktree. Inspect current private,
common, every direct regular non-symlink `<common>/worktrees/<admin>/hooks`, and
private dirs Git resolves from reachable worktrees. Also inspect canonical
repository source `<top>/.githooks` whenever either role target exists or is in
the index, even when it is not effective in this checkout; this exposes the
tracked shipped pre-commit in a fresh clone whose config does not select it.
Unknown admin is a blocker. A candidate is `potentially-effective` when active
for any reachable worktree, common fallback, or under locked/unknown admin;
other private and repository-source candidates are `dead-misplaced`. Canonical
duplicates are removed. Sibling-private mutation is external. No arbitrary
hooksPath history registry.

## Measured defect

| Git measurement in admitted linked worktree | Absolute result |
|---|---|
| `--git-dir` | parent `.git/worktrees/agent-orchestrator-ao53-hook-paths` |
| `--git-common-dir` | parent `.git` |
| `--git-path hooks` with local `.githooks` | this worktree `.githooks` |
| current AO resolver | private worktree git-dir `/hooks` (dead) |

Effective `.githooks` has tracked custom `pre-push` and no `pre-commit`; current
status/doctor inspect the dead private dir.

## Static ownership and target track state

Role grammar: exact pre-commit current/legacy invokes `commit-check`; pre-push
invokes `push check`; legacy may name another same-family worktree.

- `absent`: `lexists` false; target track state is still measured.
- `foreign`: symlink, unreadable, undecodable, or custom content without
  plausible non-comment AO role invocation. Marker alone is foreign, so
  repository test `.githooks/pre-push` stays custom.
- `ambiguous-ao`: plausible AO invocation not exact current/legacy; never mutate.
- `current-local (behavior unverified)`: byte-exact canonical portable
  project-local generated body, or byte-exact repository pre-commit at canonical
  tracked `<top>/.githooks/pre-commit`.
- `current-scoped (behavior unverified)`: byte-exact canonical shared/external
  generated body with current family/class binding.
- `legacy (behavior unverified)`: exact prior generated grammar, stale binding,
  or a CRLF-only translation of an otherwise exact current/prior body.

Canonical generated bytes are UTF-8, LF-only, and end with exactly one LF.
Renderers produce bytes; readers compare bytes without universal-newline
translation; writers use binary mode. New tracked `.githooks/pre-commit` is
paired with repository `.gitattributes` entry
`.githooks/pre-commit text eol=lf`, so a normal checkout, including
`core.autocrlf=true`, retains canonical LF bytes. A CRLF-only untracked body is
eligible legacy. A CRLF tracked repository body is a protected working-tree
modification: preserve it, report legacy plus `git restore --
.githooks/pre-commit` as the repair, and do not call it current. Mixed endings or
other edits follow the foreign/ambiguous rules. Tests cover canonical LF,
`core.autocrlf=true` checkout, untracked CRLF legacy, tracked CRLF restoration,
mixed endings, and an undecodable file.

Measure track/target state for existing and absent paths. Walk ancestors and
inspect structural worktree roots (ancestor with `.git` file/dir/symlink).
At each, sanitized `rev-parse --show-toplevel`; timeout, execution/dubious-
ownership/unreadable-marker/non-contained/other failure is `indeterminate`.
Deduplicate enclosing tops; run `git --literal-pathspecs ls-files
--error-unmatch -- <relative>`. Any exit 0 is `tracked`; all discovered exit 1
is `untracked`; unexpected status is `indeterminate` unless tracked dominates.
No structural owner is `unobserved` for absent target and `indeterminate` for an
existing file, protecting environment-addressed bare dotfiles. Symlinks stop
foreign.

Existing mutation eligibility is current/legacy+untracked. Absent creation needs
untracked, or unobserved plus explicit shared/external/globally-configured
authorization. Tracked or indeterminate absent target is refused, so a
deleted-but-indexed repository hook must be restored by Git rather than replaced.

## Two generated bodies

A project-local generated body is portable: it embeds only the project-relative
AO root, derives it from Git's documented initial top-level hook cwd, and checks
`[ -d "$root/.ao" ] || exit 0` before Git/AO lookup. It resolves `ao` at runtime
with `command -v`, absolutizes relative `GIT_INDEX_FILE` against initial cwd,
and invokes the role command. It contains no machine absolute path or family
identity, so an untracked local `.githooks/pre-commit` accidentally staged into
source is portable rather than machine-bound (but remains behavior-unverified).

A shared/external body embeds canonical family identity, relative AO root, and
class. It absolutizes/preserves index, clears repository exports, resolves
common dir relative to initial cwd via Git plus `CDPATH= cd "$path" && pwd -P`,
and exits 0 on measured family mismatch. After a match it resolves top/root,
exits 0 without `.ao`, and invokes AO; matching-family failure is nonzero. If
family cannot be measured it exits 0 to avoid denying an unrelated/bare repo.
Status/doctor therefore label it `routing unprobed`; #59 owns runtime proof.

Tracked repository pre-commit uses the portable local body, starts with the
no-`.ao` check, and is recognized as repository-owned only at its canonical
tracked project-local path.

## Install, modes, and authorization

Install mutates only the active role targets; misplaced candidates are reported
and are cleaned by uninstall/remove under their own full preflight. Active roles
are independently preflighted:

1. current: no-op;
2. eligible absent: create the body selected by directory class;
3. untracked legacy: upgrade to selected body;
4. foreign/ambiguous/tracked/indeterminate: preserve/report unavailable.

Authorize every planned shared/external/globally-configured mutation before any
write. If any eligible planned mutation needs authorization and the flag is
absent, the whole command returns 1 before all writes; it never skips the
unauthorized target and mutates an authorized/local sibling. For writes, create
missing active dir, refuse existing non-directory, re-resolve/reclassify, then
use a same-dir temp, write canonical bytes in binary mode, flush and `fsync`, set
exact `0o755`, and `os.replace`. Never overwrite except eligible legacy. Install
returns 0 only when both roles end current; otherwise 1, although an eligible
other role is safely installed when no command-wide authorization refusal
exists. Custom pre-push therefore stays byte-identical while pre-commit is
created and the command explicitly reports AO push-window hook unavailable.

Writer tests assert canonical bytes and `0o755`. The tracked repository file is
validated as canonical LF bytes and `0o755` in this checkout and Git index mode
`100755`; `.gitattributes` is validated with `git check-attr` and a
`core.autocrlf=true` checkout. These are source-candidate properties, not runtime
execution qualification. AO53 does not detect or automatically repair a later
mode change. #59 owns that report/probe.

`--allow-shared-hooks` applies to install/uninstall/remove. Any eligible shared,
external, or globally-configured mutation triggers the command-wide refusal
above without it. It never permits foreign/ambiguous/symlink/tracked/
indeterminate mutation.

## Uninstall and remove truthfulness

Uninstall inventories both roles in the active directory and every deduplicated
legacy/repository-source candidate above. It preflights the whole set, then
plans deletion of every eligible current/legacy target, including eligible
`dead-misplaced` files. If any planned delete is shared, external, or
globally-configured and `--allow-shared-hooks` is absent, uninstall returns 1
before every deletion; it does not skip that target and delete project-local
ones. Resolver, unknown-admin, authorization, or deletion failure never claims
success. Protected files remain byte-identical and are reported with class,
track state, and reachability.

After attempted deletion uninstall returns 1 whenever any current/legacy/
ambiguous AO form remains `potentially-effective`; it returns 0 only when none
does. A protected `dead-misplaced` form warns but does not alone make the result
nonzero. Foreign custom hooks also do not alone fail uninstall. Therefore, when
the tracked canonical repository pre-commit is effective, uninstall preserves
it and necessarily returns 1; the output says it is repository-owned/tracked
rather than pretending removal succeeded.

`remove --yes` classifies reachability before applying protected-form blockers
and preflights everything before deleting state:

- resolver/unknown-admin/auth failure aborts intact;
- an eligible current/legacy form is removed across active and misplaced
  candidates, subject to command-wide authorization;
- any protected `dead-misplaced` form is preserved and warns but never blocks,
  regardless of whether its static form is current, legacy, or ambiguous; thus
  an inactive tracked `<top>/.githooks/pre-commit` in a fresh clone cannot make
  removal permanently impossible;
- among `potentially-effective` candidates, protected ambiguous, legacy, or
  `current-scoped` aborts intact;
- a protected, potentially-effective `current-local` also aborts unless its
  directory is provably single-worktree worktree-local: inside invoking top,
  effective only there, not common fallback, not shared/external/
  globally-configured, and not a legacy location;
- only that protected potentially-effective current-local may remain after a
  successful remove: its no-`.ao` check occurs before Git/AO lookup, so deleting
  this project's state makes it statically inert; report preserved-and-inert;
- foreign remains user-owned.

A protected shared/external/globally-configured hook is never called inert:
another same-family worktree or globally selected repository may still have
`.ao`, so remove must delete it with authorization or abort while retaining
state.

## Status and doctor

`hooks status` prints resolver failure or effective path/class/facts, winning
hooksPath scope/origin (including a set-but-empty value), both active
static/track states, and every AO misplaced path with reachability, including
inactive canonical `<top>/.githooks` AO content.

Interactive doctor prints commit and push states. Commit
absent/foreign/ambiguous/legacy or potentially-effective misplaced pre-commit is
a `commit-hook` problem. A tracked CRLF repository pre-commit includes the
`git restore -- .githooks/pre-commit` repair hint; `.gitattributes` prevents that
state in a normal fresh checkout. A current-scoped pre-commit adds
`commit-hook-routing-unverified` because its deliberate unmeasurable-family
branch is fail-open until #59 supplies a probe. `_doctor_check` returns nonzero
and uses its existing notifier for both problem keys.

AO53 deliberately creates no pre-push `doctor_problems` key. Absent, foreign,
ambiguous, legacy, misplaced, and scoped-routing-unprobed pre-push states are
always explicit informational output (`AO push-window hook unavailable` or
`behavior unverified`) and any non-current active state makes `hooks install`
return 1. This covers absent as well as this repository's intentionally
preserved tracked custom pre-push without creating an unfixable hourly alarm.
Chaining/replacing custom pre-push and runtime enforcement are outside #53.

## Implementation entry-point map

- `src/ao/cli.py`: replace `PRE_COMMIT_HOOK`/`PRE_PUSH_HOOK` text constants with
  canonical byte renderers; replace `_ao_hook_state` and `_ao_hook_paths` with
  the resolver/inventory/classifier while retaining compatibility only where
  tests need a direct helper; route `cmd_hooks`, the `cmd_remove` hook preflight,
  `doctor_problems`, interactive `cmd_doctor`, `_doctor_check`, and the `hooks`
  and `remove` parsers in `main` through that single result.
- `.gitattributes`: add exactly `.githooks/pre-commit text eol=lf`;
  `.githooks/pre-commit`: add the exact portable repository body as mode
  `100755`; `.githooks/pre-push` remains byte-identical custom/foreign.
- `tests/test_hook_paths.py`: new real-topology and matrix coverage, including
  NUL config parsing, empty configured value, authorization all-or-nothing,
  fresh-clone dead tracked source, and autocrlf checkout.
- `tests/test_skill.py`: update its direct `_ao_hook_paths` coupling and existing
  remove/interactive-doctor expectations; `tests/test_switches_and_bypass.py`
  updates only corrected hook path/state/role expectations.
- `README.md`, `README.tr.md`, `docs/features.md`, and `docs/safety.md`: document
  `--allow-shared-hooks`, refusal for shared/external/global configuration,
  independent role outcomes, truthful install/uninstall status, and that a
  recognized static hook is intended to invoke `commit-check` but remains
  behavior-unverified until #59 probes execution. CLI help in `src/ao/cli.py`
  states the same authorization contract.
- `src/ao/skill/SKILL.md`: apply the same static-intent/runtime-proof wording to
  the payload installed into other repositories; no generated client-specific
  copy is edited by hand.

## Complete product surface and required tests

| Surface | Required behavior/test |
|---|---|
| resolver | real normal/relative/absolute/linked/bare-adjacent topologies; NUL-safe config-unset/empty and local/worktree/global/system scope; poisoned env; literal glob; argv has no path-format |
| sharing/legacy | main/linked/bare/live/stale/locked/unknown facts; configured shared path; globally-configured authorization; complete safe private/common/sibling and canonical repository-source candidates |
| target ownership | existing and absent literal index checks; tracked-missing refusal; unobserved external explicit creation; bare-dotfiles preservation; canonical LF/current, autocrlf checkout, untracked CRLF legacy, tracked CRLF restore, mixed/undecodable refusal |
| install | independent active-role matrix; local portable vs scoped body; mkdir/recheck; command-wide auth-first; canonical binary write; `0755`; custom sibling preserved; truthful exit |
| status | exact path/class/config facts/states/misplaced reachability; set-empty config; inactive tracked repository AO hook; scoped routing-unprobed label |
| uninstall | active plus every misplaced candidate; eligible dead cleanup; command-wide auth-first/no partial local delete; effective tracked hook remains/nonzero; dead protected warning |
| remove | shared/global/protected effective blocker retains state; dead protected never blocks; only provably single-worktree early-inert effective local current survives |
| doctor | commit placement and scoped-routing problems notify; tracked CRLF has restore path; every non-current push state is explicit non-alarming information |
| templates | local contains no machine path and early no-`.ao`; scoped mismatch/no-AO/unmeasurable routing; relative index reaches AO test double unchanged |
| repository hooks | `.gitattributes` forces LF for pre-commit; pre-commit exact canonical portable body, local file `0755`, index `100755`; pre-push byte-identical custom/foreign |
| docs/help/payload | EN/TR command rows, features, safety, parser help, and installed skill payload agree on authorization, static intent, behavior-unverified status, and exits |
| init/generic status | unchanged except the corrected skill payload text; existing regressions; #59 execution probe excluded |

The similarly named `cmd_remove` `~/.ao` substring cleanup defect is confirmed:
`if key in f` can remove another worktree's `push-<key>.ok`. Decision
`D-1788908734=a` did not admit this per-user-state namespace into AO53, so it is
unchanged and no AO53 evidence claims it fixed. Backlog #66 owns the defect:
that slice replaces basename/substrings with stable per-project identity for
push windows, state, logs, heartbeats, locks, and their cleanup.

## Mechanism ladder

Human choice requires shared/global authorization. Existing install does not
authorize unrelated repositories; ordering cannot make a global path local;
deleting hook management removes accepted surface. No dependency/subsystem: one
argparse flag, ordered classifier, per-role ordering, two static templates, and
one path-specific Git attributes rule selected by the already-computed location
class. The same flag guards all shared/external/globally-configured mutations.
No history registry is needed: Git/admin entries plus the canonical repository
source supply paths/reachability; static grammar/family covers current/stale/
ambiguous; conservative structural discovery supplies track state.

## Explicit #59 limitation

A signature-bearing hook can be non-executable or behavior-changed while
retaining recognized text. AO53 reports only behavior unverified. Backlog #59
must inspect/execute Git's resolved hook in a synthetic refusal probe before any
surface claims installation/enforcement.
