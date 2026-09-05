import os
import subprocess

from ao import lib as A


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True)


def test_review_sees_the_inventory_and_not_the_coordination_noise(project):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "a.py"), "w").write("x = 1\n")
    os.makedirs(os.path.join(root, ".kiro", "steering"))
    open(os.path.join(root, ".kiro", "steering", "s.md"), "w").write("rule\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    open(os.path.join(root, "src", "a.py"), "w").write("x = 2\n")                     # product change
    open(os.path.join(root, ".kiro", "steering", "s.md"), "w").write("rule changed\n")  # coordination noise
    for i in range(30):                                                                # review artefacts
        open(os.path.join(root, "semantic-review", f"r{i:02d}.md"), "w").write("VERDICT: NEEDS_CHANGES\n")
    os.makedirs(os.path.join(root, "evidence"))
    open(os.path.join(root, "evidence", "inventory.md"), "w").write("# inventory\nO1..O15\n")
    diff, included = A.review_diff(root, project)
    assert "x = 2" in diff and "rule changed" not in diff
    assert included == ["evidence/inventory.md"] and "O1..O15" in diff
    diff2, inc2 = A.review_diff(root, project, paths=["evidence"])
    assert "x = 2" not in diff2 and inc2 == ["evidence/inventory.md"]


def test_helpers_are_not_writers(project, monkeypatch):
    root = project["root"]
    A.helper_register(root, 500, "reviewer")
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A, "_proc_table", lambda: {500: (1, 500, "??"), 501: (500, 500, "??"), 600: (1, 600, "??")})
    lines = {500: "/x/bin/claude -p review", 501: "/x/lib/node_modules/@anthropic-ai/claude-code/cli.js",
             600: "/x/bin/kiro-cli chat --no-interactive"}

    def fake_sh(cmd, **kw):
        if cmd.startswith("pgrep"):
            return "500\n501\n600"
        if cmd.startswith("ps -o args= -p"):
            return lines[int(cmd.split()[-1])]
        if cmd.startswith("lsof"):
            return f"p500\nn{root}\np501\nn{root}\np600\nn{root}"
        return ""
    monkeypatch.setattr(A, "sh", fake_sh)
    assert A.agent_pids(root, {"resume": {"argv": ["kiro-cli"]}}) == [600]
