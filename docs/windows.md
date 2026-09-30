# Windows

What works, what does not, and how hosted runners exercise it.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| what ao prints and reads on its standard streams | UTF-8, as Python's UTF-8 mode sets them, whatever the code page (below) |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`); a request is read as UTF-8 and every reply is seven-bit JSON, whatever the code page |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`; the working directory is read from the process environment block (a 64-bit process, by a 64-bit Python), proven on the Windows lane, and where it cannot be read a turn is matched by the repository path on its command line |
| `ao hold` | proven on the Windows lane: a turn placed by its working directory is stopped |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min). Each task names a program that exists — the console script on PATH, a clone's script, or `python -m ao.watchdog` from an interpreter that imports an installed ao — or install refuses; install exits 1 when `schtasks` cannot create a task, and `ao remove --yes` deletes both tasks in its own process and checks they are gone |
| desktop notifications | a toast through PowerShell behind the `toast` feature switch, off by default; Telegram and e-mail carry the orange and red levels |
| `bin/ao.ps1`, the subset for a machine without Python | `status`, `board` and `doctor`; reads files as UTF-8 and takes paths literally; written and reviewed, not yet run on Windows (below) |
| commit hook (`ao hooks install`) | installed inside the repository; a shared, external or globally configured hooks directory is refused (#71); since HOOK-V4 the hook reads a drive-letter index as absolute, and its execution proof has not yet run on the lane (below) |
| pre-push hook | works under Git's own shell |

*In ao since slice ANCESTOR-WINDOWS: the process that started a process is read from Toolhelp, the
process list Windows keeps, in-process, and no longer from the PowerShell snapshot alone. It is how
`ao lock -- ao verify` finds that the lock it meets is its own parent's and runs inside it; when
PowerShell answered nothing, the verify waited out that lock and gave up. Where neither says which
process started one on the way, `ao verify` and `ao merge-check` say they cannot tell whether the
holder started them, then wait for it as for any other. A parent pid on Windows can outlive its
process and be given to another; a walk through one still ends. `tests/test_gate_lock.py` holds the
Toolhelp read on the Windows lane, which has not run it yet.*

*In ao since slice WINDOWS-PID-ALIVE: whether a process runs is asked of the process itself, opened
and waited on for no time, and no longer read from the PowerShell snapshot, which is kept for two
seconds. A process started inside them was missing from it, so a gate lock its holder had just taken
read as a dead run's, was cleared, and `ao verify` and `ao merge-check` ran their gates beside the
holder's; the Windows lane's weekly run found it. Where Windows does not answer, a fresh snapshot
does. `tests/test_windows_pid_alive.py` holds it.*

*In ao since slice WINDOWS-LANE-4: the Windows lane's run dispatched after WINDOWS-PID-ALIVE failed
twelve tests. Nine read processes through `ps` and `lsof`, which Windows does not have: the backend is
chosen once, by a PowerShell reading of this process, and the suite's first reading came in a test
that stood in for subprocess, so it failed and the fallback was kept. Windows now keeps its own
backend whatever that reading gave, and a reading that lists no process is not kept for the next two
seconds. A file in the home is named `~/…` with slashes there too, and a lane another checkout
started is known for one, where its path, compared case-folded, hid the case its branch keeps.
`tests/test_windows_lane_4.py` holds them.*

*In ao since slice WINDOWS-LANE-5: a tree ao stops is gone from the next reading of the process
list. The snapshot taken before the stop listed it for two seconds more, and a stopped process's
directory can no longer be read, so `ao hold` named the turn it had just stopped as one it could
not place and exited 1. Until WINDOWS-PID-ALIVE the hold waited for the stopped turn by that same
snapshot, which was read again by the time the wait ended. `tests/test_windows_lane_5.py` holds it.*

*In ao since slice HOOK-V4: the commit hook reads an index path that starts with a drive letter, a
colon and a slash or a backslash, `C:/…` or `C:\…`, as absolute, as it reads one that starts with a
slash. It told an absolute path by its leading slash alone, so under Git's shell the temporary index
the execution proof hands Git, and a linked worktree's index, were taken for relative ones and put
after the working directory: ao read another index than the one Git commits and refused, and no proof
passed on Windows. Both hooks are version 4 now: `ao doctor` reads a version 3 hook as legacy, and each
enrolled repository reinstalls with `ao hooks install`. The proof's tests are no longer skipped on
Windows, and `tests/test_hook_v4.py` runs the lines of the hook that read the path under `sh` on macOS
and Linux.*

The hosted `tests` workflow runs Windows and macOS every week on Python 3.12, and any
environment on demand (`gh workflow run tests -f os=windows-latest -f python=3.12`);
Ubuntu runs on every push and pull request with the Python 3.9 support floor and 3.12.
Every lane runs the same suite, and each supported
runner must pass the process backend's native self-check rather than silently use
the shell fallback. Each lane's log ends with every test it skipped and the reason
(`pytest -rs`).

The Windows lane has run. Its first run, on demand on 2026-09-17, failed on what *Found by
the lane's first run* lists; the run on demand after those fixes that day, and the weekly
run of 2026-09-21, were green on the hosted runner with Python 3.12: 1232 and then 1280
tests passed, 59 skipped. Both ran `tests/test_windows_processes.py`, which only Windows
runs: a live process's working directory read from its environment block, and `ao hold`
stopping a turn placed by it (#9).

Hosted runners cover platform API behavior and deterministic process crashes: the
durability tests kill real child processes around storage barriers and use temporary
paths. They do not provide physical power-loss, storage-controller or filesystem
qualification, including unsupported and network filesystems. Faults found on a
hosted runner get a scenario in `tests/test_scenarios.py` like any other.

## Text on a default Windows install

Every lane runs Python in UTF-8 mode (`PYTHONUTF8=1`); a Python a person installs on
Windows does not, and there it reads and writes a pipe in the ANSI code page. A character
outside that page - an arrow is outside the Western and the Turkish one alike, and the
Turkish letters are outside the Western one - ended the write, and with it the command
or the MCP server (#71, from the 2026-09-08 audit); a body piped in as UTF-8 was read as
the code page's letters; and a reader expecting UTF-8 got the code page's bytes.

*In ao since slice WINDOWS-CLOSE:* each of ao's entry points - `ao`, the watchdog, both
MCP servers, the A2A server and the Telegram poller - first sets its standard streams to
UTF-8 with the error handlers UTF-8 mode gives them, so a default install reads and
writes what the lanes do, and ao no longer needs `PYTHONUTF8=1`. Every MCP reply is
seven-bit JSON, which no code page between the server and its client can change, and a
reply that echoed a lone surrogate from a request no longer takes the server down. Every
text file ao opens and every program's output it reads as text names its encoding, and
a guard keeps it so. `tests/test_windows_close.py` holds this on every platform by
starting ao with `PYTHONIOENCODING=cp1252`, the streams of a Western Windows install.

PowerShell has code pages of its own. It wrote the process table in the console's, so a
command line holding a letter outside it - a repository under a user directory with a
Turkish name, a prompt in Turkish - came back as other letters, and a turn named by its
repository's path was not placed. The table now leaves PowerShell as UTF-8 bytes on the
standard output handle itself. Windows PowerShell also reads a file without a byte-order
mark, which is how ao writes every file, in the ANSI code page: `bin/ao.ps1` read a Turkish
board title and the notes separator as other letters, and took `[` and `]` in a project's
path for wildcards. It now reads UTF-8, takes every path literally and is itself ASCII.
Only a PowerShell can prove these. On Windows, `tests/test_windows_processes.py` reads a
command line outside the code page back from the table and places its turn, and
`tests/test_windows_powershell.py` reads a board through `bin/ao.ps1` under a path holding
`[` and `]`; wherever a PowerShell is installed, the same file has its parser read every
script ao hands it - the table's query, the toast, `bin/ao.ps1`. They are written and have
not yet run on the lane; until they do, none of this is a Windows result.

## Still open

- **The commit hook's execution proof.** Since HOOK-V4 the hook reads a drive-letter
  index path — the temporary index the proof hands Git, or a linked worktree's index —
  as absolute, where it took one for a relative path and ao refused (above). The tests
  that prove Git runs the hook are no longer skipped on Windows, and the lane has not
  run them yet; until it does, none of this is a Windows result.
- **A prompt handed to a batch file.** Found by reading, not seen on a Windows machine.
  An agent CLI installed with npm on Windows is a `.cmd` file, which runs through
  cmd.exe, and cmd.exe reads its arguments as its own syntax: it ends the command at a
  line break, replaces `%NAME%`, and a double quote in the text changes what `&` and `|`
  mean. ao refuses a `.cmd` or `.bat` reviewer for this (#71), but the watchdog hands its
  prompt to the implementer and the architect as an argument, and the architect's wake
  prompt runs to several paragraphs: through a `.cmd` the architect would read only the
  first. The fix is a channel, not a quoting rule: where the adapter declares standard
  input (Claude Code, Codex and Gemini do, among others) a batch program takes its prompt
  there, and one that declares none is refused, naming Windows and cmd.exe. It changes
  how every turn starts, so it is a slice of its own, proven on the lane.
- **Git's output in `bin/ao.ps1`.** PowerShell reads what a program prints in the
  console's code page, so where a commit subject holds a letter outside that page,
  `status` shows other letters in its place.
- **The toast has never been shown.** A test has PowerShell parse its script, which
  the lane has not yet run, and no test shows a toast on a person's desktop.

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

## Found by the lane's first run

Seen on the hosted runner (Windows Server 2025, Python 3.12); `tests/test_windows_lane.py`
holds each on every platform by doing what Windows does (#71):

- **Two clocks in one stat.** CPython 3.12 and later on Windows put a file's creation time
  in a path's `st_ctime` and its last metadata change in a handle's, so the two agree only
  while nothing changed after the file was created. `ao init` compares the marker it listed
  with the marker it opened, and refused the one it had just written, and the one a clone
  checked out, as changed before it could be read. On Windows that comparison leaves
  `st_ctime` out: the volume, file id, type, size and write time still tie the handle to the
  path, and the handle's own readings, like the fingerprint init compares across the
  reviewer probe, keep every field.
- **A written path with backslashes.** A harness on Windows names the files it writes with
  backslashes, and `ao cost` counted a turn that wrote product or coordination files as
  analysis. A written path is now classified with either separator.

The run's other failures were the tests' own assumptions, and those tests no longer make
them: Git for Windows runs a hook whatever its mode, `os.readlink` returns an absolute
target with its `\\?\` prefix, Windows file names are Unicode, the home is `USERPROFILE`
and never `HOME`, an isolated interpreter (`-I`) sets UTF-8 mode aside, and a reviewer's
tree kill starts `taskkill`, which a test's stand-in for the reviewer process answered.

## What the Windows lane skips, and why

A test that cannot pass on Windows is skipped there with its reason, never left out (#71),
and the lane's log lists each one with it:

- a hook write that needs `--allow-shared-hooks`, which ao refuses on Windows;
- POSIX file modes: an executable hook, an owner-only credentials file;
- fixtures that run through a shebang or are POSIX shell scripts: a reviewer, a
  conformance harness, a filter named `git`, a fake `keyflip` or quota command. These
  could run on Windows once each fixture is a program Windows starts - and a reviewer
  cannot be a `.cmd`, which ao refuses - but each rewrite is a Windows result only when
  the lane has run it, so they stay skipped, with their reasons, until one does;
- what Windows does not have: process groups and the CPU they spend, zombie
  processes, a directory fsync, launchd, and the `ps` and `lsof` a process table falls
  back to.

The other way round, `tests/test_windows_processes.py` and the board read through
`bin/ao.ps1` need Windows itself and run only there. PowerShell's parser reads ao's scripts
wherever a PowerShell is installed, and `pwsh` runs on Linux and macOS too. Where none is,
each test says so as it is skipped.
