"""Gate runs and the Git queries authority makes, held to what they answer and to their time (LEADS-GATE-RUNS).

ao's read-only bug hunter read src/ao/parts on 2026-10-12 and left leads, not verdicts. Measured, these held: an
`expect: empty` gate passed whatever its command answered - grep's 2 is an error, and a sweep that sent it to
/dev/null passed - so it counts now only on 0, or a code the gate declares in ok_exit; a timeout killed git, or a
gate's shell, alone and what it started ran on; a gates.json ao could not run raised once the machine lock was held;
and two verifications in one second shared an id.
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, settings as S
from tests import standin

# A program that starts a child and waits for it: the child's pid is written to `pidfile` for the test to ask after.
TREE = ("import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "with open({pidfile!r}, 'w') as fh:\n"
        "    fh.write(str(child.pid))\n"
        "time.sleep(120)\n")


def _command(argv):
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _child_of(pidfile):
    """The pid the tree's child wrote, once it has."""
    for _ in range(100):
        if os.path.exists(pidfile) and open(pidfile, encoding="utf-8").read().strip():
            return int(open(pidfile, encoding="utf-8").read())
        time.sleep(0.05)
    raise AssertionError(f"{pidfile} was never written")


def _still_runs(pid):
    """Whether `pid` runs a second after it was asked to stop; it is stopped here either way, so no test leaves it."""
    deadline = time.time() + 1
    while A._pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    alive = A._pid_alive(pid)
    if alive:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass
    return alive


def _gates(root, gates, profile="quick"):
    spec = {"gates": gates, "profiles": {profile: list(gates)}, "default_profile": profile}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)


# ---- 4cf420aa7655f252: a git query past its timeout is stopped with what it started ---------------

def test_a_git_query_past_its_timeout_is_stopped_with_what_it_started(tmp_path, monkeypatch):
    pidfile = str(tmp_path / "child.pid")
    git = standin.program(tmp_path / "bin", "git", TREE.format(pidfile=pidfile))
    monkeypatch.setattr(A, "git_binary", lambda: git)

    started = time.time()
    with pytest.raises(cli._HookResolutionError, match="git query failed"):
        cli._hook_git(str(tmp_path), "hook", "run", "pre-commit", timeout=2)

    assert not _still_runs(_child_of(pidfile))
    assert time.time() - started < 30


# ---- 2b719363f06576b1: an empty sweep passes on its program's answer, not on a command that never ran -

@pytest.mark.parametrize(("code", "passed"), ((0, True), (1, False), (2, False), (125, False), (126, False),
                                              (127, False), (128, False), (137, False), (-9, False), (9009, False)))
def test_an_empty_gate_passes_only_where_its_command_answered_and_fails_on_a_command_that_failed(code, passed):
    ok, detail, counts = cli._judge_gate({"run": "x", "expect": "empty"}, "", code)

    assert (ok, counts) == (passed, None)
    assert detail == "clean" if passed else detail.startswith(f"exit {code} and nothing printed: the command failed")
    assert cli._judge_gate({"run": "x", "expect": "empty", "ok_exit": [0, 1]}, "left.tmp\n", 1)[:2] == (False,
                                                                                                    "left.tmp")


def test_an_empty_gate_counts_the_codes_it_declares_and_its_shape_is_checked():
    """`grep -R '[' src 2>/dev/null` exits 2, an error, and printed nothing: it passed (LEADS-GATE)."""
    declared = {"run": "ls -d .tmp-* 2>/dev/null", "expect": "empty", "ok_exit": [1, 2]}

    assert cli._judge_gate(declared, "", 2)[0] is True and cli._judge_gate(declared, "", 3)[0] is False
    assert cli._judge_gate(declared, "", 0)[0] is True                    # what it declares adds to 0, never takes it
    assert cli._gate_shape_problem(declared) is None
    for bad in ([], [True], [126], ["1"], 1):
        assert "ok_exit" in cli._gate_shape_problem(dict(declared, ok_exit=bad)), bad
    assert "above zero" in cli._gate_shape_problem({"run": "x", "timeout": 0})
    # 1e308 was taken, then overflowed the wait once the machine lock was held.
    for long in (1e308, 7 * 24 * 3600 + 1):
        assert "at most a week" in cli._gate_shape_problem({"run": "x", "timeout": long}), long
    assert cli._gate_shape_problem({"run": "x", "timeout": 7 * 24 * 3600}) is None
    # The default a gate without a timeout runs under has the same ceiling: past it, it is passed over and named.
    value, source, problem = S.resolve({"gates": {"default_timeout": 10 ** 12}}, "gates.default_timeout")
    assert (value, source) == (600, "default") and str(S.GATE_TIMEOUT_MAX) in problem
    # ... and is refused before the lock where a gate would run under it, as a gate's own is, not passed over.
    spec, past = {"gates": {"g": {"run": "x"}}, "profiles": {"quick": ["g"]}}, {"gates": {"default_timeout": 604801}}
    assert "runs gate g with no timeout of its own" in cli._profile_gates(spec, "quick", past)[1]
    timed = {"gates": {"g": {"run": "x", "timeout": 60}}, "profiles": {"quick": ["g"]}}
    assert cli._profile_gates(timed, "quick", past) == (["g"], None)
    unnamed = {"gates": {"": {"run": "x"}}, "profiles": {"quick": [""]}}
    assert "with no timeout of its own" in cli._profile_gates(unnamed, "quick", past)[1]
    for floor in (float("inf"), [], "", "300", True, -1, 2.5):
        assert "min_tests" in cli._gate_shape_problem({"run": "x", "min_tests": floor}), floor
    assert cli._gate_shape_problem({"run": "x", "min_tests": 300}) is None
    grep = {"run": "git grep -n TODO", "expect": "empty", "ok_exit": [0, 1]}
    assert cli._judge_gate(grep, "", 1)[0] is True and cli._judge_gate({"run": "x", "expect": "empty"}, "", 1)[0] is False
    assert cli._profile_gates({"gates": {}, "profiles": {"quick": ""}}, "quick")[1] == \
        "profile quick is not a list of gate names"


def test_verify_fails_an_empty_gate_whose_command_was_not_found(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    _gates(root, {"sweep": {"run": _command([sys.executable, "-c", "import sys; sys.exit(127)"]), "expect": "empty"},
                  "grep": {"run": _command([sys.executable, "-c", "import sys; sys.exit(1)"]), "expect": "empty",
                           "ok_exit": [0, 1]}})

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    gates = {gate["name"]: gate for gate in A.latest_verification(root)["gates"]}
    assert (gates["sweep"]["passed"], gates["sweep"]["exit"]) == (False, 127)
    assert (gates["grep"]["passed"], gates["grep"]["detail"]) == (True, "clean")


# ---- 8ce12e1e3d2bab33: a gates.json ao cannot run is one line, before the lock -------------------

GOOD = {"run": "true", "timeout": 30}


@pytest.mark.parametrize(("written", "said"), (
    ("{not json", "Expecting property name"),
    ("[]", "it is not a JSON object"),
    (json.dumps({"gates": [], "profiles": {"quick": ["smoke"]}}), "its profiles and gates are not both JSON objects"),
    (json.dumps({"gates": {"smoke": GOOD}, "profiles": {"quick": "smoke"}}), "profile quick is not a list of gate"),
    (json.dumps({"gates": {}, "profiles": {"quick": ["smoke"]}}), "runs gate smoke, and no gate defines it"),
    (json.dumps({"gates": {"smoke": {"timeout": 30}}, "profiles": {"quick": ["smoke"]}}), "has no command in run"),
    (json.dumps({"gates": {"smoke": {"run": "true", "timeout": "soon"}}, "profiles": {"quick": ["smoke"]}}),
     "its timeout is not a number of seconds"),
    (json.dumps({"gates": {"smoke": {"run": "true", "min_tests": "many"}}, "profiles": {"quick": ["smoke"]}}),
     "its min_tests is not a whole number of tests"),
    (json.dumps({"gates": {"smoke": GOOD}, "profiles": {"quick": ["smoke"]}, "default_profile": ["quick"]}),
     "is not a name"),
))
def test_a_gates_file_ao_cannot_run_is_said_in_one_line_before_the_lock_is_taken(project, monkeypatch, capsys,
                                                                                 written, said):
    root = project["root"]
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        fh.write(written)
    taken = []
    monkeypatch.setattr(cli, "_take_gate_lock", lambda cfg, wait: taken.append(wait) or (lambda: None))

    assert cli.cmd_verify(project, SimpleNamespace(profile=None, wait=0)) == 2
    assert cli.cmd_merge_check(project, SimpleNamespace(profile=None, wait=0, into="HEAD", branch="HEAD")) == 2

    out = capsys.readouterr().out
    assert out.count("no usable .ao/gates.json") == 2 and said in out
    assert taken == [] and A.latest_verification(root) is None


# ---- 0582a3e732b7458e, 95159c8db24b3d9a: a gate past its timeout is stopped with its workers --------

def test_a_gate_past_its_timeout_is_stopped_with_the_workers_it_started(project, tmp_path, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    pidfile = str(tmp_path / "worker.pid")
    worker = tmp_path / "suite.py"
    worker.write_text(TREE.format(pidfile=pidfile), encoding="utf-8")
    _gates(root, {"suite": {"run": _command([sys.executable, str(worker)]), "timeout": 2}})

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    gate = A.latest_verification(root)["gates"][0]
    assert (gate["passed"], gate["exit"]) == (False, 124)
    assert not _still_runs(_child_of(pidfile))
    assert not os.path.exists(str(tmp_path / "gate.lock"))



# A program that starts a child which moves into a process group of its own, and waits.
GROUPED = ("import subprocess, sys, time\n"
           "child = subprocess.Popen([sys.executable, '-c', 'import os, time; os.setpgid(0, 0); time.sleep(120)'])\n"
           "with open({pidfile!r}, 'w') as fh:\n"
           "    fh.write(str(child.pid))\n"
           "time.sleep(120)\n")


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX's; Windows stops a tree with taskkill /T")
def test_a_gate_whose_worker_left_its_process_group_is_stopped_with_it(project, tmp_path, monkeypatch):
    """The timeout signalled the gate's group, and named a worker that had moved into a group of its own among what was
    left - running, after the machine lock was let go. It is signalled by its pid now (LEADS-GATE-RUNS)."""
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    pidfile = str(tmp_path / "worker.pid")
    worker = tmp_path / "suite.py"
    worker.write_text(GROUPED.format(pidfile=pidfile), encoding="utf-8")
    _gates(root, {"suite": {"run": _command([sys.executable, str(worker)]), "timeout": 2}})

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    child = _child_of(pidfile)
    assert not _still_runs(child)
    assert "still run after it was stopped" not in A.latest_verification(root)["gates"][0].get("detail", "")


# A program that starts a child which moves into a process group of its own, and leaves it there: the child's parent
# is gone, so no parent link leads from the gate to it.
ORPHANED = ("import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import os, time; os.setpgid(0, 0); time.sleep(120)'])\n"
            "with open({pidfile!r}, 'w') as fh:\n"
            "    fh.write(str(child.pid))\n")


@pytest.mark.skipif(os.name == "nt", reason="sessions and process groups are POSIX's")
def test_a_worker_that_left_the_gates_group_and_lost_its_parent_is_stopped_by_its_session(project, tmp_path,
                                                                                          monkeypatch):
    """Neither the tree nor the group led to a worker that moved into a group of its own once its parent had exited:
    it ran on after the machine lock was let go, named nowhere. The gate leads a session, and only a session of its
    own leaves it (LEADS-GATE-RUNS)."""
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    pidfile = str(tmp_path / "worker.pid")
    worker = tmp_path / "suite.py"
    worker.write_text(ORPHANED.format(pidfile=pidfile), encoding="utf-8")
    _gates(root, {"suite": {"run": _command([sys.executable, str(worker)]) + "; sleep 120", "timeout": 3}})

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    assert not _still_runs(_child_of(pidfile))


@pytest.mark.skipif(os.name == "nt", reason="sessions are POSIX's")
def test_a_process_table_that_cannot_be_read_during_the_stop_is_said(project, tmp_path, monkeypatch, capsys):
    """A table that could not be read was read as a session with nobody else in it, and the stop said nothing of what
    might still run (LEADS-GATE-RUNS)."""
    from ao import procs
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    _gates(root, {"suite": {"run": _command([sys.executable, "-c", "import time; time.sleep(120)"]), "timeout": 2}})

    def unreadable():
        raise procs.TableUnreadable("no process table here")
    monkeypatch.setattr(procs, "refresh", unreadable)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    assert "could not be read for the rest of its session, which may still run" in capsys.readouterr().out


def test_a_gate_the_system_cannot_start_is_its_failure_recorded_not_an_exception(project, tmp_path, monkeypatch):
    """A command line past ARG_MAX passed the shape check, and its start raised once the machine lock was held: no gate
    answer was recorded. It is the gate's failure now (LEADS-GATE-RUNS)."""
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    limit = os.sysconf("SC_ARG_MAX") if hasattr(os, "sysconf") else 32767
    _gates(root, {"smoke": {"run": "echo " + "x" * (limit + 1024), "timeout": 30}})

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1

    gate = A.latest_verification(root)["gates"][0]
    assert gate["passed"] is False and gate["exit"] == 127
    assert not os.path.exists(str(tmp_path / "gate.lock"))

# ---- 05a456a3d6357050: no two verifications share an id --------------------------------------------

class _StoppedClock:
    """cli's time module, its clock stopped at one second: two runs read the same second, as on a fast machine."""

    def __init__(self, now):
        self.now = now

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return self.now


def test_verifications_in_one_second_or_after_the_clock_stepped_back_get_ids_of_their_own(project, tmp_path,
                                                                                          monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    _gates(root, {"smoke": {"run": _command([sys.executable, "-c", "pass"]), "timeout": 30}})
    second = int(time.time())
    monkeypatch.setattr(cli, "time", _StoppedClock(second))
    ahead = f"V-{A.unique_ms() + 3_600_000}"           # a run an hour ahead of this clock, before it was set back
    A.record_verification(root, {"id": f"V-{second}", "passed": False, "gates": []})

    ids = []
    for _ in range(2):
        assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
        ids.append(A.latest_verification(root)["id"])
    A.record_verification(root, {"id": ahead, "passed": False, "gates": []})
    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
    ids.append(A.latest_verification(root)["id"])

    assert len(set(ids + [f"V-{second}", ahead])) == 5
    assert int(ids[2][2:]) > int(ahead[2:]) and int(ids[1][2:]) > int(ids[0][2:])


def test_two_verifications_minted_one_id_outside_the_ledger_record_two(project):
    """Two runs under one held machine lock read the same newest id in one millisecond (LEADS-GATE): the id a row
    goes under is decided under the ledger's lock, above every id recorded."""
    root = project["root"]
    first = {"id": "V-1000000", "at": "x", "passed": True, "profile": "quick", "gates": []}
    second = dict(first)

    one, two = A.record_verification(root, first), A.record_verification(root, second)

    assert one["id"] == "V-1000000" and two["id"] == "V-1000001" and second["id"] == "V-1000001"


def test_a_timeout_whose_tree_cannot_be_stopped_says_so_and_never_reads_as_nothing_left(monkeypatch, tmp_path):
    """No taskkill on Windows: kill_turn raised, the leader alone was ended, and the run read as nothing left while
    what the gate started ran on (LEADS-GATE)."""
    def no_tree_kill(pid, sig):
        raise FileNotFoundError(2, "taskkill is not where Windows keeps it")

    monkeypatch.setattr(A, "kill_turn", no_tree_kill)

    with pytest.raises(subprocess.TimeoutExpired) as stopped:
        A.run_as_group([sys.executable, "-c", "import time; time.sleep(30)"], 0.5, capture_output=True)

    assert stopped.value.left == [] and "could not be stopped or asked after" in stopped.value.unasked
    out, code = cli._run_gate({}, {"run": _command([sys.executable, "-c", "import time; time.sleep(30)"]),
                                   "timeout": 0.5}, str(tmp_path))
    assert code == 124 and "could not be stopped or asked after" in out and "stop them by hand" in out


def test_a_gate_whose_command_holds_a_nul_is_refused_before_the_lock():
    assert "NUL" in cli._gate_shape_problem({"run": "echo\x00x"})
