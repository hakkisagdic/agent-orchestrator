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
    lines = {9: "/bin/zsh -c cd /repo && ao mail ack x && pwd -P >| /tmp/claude-add3-cwd",
             1: "zsh (kiro-cli-term)",
             2: "/Users/x/.local/bin/kiro-cli chat --resume-id s",
             3: "/Users/x/.local/bin/kiro-cli-chat acp --agent-engine=kas",
             4: "/Users/x/Library/Application Support/kiro-cli/node --experimental x",
             5: "/bin/zsh -c cd /repo && ls agent-mail/kiro-to-fable.md",
             6: "/Applications/Claude.app/Contents/MacOS/Claude Helper",
             7: "node /Users/x/lib/node_modules/@anthropic-ai/claude-code/cli.js -p hi",
             8: "claude -p hello"}
    monkeypatch.setattr(A, "sh", lambda cmd, **kw: lines[int(cmd.split()[-1])])
    monkeypatch.setattr(A.os.path, "isfile", lambda p: p.startswith("/Users/x/.local/bin/"))
    monkeypatch.setattr(A.os, "access", lambda p, m: p.startswith("/Users/x/.local/bin/"))
    names = {"kiro-cli", "claude", "claude-code"}
    assert sorted(p for p in lines if A._is_agent_process(p, names)) == [2, 3, 4, 7, 8]


def test_agent_pids_uses_one_lsof_call(monkeypatch):
    calls = []

    def fake_sh(cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("pgrep"):
            return "101\n102\n103"
        if cmd.startswith("ps -o args= -p"):
            return "/usr/local/bin/claude -p x"
        if cmd.startswith("lsof"):
            return "p101\nn/repo\np102\nn/elsewhere\np103\nn/repo"
        return ""
    monkeypatch.setattr(A, "sh", fake_sh)
    monkeypatch.setattr(A.os.path, "realpath", lambda p: p)
    out = A.agent_pids("/repo", {"resume": {"argv": ["claude"]}})
    assert out == [101, 103]
    assert len([c for c in calls if c.startswith("lsof")]) == 1
