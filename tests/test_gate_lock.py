"""The machine gate lock is waited for whoever holds it, except by the run it is held for (GATE-LOCK-SAME-ROOT).

`ao verify` and `ao merge-check` waited for the lock only when another project held it. Held for
their own project - an implementer's `ao lock -- <suite>` beside the architect's `ao verify` in one
checkout - they tried to take it, ignored that the take failed and ran a second suite anyway; and two
runs that looked at a free lock at the same moment both went on the same way
(docs/audit/2026-09-08-adversarial.md). Now the lock is taken before it is looked at, and a holder is
waited for whichever project it runs for. The one holder a run goes ahead under is its own: this
process, already holding it, or a process that started this one, as `ao lock -- ao verify` does.
"""
import json
import os
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _gates(root):
    """One gate that passes, in a quick and a full profile."""
    argv = [sys.executable, "-c", "print('gate ok')"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    spec = {"gates": {"smoke": {"run": command, "expect": "exit_zero", "timeout": 60}},
            "profiles": {"quick": ["smoke"], "full": ["smoke"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)


def _held(lock, root, pid):
    """The lock as a run for `root` in process `pid` writes it."""
    lock.write_text(json.dumps({"root": root, "pid": pid, "at": int(time.time())}), encoding="utf-8")


class _Clock:
    """lib's time module, except that sleeping moves this clock on instead of waiting."""

    def __init__(self):
        self.now, self.slept = time.time(), 0

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


@pytest.fixture
def lock(tmp_path, monkeypatch):
    """The machine gate lock, in this test's directory instead of the machine's."""
    path = tmp_path / "gate.lock"
    monkeypatch.setattr(A, "GATE_LOCK", str(path))
    return path


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(A, "time", clock)
    return clock


@pytest.fixture
def other_process():
    """A live process that is neither this one nor one that started it, so a lock it holds is someone else's."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield child.pid
    child.kill()
    child.wait()


def test_verify_waits_for_a_run_in_its_own_project_and_refuses_when_it_does_not_end(project, lock, clock,
                                                                                    other_process, capsys):
    root = project["root"]
    _gates(root)
    _held(lock, root, other_process)                 # an `ao lock -- <suite>` in this checkout, say

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 2

    out = capsys.readouterr().out
    assert clock.slept >= 60                         # the whole --wait, then the refusal
    assert "another run in this project holds the machine lock" in out and f"pid {other_process}" in out
    assert "still busy; not starting a second suite" in out
    assert A.latest_verification(root) is None       # no gate ran, so nothing was recorded
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == other_process


def test_verify_still_waits_for_another_project_and_refuses_as_it_did(project, lock, clock, other_process,
                                                                      tmp_path, capsys):
    root = project["root"]
    _gates(root)
    _held(lock, str(tmp_path / "other-project"), other_process)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 2

    out = capsys.readouterr().out
    assert clock.slept >= 60
    assert "other-project is running its gates" in out and "still busy; not starting a second suite" in out
    assert A.latest_verification(root) is None
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == other_process


def test_a_lock_taken_between_the_look_and_the_take_is_waited_for(project, lock, clock, other_process, tmp_path,
                                                                   monkeypatch, capsys):
    """The race the audit found: two runs look, both find the lock free, one takes it, and the other ran anyway."""
    root = project["root"]
    _gates(root)
    _held(lock, str(tmp_path / "other-project"), other_process)
    real, looks = A.gate_lock_holder, []

    def free_at_the_first_look():
        looks.append(True)
        return None if len(looks) == 1 else real()

    monkeypatch.setattr(A, "gate_lock_holder", free_at_the_first_look)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 2

    assert clock.slept >= 60 and "still busy; not starting a second suite" in capsys.readouterr().out
    assert A.latest_verification(root) is None


def test_merge_check_waits_for_a_run_in_its_own_project_and_refuses_when_it_does_not_end(project, lock, clock,
                                                                                        other_process, capsys):
    root = project["root"]
    _gates(root)
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    with open(os.path.join(root, "feature.txt"), "w", encoding="utf-8") as fh:
        fh.write("feature\n")
    _git(root, "add", "feature.txt")
    _git(root, "commit", "-q", "-m", "feature")
    _git(root, "checkout", "-q", base)
    _held(lock, root, other_process)

    assert cli.cmd_merge_check(project, SimpleNamespace(branch="feature", into="HEAD", profile="full", wait=60)) == 2

    out = capsys.readouterr().out
    assert clock.slept >= 60 and "another run in this project holds the machine lock" in out
    assert A.merge_checks(root) == []                # no gate ran on the merge, so nothing was recorded
    assert len(_git(root, "worktree", "list").splitlines()) == 1


def test_a_verify_inside_a_hold_this_process_already_has_runs_and_leaves_the_lock_held(project, lock):
    root = project["root"]
    _gates(root)
    assert A.acquire_gate_lock(root, 0)
    try:
        assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
        assert A.latest_verification(root)["passed"] is True
        assert A.gate_lock_holder()["pid"] == os.getpid()    # it let go of nothing it had not taken
    finally:
        A.release_gate_lock()
    assert not lock.exists()


def test_a_verify_started_under_ao_lock_runs_inside_the_lock_its_parent_holds(project):
    root = project["root"]
    _gates(root)
    # Both processes find their home, and the machine lock in it, in this test's directory.
    env = A.self_child_env(dict(os.environ, HOME=A.HOME, USERPROFILE=A.HOME))
    ao = [sys.executable, "-m", "ao", "-C", root]

    done = subprocess.run(ao + ["lock", "--wait", "0", "--"] + ao + ["verify", "--wait", "0"], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "waiting up to" not in done.stdout        # it did not wait for the lock its own parent holds
    assert A.latest_verification(root)["passed"] is True
    assert not os.path.exists(os.path.join(A.HOME, ".ao", "gate.lock"))    # `ao lock` let it go at the end


def test_only_this_process_and_those_that_started_it_count_as_the_run_a_lock_is_held_for(other_process):
    assert A.self_or_ancestor(os.getpid())
    if os.getppid() > 1:
        assert A.self_or_ancestor(os.getppid())
    assert not A.self_or_ancestor(other_process)     # a process this one started did not start this one
    assert not A.self_or_ancestor(1)                 # every process descends from it, so it never counts
    assert not A.self_or_ancestor(None) and not A.self_or_ancestor("not a pid")


# ---- VERIFY-LOCK-LATE ---------------------------------------------------------------------------

def test_a_verify_with_no_gates_declared_says_so_without_waiting_for_the_lock(project, lock, clock,
                                                                            other_process, capsys):
    root = project["root"]
    _held(lock, root, other_process)                 # another suite holds the machine

    assert cli.cmd_verify(project, SimpleNamespace(profile=None, wait=60)) == 1

    out = capsys.readouterr().out
    assert "No .ao/gates.json" in out and "waiting up to" not in out
    assert clock.slept == 0                          # told at once, not after the wait
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == other_process


def test_a_verify_of_a_profile_the_project_lacks_says_so_without_waiting_for_the_lock(project, lock, clock,
                                                                                     other_process, capsys):
    root = project["root"]
    _gates(root)
    _held(lock, root, other_process)

    assert cli.cmd_verify(project, SimpleNamespace(profile="nightly", wait=60)) == 1

    out = capsys.readouterr().out
    assert "unknown profile nightly; have: quick, full" in out and "waiting up to" not in out
    assert clock.slept == 0
    assert A.latest_verification(root) is None


def test_a_verify_with_gates_to_run_still_waits_for_the_lock(project, lock, clock, other_process, capsys):
    root = project["root"]
    _gates(root)
    _held(lock, root, other_process)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=30)) == 2

    assert clock.slept >= 30 and "still busy; not starting a second suite" in capsys.readouterr().out
