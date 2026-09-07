import pytest

from ao import lib as A


def test_orphans_are_leaderless_tty_less(monkeypatch):
    table = {10: (1, 9, "??"),      # parent init, leader 9 dead -> orphan
             11: (10, 9, "??"),     # child of the orphan, same dead group -> orphan
             20: (1, 20, "??"),     # its own leader (a live headless turn) -> not orphan
             30: (5, 30, "ttys001"),  # a person's terminal session -> not orphan
             40: (1, 41, "??"),     # leader alive
             41: (1, 41, "??")}
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [10, 11, 20, 30, 40])
    assert A.orphans("/r", {}, table) == [10, 11]


def test_writers_counts_turns_not_processes(monkeypatch):
    table = {20: (1, 20, "??"), 21: (20, 20, "??"), 22: (21, 20, "??"), 10: (1, 9, "??")}
    monkeypatch.setattr(A, "_proc_table", lambda: table)
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [10, 20, 21, 22])
    roots, dead = A.writers("/r", {})
    assert roots == [20] and dead == [10]


def test_a_process_that_mentions_the_agent_is_not_the_agent(monkeypatch):
    vectors = {9: ["/bin/zsh", "-c", "cd /repo && ao mail ack x && pwd -P >| /tmp/claude-add3-cwd"],
               1: ["zsh (kiro-cli-term)"],
               2: ["/Users/x/.local/bin/kiro-cli", "chat", "--resume-id", "s"],
               3: ["/Users/x/.local/bin/kiro-cli-chat", "acp", "--agent-engine=kas"],
               4: ["/Users/x/Library/Application Support/kiro-cli/node", "--experimental", "x"],
               5: ["/bin/zsh", "-c", "cd /repo && ls agent-mail/kiro-to-fable.md"],
               6: ["/Applications/Claude.app/Contents/MacOS/Claude Helper"],
               7: ["node", "/Users/x/lib/node_modules/@anthropic-ai/claude-code/cli.js", "-p", "hi"],
               8: ["claude", "-p", "hello"]}
    monkeypatch.setattr(A, "_executable", lambda p: p.startswith("/Users/x/.local/bin/"))
    names = {"kiro-cli", "claude", "claude-code"}
    assert sorted(p for p, av in vectors.items() if A._is_agent_process(p, names, av)) == [2, 3, 4, 7, 8]


def test_agent_pids_matches_on_cwd_with_exact_argv(monkeypatch):
    from ao import procs
    vectors = {101: ["/usr/local/bin/claude", "-p", "x"], 102: ["/usr/local/bin/claude", "-p", "y"],
               103: ["/Users/x/Library/Application Support/kiro-cli/node", "--flag"], 104: ["/bin/zsh", "-c", "claude things"]}
    cwds = {101: "/repo", 102: "/elsewhere", 103: "/repo", 104: "/repo"}
    monkeypatch.setattr(procs, "all_pids", lambda: list(vectors))
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))
    monkeypatch.setattr(procs, "cwd", lambda pid: cwds.get(pid))
    monkeypatch.setattr(A, "helper_pids", lambda root: set())
    monkeypatch.setattr(A.os.path, "realpath", lambda p: p)
    assert A.agent_pids("/repo", {"resume": {"argv": ["claude"]}}) == [101, 103]



def _architect_process_table(monkeypatch, vectors, cwds, parents=None, helpers=()):
    from ao import procs

    parents = parents or {}
    monkeypatch.setattr(procs, "all_pids", lambda: list(vectors))
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))
    monkeypatch.setattr(procs, "cwd", lambda pid: cwds.get(pid))
    monkeypatch.setattr(
        A,
        "_proc_table",
        lambda: {pid: (parents.get(pid, 1), pid, "??") for pid in vectors},
    )
    monkeypatch.setattr(A, "helper_pids", lambda root, what=None: set(helpers))


def test_architect_presence_measures_a_live_interactive_configured_process(monkeypatch):
    vectors = {201: ["/agents/claude"]}
    _architect_process_table(monkeypatch, vectors, {201: "/repo"})
    monkeypatch.setattr(
        A,
        "discover_architect",
        lambda cwd: (_ for _ in ()).throw(AssertionError("transcript discovery is not liveness")),
    )

    assert A.architect_present("/repo", {"argv": ["claude", "-p", "{prompt}"]})


@pytest.mark.parametrize("flag", ["-p", "--print", "--no-interactive"])
def test_architect_presence_classifies_headless_at_the_full_process_tree_root(monkeypatch, flag):
    vectors = {
        210: ["/agents/claude", flag, "triage"],
        211: ["/bin/sh", "-c", "runtime"],
        212: ["/agents/node", "/opt/claude-code/engine.js"],
    }
    _architect_process_table(
        monkeypatch,
        vectors,
        {210: "/repo", 211: "/repo", 212: "/repo"},
        parents={211: 210, 212: 211},
    )

    assert not A.architect_present("/repo", {"argv": ["claude", "-p", "{prompt}"]})


def test_architect_presence_excludes_deep_ao_helper_descendants(monkeypatch):
    vectors = {220: ["/agents/claude"]}
    cwds = {220: "/repo"}
    parents = {}
    previous = 220
    for pid in range(221, 235):
        vectors[pid] = ["/bin/sh", "-c", "child"]
        cwds[pid] = "/repo"
        parents[pid] = previous
        previous = pid
    vectors[235] = ["/agents/node", "/opt/claude-code/engine.js"]
    cwds[235] = "/repo"
    parents[235] = previous
    _architect_process_table(
        monkeypatch,
        vectors,
        cwds,
        parents=parents,
        helpers={220},
    )

    assert not A.architect_present("/repo", {"argv": ["claude"]})


def test_architect_presence_rejects_other_agents_and_other_projects(monkeypatch):
    vectors = {
        230: ["/agents/kiro-cli", "chat"],
        231: ["/agents/claude"],
    }
    _architect_process_table(
        monkeypatch,
        vectors,
        {230: "/repo", 231: "/another-project"},
    )

    assert not A.architect_present("/repo", {"argv": ["claude"]})


def test_architect_presence_releases_on_the_next_scan_after_process_exit(monkeypatch):
    vectors = {240: ["/agents/claude"]}
    cwds = {240: "/repo"}
    _architect_process_table(monkeypatch, vectors, cwds)
    architect = {"argv": ["claude"]}

    assert A.architect_present("/repo", architect)
    vectors.clear()
    cwds.clear()
    assert not A.architect_present("/repo", architect)


def test_architect_presence_uses_exact_absolute_argv_fallback_without_cwd(monkeypatch):
    vectors = {250: ["/agents/claude", "/repo"]}
    _architect_process_table(monkeypatch, vectors, {250: None})

    assert A.architect_present("/repo", {"argv": ["claude"]})



def test_duplicate_wake_guard_requires_an_architect_helper_tree(monkeypatch):
    vectors = {270: ["/agents/claude", "-p", "triage"]}
    _architect_process_table(monkeypatch, vectors, {270: "/repo"})
    architect = {"argv": ["claude"], "cwd": "/architect-worktree"}
    role = {"value": "reviewer"}
    monkeypatch.setattr(
        A,
        "helper_pids",
        lambda root, what=None: {270} if what is None or what == role["value"] else set(),
    )

    assert not A.architect_turn_present("/repo", architect)
    role["value"] = "architect"
    assert A.architect_turn_present("/repo", architect)


@pytest.mark.parametrize(
    "argv",
    [
        [r"C:\Tools\claude.exe", r"C:\repo"],
        [
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Users\dev\node_modules\claude-code\cli.js",
            r"C:\repo",
        ],
    ],
)
def test_architect_presence_accepts_windows_executables_and_runtime_paths(monkeypatch, argv):
    vectors = {280: argv}
    _architect_process_table(monkeypatch, vectors, {280: None})

    assert A.architect_present(
        r"C:\repo",
        {"argv": ["claude"], "cwd": r"C:\repo"},
    )


def test_architect_presence_rejects_windows_absolute_argv_near_match(monkeypatch):
    vectors = {281: [r"C:\Tools\claude.exe", r"C:\repo-other"]}
    _architect_process_table(monkeypatch, vectors, {281: None})

    assert not A.architect_present(
        r"C:\repo",
        {"argv": ["claude"], "cwd": r"C:\repo"},
    )


def test_helper_registry_rejects_a_reused_pid(monkeypatch, tmp_path):
    starts = {290: "process-a"}
    monkeypatch.setattr(A, "HOME", str(tmp_path))
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: starts.get(pid))

    A.helper_register("/repo", 290, "architect")
    assert A.helper_pids("/repo") == {290}

    starts[290] = "process-b"
    assert A.helper_pids("/repo") == set()



@pytest.mark.parametrize(
    "argv",
    [
        ["/agents/claude-reviewer"],
        ["/agents/node", "/opt/claude-code-reviewer/engine.js"],
    ],
)
def test_architect_presence_rejects_sibling_and_runtime_near_match(monkeypatch, argv):
    vectors = {300: argv}
    _architect_process_table(monkeypatch, vectors, {300: "/repo"})

    assert not A.architect_present("/repo", {"argv": ["claude"]})



def test_helper_registration_refreshes_a_warm_process_snapshot(monkeypatch, tmp_path):
    from ao import procs

    fresh = {"value": False}
    monkeypatch.setattr(A, "HOME", str(tmp_path))
    monkeypatch.setattr(procs, "refresh", lambda: fresh.update(value=True))
    monkeypatch.setattr(
        procs,
        "info",
        lambda pid: {"start": "fresh-start"} if fresh["value"] and pid == 310 else None,
    )

    A.helper_register("/repo", 310, "architect")

    assert fresh["value"]
    assert A.helper_pids("/repo", "architect") == {310}
