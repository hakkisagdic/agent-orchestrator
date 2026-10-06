"""A kiro reviewer runs as an agent of ao's whose tools only read (KIRO-READONLY).

Measured on kiro-cli 2.27.1 on 2026-10-05: with --no-interactive, a tool `--trust-tools=` leaves untrusted runs all
the same - asked to, it ran `touch` and made the file - and reviewers ran tests, built virtual environments under
/tmp and installed packages. Run as an agent whose tools are read, grep and glob alone, it read the file it was asked
to and made nothing. ao writes that agent into the reviewer's own directory for each run.
"""
import json
import os
import sys

import pytest

from ao import cli, lib as A

KIRO = ["kiro-cli", "chat", "--no-interactive", "{prompt}", "--model", "m", "--trust-tools="]


def test_a_kiro_reviewer_runs_as_an_agent_that_only_reads(tmp_path):
    argv, why = cli._reviewer_agent(str(tmp_path), KIRO)

    assert why is None and argv[:-2] == KIRO and argv[-2] == "--agent"
    name = argv[-1]
    with open(tmp_path / ".kiro" / "agents" / f"{name}.json", encoding="utf-8") as fh:
        config = json.load(fh)
    assert config["name"] == name and config["tools"] == ["read", "grep", "glob"]
    assert config["includeMcpJson"] is False and config["mcpServers"] == {} and config["allowedTools"] == []
    # a name the tree cannot know: every run has its own
    assert cli._reviewer_agent(str(tmp_path), KIRO)[0][-1] != name


def test_a_reviewer_whose_adapter_declares_no_agent_runs_as_it_is(tmp_path):
    claude = ["claude", "-p", "{prompt}", "--tools", "Read,Grep,Glob"]

    assert cli._reviewer_agent(str(tmp_path), claude) == (claude, None)
    assert not (tmp_path / ".kiro").exists()


@pytest.mark.skipif(os.name == "nt", reason="a link needs a privilege on Windows")
def test_a_tree_that_holds_the_agents_folder_as_a_link_is_refused(tmp_path):
    fresh, elsewhere = tmp_path / "fresh", tmp_path / "elsewhere"
    fresh.mkdir()
    elsewhere.mkdir()
    os.symlink(elsewhere, fresh / ".kiro")

    why = cli._reviewer_agent(str(fresh), KIRO)[1]

    assert why and ".kiro" in why and "as a link or a file" in why
    assert list(elsewhere.iterdir()) == []


def test_a_tree_that_holds_the_agents_folder_as_a_file_is_refused(tmp_path):
    (tmp_path / ".kiro").write_text("", encoding="utf-8")

    assert "as a link or a file" in cli._reviewer_agent(str(tmp_path), KIRO)[1]


def test_a_kiro_command_that_names_an_agent_of_its_own_is_refused():
    composed = A.compose_reviewer("kiro", model="m")["argv"]

    assert A.reading_problems(composed) == []
    for named in (["--agent", "kiro_default"], ["--agent=kiro_default"]):
        assert any("it names --agent" in problem for problem in A.reading_problems(composed + named)), named


def test_a_reviewer_agent_ao_cannot_write_is_named():
    for name, adapter in A.package_adapters().items():
        assert A.reviewer_agent_problems(adapter) == [], name
    broken = {"options": {"reviewer_agent": {"argv": ["--agent"], "path": "../x/{agent}.json", "config": {}}}}

    assert len(A.reviewer_agent_problems(broken)) == 3


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_kiro_review_runs_its_harness_as_the_agent_written_beside_the_tree(project, tmp_path):
    """End to end: the stand-in kiro-cli finds the agent ao named in its own directory, holding read tools alone."""
    harness = tmp_path / "kiro-cli"
    harness.write_text(f"""#!{sys.executable}
import json, os, sys
name = sys.argv[sys.argv.index("--agent") + 1]
with open(os.path.join(".kiro", "agents", name + ".json"), encoding="utf-8") as fh:
    tools = json.load(fh)["tools"]
assert tools == ["read", "grep", "glob"], tools
for line in ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0"):
    print(line)
""", encoding="utf-8")
    harness.chmod(0o755)
    argv = [str(harness), "chat", "--no-interactive", "review this", "--model", "m", "--trust-tools="]

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["ok"] and attempt["out"].startswith("VERDICT: APPROVED"), attempt


def test_a_kiro_reviewer_over_acp_runs_as_the_agent_too(project, monkeypatch):
    """KIRO-READONLY-2: with `review.transport` acp, a kiro reviewer ran `kiro-cli acp` with no agent of ao's."""
    from ao import acp
    seen = {}

    class Recording:
        def __init__(self, argv, cwd, env=None, permission=None):
            name = argv[argv.index("--agent") + 1]
            with open(os.path.join(cwd, ".kiro", "agents", f"{name}.json"), encoding="utf-8") as fh:
                seen.update(argv=list(argv), tools=json.load(fh)["tools"])
            raise acp.ProbeError("stopped here: the test reads what the session was started with")

    monkeypatch.setattr(acp, "Session", Recording)

    cli._run_acp_reviewer(project["root"], ["kiro-cli", "acp"], "kiro", "review this", 60, "kiro")

    assert seen["argv"][:3] == ["kiro-cli", "acp", "--agent"] and seen["tools"] == ["read", "grep", "glob"]


def _tree_with(tmp_path, name, make):
    """A git tree holding `name`, as `make(path)` writes it."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    make(repo / name)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True)
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()
    return str(repo), tree


@pytest.mark.skipif(os.name == "nt", reason="a link needs a privilege on Windows")
def test_a_tree_that_commits_the_agents_folder_as_a_link_unpacks_no_link(tmp_path):
    """KIRO-READONLY-2: the candidate's links are not unpacked, so ao's agent is written in a folder of its own."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    repo, tree = _tree_with(tmp_path, ".kiro", lambda path: os.symlink(elsewhere, path))
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    assert cli._unpack_candidate(repo, tree, str(fresh)) == str(fresh)
    argv, why = cli._reviewer_agent(str(fresh), KIRO)

    assert why is None and not os.path.islink(fresh / ".kiro") and (fresh / ".kiro" / "agents").is_dir()
    assert list(elsewhere.iterdir()) == []


def test_a_tree_that_commits_the_agents_folder_as_a_file_is_refused(tmp_path):
    repo, tree = _tree_with(tmp_path, ".kiro", lambda path: path.write_text("not a folder\n", encoding="utf-8"))
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    assert cli._unpack_candidate(repo, tree, str(fresh)) == str(fresh)
    assert "as a link or a file" in cli._reviewer_agent(str(fresh), KIRO)[1]
