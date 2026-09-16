# Windows

What works, what does not, and how hosted runners exercise it.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`) |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`; the working directory is read from the process environment block (a 64-bit process, by a 64-bit Python), and where it cannot be read a turn is matched by the repository path on its command line |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min) |
| desktop notifications | a toast through PowerShell behind the `toast` feature switch, off by default; Telegram and e-mail carry the orange and red levels |
| commit hook (`ao hooks install`) | installed inside the repository; a shared, external or globally configured hooks directory is refused (#71); its execution proof does not pass yet (below) |
| pre-push hook | works under Git's own shell |

The hosted `tests` workflow runs only through manual `workflow_dispatch`, one
environment and interpreter at a time, on the maintainer's word (macOS minutes cost
10x, Windows 2x). Python 3.11 runs
on Ubuntu, Windows and macOS; Ubuntu also carries the Python 3.9 support-floor and
Python 3.12 release/newer lanes. Every lane runs the same suite, and each supported
runner must pass the process backend's native self-check rather than silently use
the shell fallback.

Hosted runners cover platform API behavior and deterministic process crashes: the
durability tests kill real child processes around storage barriers and use temporary
paths. They do not provide physical power-loss, storage-controller or filesystem
qualification, including unsupported and network filesystems. Faults found on a
hosted runner get a scenario in `tests/test_scenarios.py` like any other.

Not yet done, in order of value: `ao hold` proven on the Windows lane, and the commit
hook's execution proof. The hook body tells an absolute index path by its leading
slash, so under Git's shell a drive-letter path — the temporary index the proof hands
Git, or a linked worktree's index — is taken for a relative one and prefixed with the
working directory. ao then reads another index than the one Git commits and refuses.
That fails closed, but no proof passes on Windows until the hook recognises a drive
letter, and that is a new hook version.

## What the Windows lane skips, and why

A test that cannot pass on Windows is skipped there with its reason, never left out (#71):

- the commit hook's execution proof, for the reason above;
- a hook write that needs `--allow-shared-hooks`, which ao refuses on Windows;
- POSIX file modes: an executable hook, an owner-only credentials file;
- fixtures that run through a shebang or are POSIX shell scripts: a reviewer, a
  conformance harness, a filter named `git`, a fake `keyflip`;
- what Windows does not have: process groups and the CPU they spend, zombie
  processes, a directory fsync.
