import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, procs, watchdog as W
from tests.scenarios import World


def test_a_windows_working_directory_names_a_transcript_directory_under_claude_projects(project, monkeypatch):
    base = os.path.join(A.HOME, ".claude", "projects")
    monkeypatch.setattr(A.os, "name", "nt")
    assert A.escaped_cwd_dir(A.load_adapter("claude-code")["sessions"], "C:\\Users\\me\\repo.v2") == os.path.join(base, "C--Users-me-repo-v2")

    monkeypatch.setattr(A.os, "name", "posix")
    assert A.escaped_cwd_dir(A.load_adapter("claude-code")["sessions"], "/srv/me/repo.v2") == os.path.join(base, "-srv-me-repo-v2")


def test_a_transcript_directory_that_exists_is_used_as_found(project, monkeypatch):
    base = os.path.join(A.HOME, ".claude", "projects")
    os.makedirs(os.path.join(base, "-srv-my_repo"))

    assert A.escaped_cwd_dir(A.load_adapter("claude-code")["sessions"], "/srv/my_repo") == os.path.join(base, "-srv-my_repo")
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "abc", "cwd": "/srv/my_repo"})
    assert A.session_paths(cfg)[0] == os.path.join(base, "-srv-my_repo", "abc.jsonl")


def _windows_table(monkeypatch, root, rows):
    monkeypatch.setattr(A.os, "name", "nt")
    monkeypatch.setattr(procs, "all_pids", lambda: list(rows))
    monkeypatch.setattr(procs, "argv", lambda pid: rows[pid])
    monkeypatch.setattr(procs, "cwd", lambda pid: None)
    monkeypatch.setattr(A, "helper_pids", lambda target, what=None: set())


def test_an_agent_windows_cannot_place_is_named_and_one_that_names_the_tree_is_placed(project, monkeypatch):
    root = project["root"]
    _windows_table(monkeypatch, root, {
        7: ["C:\\tools\\kiro-cli.exe", "chat", "--resume-id", "s1"],
        8: ["C:\\tools\\kiro-cli.exe", "chat", root],
        9: ["C:\\Windows\\notepad.exe"],
    })

    assert A.unplaced_agent_pids(root, A.load_adapter("kiro")) == [7]

    monkeypatch.setattr(A.os, "name", "posix")
    assert A.unplaced_agent_pids(root, A.load_adapter("kiro")) == []


def test_the_watchdog_starts_no_turn_beside_an_agent_it_cannot_place(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [7])

    trace = world.cycle()

    assert any("cannot be placed in a tree" in line for line in trace)
    assert "nudging" not in world.verdict


def test_writers_and_hold_do_not_report_nobody_when_they_cannot_see(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [7])
    monkeypatch.setattr(A, "writers", lambda root, adapter: ([], []))
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, **kw: [])
    monkeypatch.setattr(A, "orphans", lambda root, adapter: [])

    assert cli.cmd_writers(project, SimpleNamespace(clean=False, json=False)) == 1
    assert "writers unknown" in capsys.readouterr().out

    args = SimpleNamespace(action="hold", by="a person", reason="editing", grace=0, note=None)
    assert cli.cmd_hold(project, args) == 1
    out = capsys.readouterr().out
    assert "no agent turn could be placed" in out and "were not stopped" in out
    assert A.hold_state(project["root"])


def test_the_shell_helper_speaks_cmd_on_windows(monkeypatch):
    seen = []
    monkeypatch.setattr(A.os, "name", "nt")
    monkeypatch.setattr(A.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                        SimpleNamespace(stdout="", returncode=0))

    A.sh("keyflip surfaces 2>/dev/null")

    assert seen == ["keyflip surfaces 2>NUL"]


def test_the_digest_reads_commits_without_a_shell(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: None)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                    "-m", "a | subject with a pipe"], cwd=root, check=True)

    subjects = [c["subject"] for c in A.digest(root, project)["commits"]]

    assert "a | subject with a pipe" in subjects


def test_a_cmd_reviewer_is_refused_on_windows(project, monkeypatch):
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: ("C:\\bin\\review.cmd", "1.0"))
    monkeypatch.setattr(A.os, "name", "nt")

    label, _, _, attempt = cli._reviewer_route_invocation(
        project["root"], {"id": "r", "argv": ["review", "{prompt}"]}, "prompt", 30, False, None)

    assert attempt["kind"] == "configuration-error" and "8191" in attempt["reason"]


def test_the_telegram_poller_is_refused_where_there_is_no_launchd(project, monkeypatch, capsys):
    A.project_key(project["root"])          # registered first: the lock is not taken under "nt"
    monkeypatch.setattr(A.os, "name", "nt")

    assert cli.cmd_telegram(project, SimpleNamespace(action="install", once=False)) == 1
    assert "Task Scheduler" in capsys.readouterr().out


def test_a_transcript_that_cannot_be_found_is_a_problem_while_the_implementer_is_driven(project, monkeypatch):
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "gone", "cwd": project["root"]})
    from ao import features as F
    monkeypatch.setattr(F, "enabled", lambda cfg, key: True)

    keys = [key for key, _ in cli.doctor_problems(cfg)]

    assert "transcript-missing" in keys and "transcript-missing" in cli.DOCTOR_RED


def test_a_shared_hook_is_refused_on_windows_even_when_allowed(monkeypatch, capsys):
    target = {"needs_authorization": True, "directory_class": "shared", "directory": "/srv/hooks",
              "globally_configured": False}
    monkeypatch.setattr(A.os, "name", "nt")

    assert cli._authorization_refusal([target], True) is True
    assert "cannot recognise this repository on Windows" in capsys.readouterr().out
