# Windows

What works, what does not, and how hosted runners exercise it.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| what ao prints and reads on its standard streams | UTF-8, as Python's UTF-8 mode sets them, whatever the code page (below) |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`); a request is read as UTF-8 and every reply is seven-bit JSON, whatever the code page |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`, every process of the tree named to it and asked afterwards; the working directory is read from the process environment block (a 64-bit process, by a 64-bit Python), proven on the Windows lane, and where it cannot be read a turn is matched by the repository path on its command line; a table PowerShell cannot read never reads as no process: what decides from it refuses or holds, and says why; what an ended turn left is not told apart from a turn, as Windows has no process groups, and `ao writers` and `ao doctor` say so (below) |
| `ao hold` | proven on the Windows lane: a turn placed by its working directory is stopped; what a stop leaves running is named, and the hold exits 1 (below) |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min). Each task names a program that exists — the console script on PATH, a clone's script, or `python -m ao.watchdog` from an interpreter that imports an installed ao — or install refuses; install exits 1 when `schtasks` cannot create a task, saying why in the OEM code page schtasks writes in, and `ao remove --yes` deletes both tasks in its own process and checks they are gone; a silent watchdog is to be looked into with Task Scheduler (below) |
| desktop notifications | a toast through PowerShell behind the `toast` feature switch, off by default; Telegram and e-mail carry the orange and red levels |
| `bin/ao.ps1`, the subset for a machine without Python | `status`, `board` and `doctor`; reads files as UTF-8 and takes paths literally; written and reviewed, not yet run on Windows (below) |
| commit hook (`ao hooks install`) | installed inside the repository; a shared, external or globally configured hooks directory is refused (#71); since HOOK-V4 the hook reads a drive-letter index as absolute, and its execution proof passes on the lane (below) |
| pre-push hook | works under Git's own shell |
| the programs ao starts | Windows' own tools from its system directory, every other program from PATH's absolute entries, never the current directory (below) |
| files ao keeps private (`~/.ao/email.json`, `telegram.json`, `pings.json`) | as private as `~/.ao`, which takes its permissions from the user profile: Windows keeps no file mode, and ao says so where it saves one and in `ao doctor` (below) |
| the reviewer stall check (`review.stall_minutes`) | does not apply: Windows has no process group to read a reviewer's CPU from, so the review's timeout ends a reviewer that hangs, and the review says so (below) |
| a transcript read without following a link | holds: where there is no O_NOFOLLOW the path is looked at before it is opened, and the file opened must be the one looked at (below) |
| the grant audit (`ao doctor`, an actor's grant) | asks `py -c`, `powershell -Command` and `cmd /c`, with their kin, as it asks `python -c` (below) |

*In ao since slice WINDOWS-LANE-PARITY: the Windows lane runs the reviewer and authority tests the
other lanes run. Each of them started a stand-in harness - a reviewer, a kiro-cli, an agy, an opencode,
a conformance harness - as a Python script whose first line named the interpreter; Windows runs no
such line, and ao knows a harness by the name of the program it starts, so `[sys.executable, script]`
would be no harness of any adapter's, and every one of them was skipped on Windows. A stand-in is now
written by `tests/standin.py`: an executable script on POSIX, and on Windows `<name>.exe`, as pip makes
a console script there - distlib's console launcher, a line naming the interpreter, and the script as
`__main__.py` in a zip after them. Eighteen tests that were skipped on Windows run there now; the one
reviewer test still skipped waits for a sign-in line while the reviewer runs, which Windows does not
hand over until the reviewer's streams close. The weekly run also has a Windows leg without Python's
UTF-8 mode, as a default install runs it, and a dispatch can drop UTF-8 mode on any runner
(`-f utf8=off`). `tests/test_windows_lane_parity.py` runs a stand-in as a program on every platform,
and fails when a test is skipped on Windows for its shebang again, or the workflow loses that leg or
a lane's list of what it skipped.*

*In ao since slice WINDOWS-SAY-WHY: what ao cannot hold on Windows it holds another way, or says
so, where it bears and in `ao doctor` (`on Windows`). Several guarantees were made with what Windows
does not have, and there they quietly did not hold. Opening a subagent's transcript without following a
link asked for O_NOFOLLOW, which Windows lacks, and a link was followed; where a platform has none the
path is now looked at first - a link is refused - and the file opened must be the one looked at, by its
file id, or it is refused too. A file ao keeps private was given mode 0600, which Windows ignores, and
ao said "(0600)": `~/.ao/email.json`, `telegram.json` and `pings.json` are as private as `~/.ao`, which
takes its permissions from the user profile unless someone changed them, and `ao email setup`, `ao
pings setup` and `ao telegram setup` say that on Windows; a prompt handed over in a file is kept by the
permissions of its temporary directory the same way. What an ended turn left behind is found by its
dead process group, which Windows has none of: none was ever found, and none was said to be. `ao
writers` says on Windows that such leftovers are not told apart - each counts as a writer, and the
watchdog starts no turn beside it until it is stopped - and its `--json` gives `"orphans": null` with
`orphans_unknown`. The reviewer stall check (`review.stall_minutes`) reads a process group's CPU, and on
Windows it never fired: a review there says, as its wait begins, that only its timeout ends a reviewer
that hangs. schtasks writes in the OEM code page, and what it said when it refused a task was read as
UTF-8, every letter outside ASCII a replacement character; it is read as UTF-8 where it is that, and in
the OEM code page otherwise. The grant audit asks `py -c`, `py -3 -c`, `python.exe -c`, `powershell
-Command`, `-c` and `-EncodedCommand`, `pwsh -Command` and `-c`, `cmd /c`, `cmd //c` (Git Bash's form)
and `cmd.exe /c` as it asks `python -c`: each runs a line of code. A silent watchdog was to be looked
into with launchctl on every platform; the hint names Task Scheduler on Windows and `systemctl --user`
on Linux, and `ao telegram setup` gives PowerShell's way to read a chat id and says how to write the
file, where it gave curl, python3, mkdir, echo and chmod. `tests/test_windows_say_why.py` makes Windows
on every platform.*

*In ao since slice WINDOWS-STOP-PROVEN: a stop says what it left running. `ao hold` stopped each
turn with `taskkill /T /F` and said every tree was stopped, whatever taskkill did: taskkill is refused a
process another user or an elevated prompt started, and finds a process's children by the parent each
names, so a child whose parent had already exited - a turn's wrapper ending as it was stopped - was
left running. Every process of the tree, as the process table read it just before, is now named to
taskkill, not the first alone, and each is asked afterwards, for up to five seconds, whether it runs; a
process neither Windows nor the table answers for is taken as running. A process that started more
than a second before the parent it names is no part of the tree: Windows keeps a parent's pid after
it exits and hands it to a process started later. `ao hold` then reads the table again for unattended
turns and what ended turns left, names what still runs and how to stop it by hand, and exits 1; the
hold stands. The watchdog's reaper starts no turn beside an unattended turn it could not stop and tells
a person (`reap-survived`, [alarms.md](alarms.md)). `ao review cancel` records nothing while the
review's runner still runs, records a reviewer it could not stop as still running, keeping its place
among the helpers, and exits 1. A reviewer stopped on its timeout, an ACP reviewer's session, `ao
harness probe`, `ao writers --clean`, a codebase provider, a lane's post-create command and a filter
hook ao probes each say what of them still runs. What taskkill and the table both miss - a process
whose parent had exited under a parent that had exited too - the hold's second reading still names
when it is an agent's. On macOS and Linux a turn is stopped by its process group as before, and a
forced stop is asked about the same way. `tests/test_windows_stop_proven.py` makes taskkill and
Windows' answers its own, on every platform.*

*In ao since slice WINDOWS-NO-CWD-EXEC: ao starts no program the current directory holds. Windows
looks for a program named without a directory in the current directory before PATH - `subprocess`
given a bare `taskkill`, `schtasks`, `git` or `cmd.exe`, and `shutil.which`, which before Python 3.12
looked there first whatever path it was given - and ao runs inside a tree an agent writes: a
`taskkill.exe`, `schtasks.exe`, `git.exe` or `powershell.exe`, or a `.bat`, `.cmd` or `.com` that
PATHEXT admits, left in the repository root would have run as the person when ao stopped a turn,
scheduled its jobs, read the process table or committed. One resolver now finds every program ao
starts: Windows' own `taskkill`, `schtasks`, `tasklist`, `cmd`, `where` and `powershell` from its
system directory - `%SystemRoot%\System32` as the environment names it, else as Windows answers,
else `C:\Windows\System32` - and from nowhere else; every other program from PATH's absolute
entries in order, honouring PATHEXT, never the current directory, an empty entry or a relative one.
A command line ao runs through a shell starts in the system's `cmd.exe`, and ao's own commands there
are looked for in PATH alone (`NoDefaultCurrentDirectoryInExePath`); a gate's command is the
project's, and cmd.exe looks for it as it always has, in the project's directory first. Where no git
is found ao no longer falls back to the bare name: it names the git Git for Windows installs, which
fails to start rather than start one the tree holds. `ao doctor` names an empty or relative PATH
entry (`path-relative`): ao looks for no program there, so one PATH reaches only through it is not
started. POSIX keeps its own lookup, except that an empty or relative entry is not searched there
either: to POSIX an empty entry is the current directory too, and a console script a relative entry
led to was made absolute and scheduled (SAFE-REMOVE-2). `tests/test_windows_no_cwd_exec.py` gives ao
a system directory of its own and plants a program under each name in a repository, on every
platform.*

*In ao since slice WINDOWS-TABLE-CLOSED: a process table that cannot be read is an error everything
that reads it sees, never an empty table. On Windows the table is one PowerShell query, and a
PowerShell that was missing, failed, did not answer within thirty seconds or printed something that
is not the table gave an empty one, which every caller took for fact: `ao hold` said no agent turn
was running and stopped nothing, `ao writers` counted none, `ao review cancel` recorded a review
failed while its reviewer worked on, and a watchdog cycle could start a turn or wake an architect
beside one it never saw. The reader now says the table could not be read, on which platform and
why: PowerShell not found, not answering in time, its exit status with the first line it wrote to
standard error, its silence, or the length of an answer that is not the table - never its text,
since a command line can hold a secret. The macOS and Linux readers and the `ps` they fall back to
say the same of their own failures. What decides from the table refuses or holds: the watchdog
starts no turn, wakes nobody and reaps nothing, and tells a person under `process-table-unreadable`
([alarms.md](alarms.md)), and a notice that would wait for the architect goes to a person instead
(the hold code `process-table`, [watchdog.md](watchdog.md)); `ao hold` stands, stops nothing it
cannot see, says so and exits 1; `ao writers` says the writers are unknown and exits 1, its
`--json` with `"writers": null`; `ao review cancel` stops and records nothing; any other command
stops with the reason and exits 1. What only shows it says it could not look: `ao hold status`;
the implementer's state in `ao status`, `ao digest`, `ao fleet` and `ao handoff`, `unknown` with
the reason; `ao reviews`; and the MCP tool `ao_status`, whose `agent_processes` is null and
`agent_processes_unread` says why. Where neither the process itself nor the table answers whether
a process runs, it is taken as running: a gate lock or the architect's lock it holds is not
cleared. `tests/test_windows_table_closed.py` makes each failure as its platform makes it, on
every platform, and holds each caller to it.*

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
and Linux. The lane ran the proof's tests green on 2026-10-01, on Python 3.9 and 3.12, once HOOK-V4-2
wrote the adoption tests' old hook with its root quoted, as an early ao wrote it.*

*In ao since slice WINDOWS-CMD-CHANNEL: a batch program the watchdog starts, for the implementer's
nudge and the architect's wake alike, is handed its prompt on standard input whatever its size,
where its adapter declares standard input, and is not started where it declares none; the reason
names Windows and cmd.exe. cmd.exe reads each argument as its own syntax and ends the command at a
line break, so an architect started through a `.cmd` would have read the first line of its wake.
The program is judged once it is resolved, just before it starts: a `.cmd` or `.bat`, in any
case. A prompt file is no channel for it, since its path is an argument too.
`tests/test_windows_cmd_channel.py` holds it on every platform by deciding as Windows does; the
lane has not yet started an agent CLI through a `.cmd`.*

The hosted `tests` workflow runs Windows and macOS every week on Python 3.12, Windows twice -
once without Python's UTF-8 mode - and any environment on demand (`gh workflow run tests -f
os=windows-latest -f python=3.12`, with `-f utf8=off` for a run without UTF-8 mode); Ubuntu runs
on every push and pull request with the Python 3.9 support floor and 3.12.
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

Every lane but one runs Python in UTF-8 mode (`PYTHONUTF8=1`); a Python a person installs on
Windows does not, and there it reads and writes a pipe in the ANSI code page. The weekly run's
second Windows leg runs without it, as such an install does (WINDOWS-LANE-PARITY). A character
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
script ao hands it - the table's query, the toast, `bin/ao.ps1`. The lane ran them green on
2026-10-01, on Python 3.9 and 3.12.

## Still open

- **A prompt handed to a batch file.** Found by reading, not seen on a Windows machine.
  An agent CLI installed with npm on Windows is a `.cmd` file, which runs through
  cmd.exe, and cmd.exe reads its arguments as its own syntax: it ends the command at a
  line break, replaces `%NAME%`, and a double quote in the text changes what `&` and `|`
  mean. ao refuses a `.cmd` or `.bat` reviewer for this (#71). The watchdog no longer hands
  such a program its prompt as an argument: where the adapter declares standard input
  (Claude Code, Codex and Gemini do, among others) the implementer's nudge and the
  architect's wake reach a batch program there, whatever the prompt's size; a batch
  program whose adapter declares none is not started, and the watchdog says why, naming
  Windows and cmd.exe (WINDOWS-CMD-CHANNEL, above). A channel, not a quoting rule: no
  quoting carries a line break through cmd.exe. What stays open is the proof: it changes
  how every turn starts, and no agent CLI has yet been started through a `.cmd` on the lane.
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
- fixtures that are POSIX shell scripts: a filter named `git`, a fake `keyflip`, `xcrun`
  or quota command, the stub programs an update or uninstall runs, a failing lane hook.
  A Python stand-in runs on Windows as a program since WINDOWS-LANE-PARITY
  (`tests/standin.py`), and each of these could be rewritten as one; the fake `gh` of
  `ao pr watch` is a Python script still started through its shebang;
- a reviewer that prints a sign-in prompt and waits: Windows hands over what a child
  printed only once its streams close, so no heartbeat sees the prompt while it waits;
- what Windows does not have: process groups and the CPU they spend, zombie
  processes, a directory fsync, launchd, and the `ps` and `lsof` a process table falls
  back to.

The other way round, `tests/test_windows_processes.py` and the board read through
`bin/ao.ps1` need Windows itself and run only there. PowerShell's parser reads ao's scripts
wherever a PowerShell is installed, and `pwsh` runs on Linux and macOS too. Where none is,
each test says so as it is skipped. No test runs `bin/ao.ps1 status` or `doctor`: a `.ps1` runs
only in a PowerShell, and both read what only Windows has - the user profile's Kiro sessions,
the process table through `Get-CimInstance` - so each would be a test of the Windows lane's alone.
