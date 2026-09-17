"""What a Windows machine does to ao's files and processes, simulated where it can be (#9, #71).

The Windows lane runs weekly and on demand. These hold on every platform what a Windows
machine exposed: text written with CRLF, Git converting line endings on checkout,
and a process table that is a PowerShell query.
"""
import builtins
import os
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_init_writes_the_marker_it_checks_even_where_text_mode_writes_crlf(tmp_path, monkeypatch, capsys):
    root = tmp_path / "crlf-init"
    root.mkdir()
    _git(root, "init", "-q")

    def windows_text(file, mode="r", *args, **kwargs):
        # What open() does on Windows: a text write turns every "\n" into "\r\n".
        if "b" not in mode and len(args) < 4 and "newline" not in kwargs:
            kwargs["newline"] = "\r\n"
        return builtins.open(file, mode, *args, **kwargs)

    monkeypatch.setattr(cli, "open", windows_text, raising=False)
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": sys.executable,
        "version": "fixture", "reason": "exact nonce echoed", "kind": "success"})
    args = SimpleNamespace(name=None, profile="claude-kiro", implementer=None, model=None, effort=None,
                           reviewer_model=None, agent=None, no_mcp=True, rules=False, watchdog=False)

    assert cli.cmd_init({"root": str(root)}, args) == 0, capsys.readouterr().out

    assert (root / cli.PROJECT_MARKER).read_bytes() == cli.PROJECT_MARKER_BYTES


def test_vendored_skills_are_the_pinned_bytes_whatever_the_machine_converts(project, tmp_path, monkeypatch, capsys):
    # Git for Windows turns core.autocrlf on by default; here every git command sees it on.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    source = tmp_path / "skills-source"
    (source / "skills" / "alpha").mkdir(parents=True)
    skill = b"---\nname: alpha\n---\n\n# Alpha\n\nDo it well.\n"
    (source / "skills" / "alpha" / "SKILL.md").write_bytes(skill)
    _git(source, "init", "-q")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "skills")
    pin = _git(source, "rev-parse", "HEAD")
    root = project["root"]

    assert cli.cmd_content(project, SimpleNamespace(action="add", spec=f"{source}@{pin}", skills="alpha",
                                                    harness="claude-code,kiro")) == 0, capsys.readouterr().out

    with open(os.path.join(root, ".claude", "skills", "alpha", "SKILL.md"), "rb") as fh:
        assert fh.read() == skill
    with open(os.path.join(root, ".kiro", "steering", "alpha.md"), "rb") as fh:
        assert fh.read() == b"---\ninclusion: manual\n---\n\n# Alpha\n\nDo it well.\n"
    assert A.verify_content(root) == []


def test_a_process_a_fresh_windows_snapshot_lacks_is_not_queried_for_again(monkeypatch):
    from ao import procs
    queries = []
    monkeypatch.setattr(procs, "refresh", lambda: queries.append("snapshot"))
    monkeypatch.setattr(procs, "info", lambda pid: None)

    monkeypatch.setattr(A.os, "name", "nt")
    assert A._process_start(4242, refresh=True) is None
    assert queries == ["snapshot"]

    queries.clear()
    monkeypatch.setattr(A.os, "name", "posix")
    assert A._process_start(4242, refresh=True) is None
    assert queries == ["snapshot"] * 3
