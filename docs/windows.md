# Windows

What works, what does not, and how it is tested without a Windows machine.

| layer | status |
|---|---|
| files: `.ao/`, mailbox, board, backlog, ledgers | works — plain files and Python |
| gates, `ao lock`, `ao verify`, `ao commit-ok`, reviews | work — subprocesses of the project's own tools |
| MCP server, playbook, `ao init` registration | work (`.mcp.json`, `.kiro/settings/mcp.json`) |
| process introspection (`ao writers`, orphans, hung turns) | first cut: `Win32_Process` through PowerShell as JSON, tree kill via `taskkill /T`; **no cwd** from CIM, so a turn is matched by the repository path on its command line |
| scheduler (`ao watchdog install`) | first cut: Task Scheduler (`schtasks`, every 2 min; doctor every 15 min) |
| desktop notifications | not on Windows yet — Telegram and e-mail carry the orange and red levels |
| pre-push hook | works under Git's own shell |

The suite runs on every push locally (the repository's pre-push hook, on a Mac);
the hosted `tests` workflow is manual — `gh workflow run tests -f os=windows-latest`
— because macOS minutes bill at 10x and Windows at 2x. It runs the same suite,
including the process backend's self-check on its own pid. That is how Windows is
developed here: the runner is the machine, on demand.
Faults found there get a scenario in `tests/test_scenarios.py` like any other.

Not yet done, in order of value: cwd via the process environment block (psutil's
method, `NtQueryInformationProcess` + `ReadProcessMemory`), a toast notification,
`ao hold` semantics for processes without groups.
