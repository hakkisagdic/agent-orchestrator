# Windows

What works, what does not, and how hosted runners exercise it.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`) |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`; the working directory is read from the process environment block (a 64-bit process, by a 64-bit Python), and where it cannot be read a turn is matched by the repository path on its command line |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min). Each task names a program that exists — the console script on PATH, a clone's script, or `python -m ao.watchdog` from an interpreter that imports an installed ao — or install refuses; install exits 1 when `schtasks` cannot create a task, and `ao remove --yes` deletes both tasks in its own process and checks they are gone |
| desktop notifications | a toast through PowerShell behind the `toast` feature switch, off by default; Telegram and e-mail carry the orange and red levels |
| commit hook (`ao hooks install`) | installed inside the repository; a shared, external or globally configured hooks directory is refused (#71); its execution proof does not pass yet (below) |
| pre-push hook | works under Git's own shell |

The hosted `tests` workflow runs Windows and macOS every week on Python 3.12, and any
environment on demand (`gh workflow run tests -f os=windows-latest -f python=3.12`);
Ubuntu runs on every push and pull request with the Python 3.9 support floor and 3.12.
Every lane runs the same suite, and each supported
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

## Found before the lane first ran

Read from the code, not seen on a Windows machine; `tests/test_windows_followups.py`
holds each on every platform by doing what Windows does (#9, #71):

- **A CRLF checkout of `.ao-project`.** Git for Windows checks text out with CRLF, so a
  clone of an enrolled repository holds the marker with CRLF and `ao init` refused it.
  Enrollment is measured from the blobs in HEAD and the active index, which that Git
  stores with the exact bytes, and stays exact. Only init's read of the working tree
  accepts the marker with CRLF, whole, and only on Windows; where Git does not convert
  it back, the staged marker is refused as before. ao does not pin `.ao-project -text`
  in `.gitattributes` instead: an attribute changes a checkout only once it is
  committed, so a repository enrolled without it would still be refused on its first
  Windows clone, and `.gitattributes` is the owner's file.
- **Ids minted in one clock tick.** Windows advanced the clock about every 15.6 ms before
  Python 3.13, and notices, submitted reviews, merge checks and commit grants minted
  within one tick shared an id. A process never mints one value twice: a repeat takes
  the next one up, and the id keeps its shape. A submitted review also creates its state
  file exclusively, so a second process in the same tick takes the next millisecond. Two
  processes can still give one notice, merge check or grant id to two ledger rows, which
  stay two rows.
- **A rename over an open file.** Windows does not replace a file another handle holds
  open: reading one ledger made an append to another fail and take its row back. Every
  replace in `storage.py` retries that refusal for about a second, then fails as before.
- **Sibling agent binaries.** `C:\...\<agent>-chat.exe` is an agent process: a path takes
  either separator, and a program is a file ending in `.exe`, `.cmd`, `.bat` or `.com`.
- **The manual MCP snippet** writes the executable and the root escaped for a TOML string.
- **Quoting for a shell.** A scoped review diff runs git, and the credit check reads its
  token store with sqlite3, without a shell: pathspecs, path and query are arguments.
- **The execution probe's cleanup.** A hook that ran past its timeout can leave children
  holding the probe's temporary index; the directory is removed without raising over the
  probe's answer.

## What the Windows lane skips, and why

A test that cannot pass on Windows is skipped there with its reason, never left out (#71):

- the commit hook's execution proof, for the reason above;
- a hook write that needs `--allow-shared-hooks`, which ao refuses on Windows;
- POSIX file modes: an executable hook, an owner-only credentials file;
- fixtures that run through a shebang or are POSIX shell scripts: a reviewer, a
  conformance harness, a filter named `git`, a fake `keyflip`;
- what Windows does not have: process groups and the CPU they spend, zombie
  processes, a directory fsync, launchd, and the `ps` and `lsof` a process table falls
  back to.
