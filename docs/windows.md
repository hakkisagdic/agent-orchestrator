# Windows

What works, what does not, and how hosted runners exercise it.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`) |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`; **no cwd** from CIM, so a turn is matched by the repository path on its command line |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min) |
| desktop notifications | not on Windows yet — Telegram and e-mail carry the orange and red levels |
| pre-push hook | works under Git's own shell |

The hosted `tests` workflow runs automatically for pull requests and pushes to
`main`, and remains available through manual `workflow_dispatch`. Python 3.11 runs
on Ubuntu, Windows and macOS; Ubuntu also carries the Python 3.9 support-floor and
Python 3.12 release/newer lanes. Every lane runs the same suite, and each supported
runner must pass the process backend's native self-check rather than silently use
the shell fallback.

Hosted runners cover platform API behavior and deterministic process crashes: the
durability tests kill real child processes around storage barriers and use temporary
paths. They do not provide physical power-loss, storage-controller or filesystem
qualification, including unsupported and network filesystems. Faults found on a
hosted runner get a scenario in `tests/test_scenarios.py` like any other.

Not yet done, in order of value: cwd via the process environment block (psutil's
method, `NtQueryInformationProcess` + `ReadProcessMemory`), a toast notification,
`ao hold` semantics for processes without groups.
