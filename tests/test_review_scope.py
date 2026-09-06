import os
import subprocess

from ao import lib as A


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True)


def test_review_sees_the_inventory_and_not_the_coordination_noise(project):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    os.makedirs(os.path.join(root, ".kiro", "steering"))
    open(os.path.join(root, ".kiro", "steering", "s.md"), "w", encoding="utf-8").write("rule\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 2\n")                     # product change
    open(os.path.join(root, ".kiro", "steering", "s.md"), "w", encoding="utf-8").write("rule changed\n")  # coordination noise
    for i in range(30):                                                                # review artefacts
        open(os.path.join(root, "semantic-review", f"r{i:02d}.md"), "w", encoding="utf-8").write("VERDICT: NEEDS_CHANGES\n")
    os.makedirs(os.path.join(root, "evidence"))
    open(os.path.join(root, "evidence", "inventory.md"), "w", encoding="utf-8").write("# inventory\nO1..O15\n")
    diff, included = A.review_diff(root, project)
    assert "x = 2" in diff and "rule changed" not in diff
    assert included == ["evidence/inventory.md"] and "O1..O15" in diff
    diff2, inc2 = A.review_diff(root, project, paths=["evidence"])
    assert "x = 2" not in diff2 and inc2 == ["evidence/inventory.md"]


def test_helpers_are_not_writers(project, monkeypatch):
    from ao import procs
    root = project["root"]
    A.helper_register(root, 500, "reviewer")
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A, "_proc_table", lambda: {500: (1, 500, "??"), 501: (500, 500, "??"), 600: (1, 600, "??")})
    vectors = {500: ["/x/bin/claude", "-p", "review"], 501: ["node", "/x/lib/node_modules/@anthropic-ai/claude-code/cli.js"],
               600: ["/x/bin/kiro-cli", "chat", "--no-interactive"]}
    monkeypatch.setattr(procs, "all_pids", lambda: list(vectors))
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))
    monkeypatch.setattr(procs, "cwd", lambda pid: root)
    assert A.agent_pids(root, {"resume": {"argv": ["kiro-cli"]}}) == [600]


def test_scoped_prospective_review_refuses_staged_paths_outside_scope_before_reviewer(
    project, monkeypatch, capsys
):
    import sys
    from types import SimpleNamespace

    from ao import cli

    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    src = os.path.join(root, "src", "a.py")
    other = os.path.join(root, "other.txt")
    open(src, "w", encoding="utf-8").write("x = 1\n")
    open(other, "w", encoding="utf-8").write("base\n")
    _git(root, "add", "src/a.py", "other.txt")
    _git(root, "commit", "-q", "-m", "base")
    open(src, "w", encoding="utf-8").write("x = 2\n")
    open(other, "w", encoding="utf-8").write("outside\n")
    _git(root, "add", "src/a.py", "other.txt")

    reviewer_calls = []

    def unexpected_reviewer(*args, **kwargs):
        reviewer_calls.append((args, kwargs))
        raise AssertionError("reviewer invoked for refused candidate")

    monkeypatch.setattr(cli, "_run_reviewer", unexpected_reviewer)
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "test",
            "argv": [sys.executable, "-c", "print('VERDICT: APPROVED')", "{prompt}"],
        },
    )
    args = SimpleNamespace(boundary="src only", timeout=30, paths=["src"], commits=None)

    assert cli.cmd_review(cfg, args) == 2
    output = capsys.readouterr().out

    assert "CANDIDATE REFUSED" in output
    assert "review scope excludes staged paths: other.txt" in output
    assert reviewer_calls == []
    assert os.listdir(os.path.join(root, cfg["reviews"])) == []
