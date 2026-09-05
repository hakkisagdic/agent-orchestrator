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
