import errno
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage
from tests.test_capability_matrix import _strict_config

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
PROFILE = dict(profile="claude-kiro", implementer=None, model=None, effort=None, reviewer_model=None)

CHILD = r'''
import os, sys, time
root, marker, step, home = sys.argv[1:5]
os.environ["HOME"] = home
from types import SimpleNamespace
from ao import cli, lib as A, storage
A.HOME = home
original = storage.replace_file_durably
def checkpoint(name):
    if name == step:
        open(marker, "w", encoding="utf-8").write(name)
        while True:
            time.sleep(1)
def replace(path, data, **kwargs):
    return original(path, data, _checkpoint=checkpoint)
storage.replace_file_durably = replace
'''

WRITERS = {
    "features": "from ao import features as F\nF.set_switch(root, 'review', False)\n",
    "profile": "cli._apply_profile(root, SimpleNamespace(" + ", ".join(f"{k}={v!r}" for k, v in PROFILE.items()) + "))\n",
    "init": (
        "cli._reviewer_probe = lambda cfg, timeout=None: {'configured': True, 'ok': True, "
        "'route': 'fixture', 'binary': sys.executable, 'version': 'fixture', "
        "'reason': 'exact nonce echoed', 'kind': 'success'}\n"
        "cli.cmd_init({'root': root}, SimpleNamespace(name=None, agent=None, no_mcp=True, rules=False, "
        "watchdog=False, " + ", ".join(f"{k}={v!r}" for k, v in PROFILE.items()) + "))\n"
    ),
}


def _config(root):
    return os.path.join(root, ".ao", "config.json")


def _opt_in(project):
    """A strict project config on disk, one role block short so every writer has work to do."""
    document = {key: value for key, value in _strict_config(project).items() if key not in ("root", "architect")}
    raw = json.dumps(document, indent=2).encode("utf-8")
    open(_config(project["root"]), "wb").write(raw)
    return document, raw


def _wait_for(path, process, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return
        if process.poll() is not None:
            raise AssertionError(f"writer exited before the checkpoint: {process.communicate()}")
        time.sleep(0.05)
    raise AssertionError("timed out waiting for the writer's checkpoint")


@pytest.mark.parametrize("step", ["temporary-written", "temporary-fsynced", "replaced"])
@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_config_writer_killed_mid_write_leaves_a_whole_config_that_keeps_the_opt_in(
    project, tmp_path, writer, step
):
    root = project["root"]
    before, raw = _opt_in(project)
    marker = tmp_path / "checkpoint"
    home = tmp_path / "writer-home"
    home.mkdir()
    env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD + WRITERS[writer], root, str(marker), step, str(home)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(marker, child)
    finally:
        child.kill()
        child.communicate(timeout=10)

    document = A.project_config_document(root)
    assert document["problem"] is None
    assert document["config"]["capability_matrix"] == before["capability_matrix"]
    if step == "replaced":
        assert document["raw"] != raw
    else:
        assert document["raw"] == raw


def test_a_failed_replacement_leaves_the_old_bytes_and_no_temporary_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b'{"capability_matrix": {"version": 1}}')

    def disk_gone(fd):
        raise OSError(errno.EIO, "disk gone")

    with pytest.raises(OSError, match="disk gone"):
        storage.replace_file_durably(str(path), b'{"features": {}}', _fsync=disk_gone)

    assert path.read_bytes() == b'{"capability_matrix": {"version": 1}}'
    assert os.listdir(tmp_path) == ["config.json"]


def test_a_config_ao_cannot_read_is_refused_not_rebuilt(project, capsys):
    root = project["root"]
    open(_config(root), "w", encoding="utf-8").write("")

    assert cli.cmd_features(project, SimpleNamespace(action="off", key="review")) == 1
    assert "not changed" in capsys.readouterr().out
    with pytest.raises(ValueError, match="config.json"):
        cli._apply_profile(root, SimpleNamespace(**PROFILE))
    assert open(_config(root), encoding="utf-8").read() == ""

    assert cli.cmd_commit_check(A.load_config(root), SimpleNamespace()) == 1
