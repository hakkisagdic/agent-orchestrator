"""What an `ao` run pays before it does anything, and what a status read starts (CLI-STARTUP).

A commit hook, a watchdog cycle and every status read an agent takes start the same
way. Importing ao read every package adapter to lay out the binary search path, the
parser read the adapter catalog once for each of init's and skill's `--agent`, a status
read started a shell in front of two of its git queries, and every setting lookup
opened and parsed the machine's settings file. Nothing here is timed: these hold the cut
by what a run opens and starts, and by the answers staying what they were.
"""
import builtins
import json
import os
import subprocess
import sys

from ao import lib as A, settings as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import builtins, json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
opened = {"import": [], "run": []}
phase = ["import"]
_open = builtins.open
def spy(file, *args, **kwargs):
    if isinstance(file, (str, bytes, os.PathLike)):
        opened[phase[0]].append(os.fsdecode(file))
    return _open(file, *args, **kwargs)
builtins.open = spy
from ao import cli, lib as A
phase[0] = "run"
sys.argv = ["ao", "-C", sys.argv[2], "board"]
try:
    cli.main()
except SystemExit:
    pass
builtins.open = _open
adapters = os.path.realpath(A.adapters_dir())
print(json.dumps({when: [os.path.basename(p) for p in paths if os.path.dirname(os.path.realpath(p)) == adapters]
                  for when, paths in opened.items()}))
"""


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True,
                   capture_output=True)


def test_importing_ao_reads_no_adapter_and_a_run_reads_each_adapter_once(project):
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH",)}
    run = subprocess.run([sys.executable, "-I", "-c", PROBE, ROOT, project["root"]], capture_output=True,
                         text=True, env=env, timeout=120)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]

    assert lines, run.stdout + run.stderr
    opened = json.loads(lines[-1])
    assert set(opened["import"]) <= {"profiles.json"}
    assert all(opened["run"].count(name) == 1 for name in opened["run"])


def test_the_binary_search_path_reads_the_adapters_when_searched_not_at_import(monkeypatch):
    shipped = tuple(A._BIN_DIRS)

    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": {"detect": {"install_dirs": ["~/.newcomer/bin"]}}})

    assert "~/.newcomer/bin" in A._BIN_DIRS and A._BIN_DIRS[4] == "~/.newcomer/bin"
    assert tuple(A._BIN_DIRS) == shipped[:4] + ("~/.newcomer/bin",) + shipped[-3:]


def test_a_status_read_asks_git_without_a_shell_and_reads_what_the_shell_read(project, tmp_path, monkeypatch):
    root = project["root"]
    _git(root, "commit", "-q", "--allow-empty", "-m", "second")
    with open(os.path.join(root, "staged.txt"), "w", encoding="utf-8") as fh:
        fh.write("staged\n")
    _git(root, "add", "staged.txt")
    open(os.path.join(root, "untracked é.txt"), "w", encoding="utf-8").close()
    empty, loose = tmp_path / "no-commits", tmp_path / "not-a-repository"
    (empty / "sub").mkdir(parents=True)
    _git(empty, "init", "-q")
    (empty / "new.txt").write_text("new\n", encoding="utf-8")
    loose.mkdir()
    places = [root, str(empty), str(loose), str(tmp_path / "missing")]
    shell = {place: (A.sh("git log --oneline -4", cwd=place), A.sh("git status --short", cwd=place))
             for place in places}
    toplevel = A.sh("git rev-parse --show-toplevel", cwd=str(empty / "sub"))
    started = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        started.append(bool(kwargs.get("shell")))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    for place in places:
        log, status = shell[place]
        state = A.git_state(place)
        assert state["log"] == log.split("\n"), place
        assert state["dirty"] == [line for line in status.split("\n") if line.strip()], place
    assert A._git_text(str(empty / "sub"), "rev-parse", "--show-toplevel") == toplevel != ""
    monkeypatch.chdir(empty / "sub")
    A.find_root()

    assert started and not any(started)
    assert shell[root][0].count("\n") == 1 and "staged.txt" in shell[root][1] and "new.txt" in shell[str(empty)][1]


def test_machine_settings_are_read_once_per_state_of_the_file_and_each_caller_gets_its_own(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"round_budget": 4}), encoding="utf-8")
    monkeypatch.setenv("AO_SETTINGS", str(path))
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and os.fsdecode(file) == str(path):
            opened.append(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    assert [S.resolve({}, "round_budget") for _ in range(5)] == [(4, "machine", None)] * 5
    assert len(opened) == 1

    mine = S.machine_settings()
    mine["round_budget"] = 9
    assert S.machine_settings() == {"round_budget": 4}

    S.write_machine({"round_budget": 3})
    assert S.resolve({}, "round_budget") == (3, "machine", None)

    path.write_text(json.dumps({"round_budget": 10}), encoding="utf-8")       # another size: read again
    assert S.resolve({}, "round_budget") == (10, "machine", None)

    other = tmp_path / "other.json"
    other.write_text(json.dumps({"round_budget": 2}), encoding="utf-8")
    monkeypatch.setenv("AO_SETTINGS", str(other))
    assert S.resolve({}, "round_budget") == (2, "machine", None)
