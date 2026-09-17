"""The cycle's git queries without a shell, and the quota reading kept between processes (CYCLE-GIT).

A watchdog cycle, a status read, a verification and a review started a shell in front of
git for their small queries - the head, the dirty paths, the newest commit's time, what is
unpushed - on every cycle of every watched project. They ask git directly now and must read
what the shell read: whatever a failing git printed, the same stripping, the same answer
outside a repository. `ao since` handed its ref to that shell, which split it, expanded it
and ran what followed a `;`; the ref is one argument to git now. And every process took
the quota line again through a shell, because the reading was kept only in memory; it is
kept on disk now for the adapter's window, and never when the command failed.
"""
import ast
import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What each converted caller gave the shell, by the words it gives git now.
SHELL = {
    ("log", "-1", "--format=%ct"): "git log -1 --format=%ct",
    ("rev-parse", "--short", "HEAD"): "git rev-parse --short HEAD",
    ("rev-parse", "HEAD"): "git rev-parse HEAD",
    ("status", "--porcelain"): "git status --porcelain",
    ("rev-parse", "--abbrev-ref", "@{u}"): "git rev-parse --abbrev-ref @{u} 2>/dev/null",
    ("rev-list", "--count", "@{u}..HEAD"): "git rev-list --count @{u}..HEAD 2>/dev/null",
    ("log", "--branches", "--not", "--remotes", "--pretty=%h"): "git log --branches --not --remotes --pretty=%h",
}


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True,
                   capture_output=True)


def _write(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _places(project, tmp_path):
    """The project ahead of its upstream and dirty every way, a repository without commits, a plain directory.

    Each already holds what the callers write - a review, the ledger directory - so the
    shell's answers, read first, are the answers the callers get.
    """
    root = project["root"]
    spaced, named = os.path.join(root, "dir with space", "a b.txt"), os.path.join(root, "ünï", "名前.md")
    for path in (spaced, named):
        _write(path, "base\n")
    _git(root, "add", "dir with space", "ünï")
    _git(root, "commit", "-q", "-m", "names with spaces and non-ASCII")
    _git(tmp_path, "init", "-q", "--bare", "origin.git")
    _git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(root, "push", "-q", "-u", "origin", "HEAD")
    for n in (1, 2):
        _git(root, "commit", "-q", "--allow-empty", "-m", f"unpushed {n}")
    _write(spaced, "staged\n")
    _git(root, "add", "dir with space")
    _write(named, "unstaged\n")
    _write(os.path.join(root, "untracked é.txt"), "new\n")
    empty, loose = tmp_path / "no-commits", tmp_path / "not-a-repository"
    empty.mkdir()
    _git(empty, "init", "-q")
    _write(empty / "staged.txt", "staged\n")
    _git(empty, "add", "staged.txt")
    loose.mkdir()
    places = [root, str(empty), str(loose)]
    for place in places:
        _write(os.path.join(place, ".ao", "ledger", "progress.jsonl"), "")
        review = os.path.join(place, "semantic-review", "r-001.md")
        _write(review, "# review\n\nVERDICT: NEEDS_CHANGES\n")
        os.utime(review, (4102444800, 4102444800))           # newer than any commit here
    return places


def _shell_flags(monkeypatch):
    """Whether each subprocess.run from here on was asked for a shell."""
    started, real_run = [], subprocess.run

    def run(*args, **kwargs):
        started.append(bool(kwargs.get("shell")))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return started


def test_the_cycles_git_queries_read_what_the_shell_read_and_start_no_shell(project, tmp_path, monkeypatch):
    places = _places(project, tmp_path)
    root = places[0]
    shell = {(place, words): A.sh(command, cwd=place) for place in places for words, command in SHELL.items()}
    asked, real_text = [], A._git_text

    def text(cwd, *args, **kwargs):
        answer = real_text(cwd, *args, **kwargs)
        asked.append((str(cwd), args, answer))
        return answer

    monkeypatch.setattr(A, "_git_text", text)
    started = _shell_flags(monkeypatch)
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "spinning", lambda root, *args, **kwargs: 7)
    monkeypatch.setattr(A, "account_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(A, "credit_usage", lambda *args, **kwargs: {"days": {}})
    answers = {}
    for place in places:
        cfg = dict(project, root=place)
        answers[place] = {"open_work": W.open_work(cfg, place), "fingerprint": A.work_fingerprint(place, cfg),
                          "unpushed": A.digest(place, cfg)["unpushed"], "anomalies": A.anomalies(place, cfg, {}, 0, 360)}
        A.record_progress(place, cfg)
    waiver = A.waive(root, "review", "S1", "a person accepts the risk", by="Test Owner")

    assert started and not any(started)
    assert {words for _, words, _ in asked} >= set(SHELL)
    for cwd, words, answer in asked:
        if words in SHELL:
            assert answer == shell[(cwd, words)], (cwd, words)
    with open(os.path.join(root, ".ao", "ledger", "progress.jsonl"), encoding="utf-8") as fh:
        assert json.loads(fh.readlines()[-1])["head"] == shell[(root, ("rev-parse", "--short", "HEAD"))] != ""
    assert waiver["head"] == shell[(root, ("rev-parse", "HEAD"))] and len(waiver["head"]) == 40
    assert answers[root]["unpushed"] == 2 and "open review findings" in answers[root]["open_work"]
    assert "open review findings" in answers[places[2]]["open_work"]


def test_a_verification_records_the_head_and_dirty_count_the_shell_read(project, tmp_path, monkeypatch):
    root = project["root"]
    _write(os.path.join(root, "src", "m.py"), "value = 1\n")
    _git(root, "add", "src/m.py")
    _write(os.path.join(root, "untracked é.txt"), "new\n")
    gate = (subprocess.list2cmdline if os.name == "nt" else shlex.join)([sys.executable, "-c", "print('ok')"])
    spec = {"gates": {"test": {"run": gate, "timeout": 30}}, "profiles": {"quick": ["test"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    head, status = A.sh("git rev-parse --short HEAD", cwd=root), A.sh("git status --short", cwd=root)
    through_a_shell, git = [], A._shell_word(A.git_binary())       # how sh() names git to its shell
    real_run = subprocess.run

    def run(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if kwargs.get("shell") and isinstance(command, str) and command.startswith(git + " "):
            through_a_shell.append(command)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0))

    record = A.latest_verification(root)
    assert record["head"] == head != ""
    assert record["dirty"] == len([line for line in status.split("\n") if line.strip()]) == 3
    assert through_a_shell == []


def test_since_hands_git_one_ref_and_runs_nothing_a_ref_carries(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    _git(root, "commit", "-q", "--allow-empty", "-m", "second commit")
    ran = tmp_path / "ran"
    started = _shell_flags(monkeypatch)

    assert cli.cmd_since(project, SimpleNamespace(ref="HEAD~1", no_mark=True)) == 0
    assert cli.cmd_since(project, SimpleNamespace(ref=":/second commit", no_mark=True)) == 0     # one ref, with a space
    capsys.readouterr()
    for ref in (f"HEAD;touch {ran}", f"HEAD $(touch {ran})", f"--output={ran}"):
        assert cli.cmd_since(project, SimpleNamespace(ref=ref, no_mark=True)) == 1, ref
        assert f"a git ref, or 'last': {ref}" in capsys.readouterr().out

    assert not ran.exists()
    assert started and not any(started)


def test_no_git_query_goes_through_a_shell():
    found = []
    for directory, _, names in os.walk(os.path.join(ROOT, "src", "ao")):
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and node.args and "sh" in (getattr(node.func, "id", None),
                                                                              getattr(node.func, "attr", None))):
                    continue
                first = node.args[0]
                if isinstance(first, ast.JoinedStr) and first.values:
                    first = first.values[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("git "):
                    found.append(f"{os.path.relpath(path, ROOT)}:{node.lineno}")

    assert found == []


PROBE = r"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
from ao import lib as A
started = []
real_run = subprocess.run
def spy(*args, **kwargs):
    started.append({"shell": bool(kwargs.get("shell")), "executable": kwargs.get("executable"), "cwd": kwargs.get("cwd")})
    return real_run(*args, **kwargs)
subprocess.run = spy
print(json.dumps({"lines": A.quota({"telemetry": {"quota": json.loads(sys.argv[2])}}), "started": started}))
"""


def _quota_tool(tmp_path, *lines, status=0):
    """A quota command on PATH that notes where it ran and with how many words, then prints `lines`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "calls.txt"
    tool = bindir / "ao-quota-probe"
    body = "".join(f"echo {shlex.quote(line)}\n" for line in lines)
    tool.write_text(f'#!/bin/sh\necho "$(pwd -P) $# $*" >> {shlex.quote(str(calls))}\n{body}exit {status}\n',
                    encoding="utf-8")
    tool.chmod(0o755)
    return str(tool), calls


def _calls(calls):
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _another_process(home, bindir, spec):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update(HOME=str(home), PATH=str(bindir) + os.pathsep + env.get("PATH", ""))
    run = subprocess.run([sys.executable, "-I", "-c", PROBE, ROOT, json.dumps(spec)], capture_output=True,
                         text=True, env=env, timeout=120)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stdout + run.stderr
    return json.loads(lines[-1])


@pytest.mark.skipif(os.name == "nt", reason="the quota command here is a POSIX shell script")
def test_a_quota_reading_is_kept_across_processes_for_its_window_and_taken_again_after(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tool, calls = _quota_tool(tmp_path, "Usage (probe):", "  alpha  42%  5h", "  beta   unknown")
    spec = {"from": "command", "argv": ["ao-quota-probe", "usage", "--providers"], "cache_seconds": 300}

    first = _another_process(home, tmp_path / "bin", spec)
    second = _another_process(home, tmp_path / "bin", spec)

    assert first["lines"] == second["lines"] == ["alpha  42%  5h"]
    assert _calls(calls) == [f"{os.path.realpath(home)} 2 usage --providers"]
    assert first["started"] == [{"shell": False, "executable": tool, "cwd": str(home)}] and second["started"] == []

    kept_file = home / ".ao" / "quota.json"
    kept = json.loads(kept_file.read_text(encoding="utf-8"))
    assert kept["ao-quota-probe usage --providers"]["lines"] == ["alpha  42%  5h"]
    kept["ao-quota-probe usage --providers"]["at"] -= 301
    kept_file.write_text(json.dumps(kept), encoding="utf-8")
    third = _another_process(home, tmp_path / "bin", spec)
    other = _another_process(home, tmp_path / "bin", dict(spec, argv=["ao-quota-probe", "usage"]))

    assert third["lines"] == other["lines"] == ["alpha  42%  5h"]
    assert len(_calls(calls)) == 3 and _calls(calls)[-1].endswith(" 1 usage")
    assert set(json.loads(kept_file.read_text(encoding="utf-8"))) == {"ao-quota-probe usage --providers",
                                                                     "ao-quota-probe usage"}


@pytest.mark.skipif(os.name == "nt", reason="the quota command here is a POSIX shell script")
def test_a_failed_quota_command_is_not_kept_and_a_word_the_shell_splits_still_goes_through_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    _, calls = _quota_tool(tmp_path, "Usage (probe):", "  alpha  99%  5h", status=3)
    monkeypatch.setattr(A, "HOME", str(home))
    monkeypatch.setattr(A, "_QUOTA", {})
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ.get("PATH", ""))
    failing = {"telemetry": {"quota": {"argv": ["ao-quota-probe", "usage"], "cache_seconds": 300}}}

    assert A.quota(failing) == A.quota(failing) == ["alpha  99%  5h"]
    assert len(_calls(calls)) == 2 and not (home / ".ao" / "quota.json").exists()
    assert A.quota({"telemetry": {"quota": {"argv": ["ao-quota-absent-probe"]}}}) == []

    started = _shell_flags(monkeypatch)
    A.quota({"telemetry": {"quota": {"argv": ["ao-quota-probe", "two words"]}}})

    assert started == [True] and _calls(calls)[-1] == f"{os.path.realpath(home)} 2 two words"
    assert not (home / ".ao" / "quota.json").exists()
