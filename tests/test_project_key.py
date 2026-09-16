import json
import os
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W


def _project(parent, name="api"):
    root = parent / name
    (root / ".ao").mkdir(parents=True)
    return str(root)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(A, "HOME", str(home))
    monkeypatch.setattr(W, "STATE_DIR", str(home / ".ao"))
    return home


def test_an_installed_project_keeps_the_name_its_files_already_have(tmp_path, home):
    root = _project(tmp_path)

    assert A.project_key(root) == "api"
    assert A.project_key(root + "/") == "api"
    assert A.heartbeat_path(root).endswith("heartbeat-api")
    mark = json.load(open(os.path.join(root, ".ao", A.PROJECT_KEY_FILE), encoding="utf-8"))
    assert mark == {"key": "api", "root": os.path.realpath(root)}


def test_two_projects_with_one_name_keep_their_files_apart(tmp_path, home):
    first = _project(tmp_path / "work")
    second = _project(tmp_path / "personal")
    one, two = A.project_key(first), A.project_key(second)
    assert one == "api" and two.startswith("api-") and len(two) == len("api-") + 8

    # a push window opened for one is not open for the other
    push = lambda root, action: cli.cmd_push({"root": root}, SimpleNamespace(action=action, minutes=30))
    assert push(first, "allow") == 0
    assert push(first, "check") == 0
    assert push(second, "check") == 1

    # watchdog state, heartbeats, locks, helper and reviewer records, cycle logs
    W.save_state(first, {"attempts": 7})
    assert W.load_state(first)["attempts"] == 7 and W.load_state(second).get("attempts") != 7
    A.heartbeat(first)
    assert A.heartbeat_age(first) is not None and A.heartbeat_age(second) is None
    assert A.acquire_architect(first, os.getpid(), "test")
    assert A.architect_lock_holder(second) is None
    for path in (W.state_path, W.cycles_path, A.architect_lock_path, A.helpers_path,
                 A.reviewer_state_path, A.heartbeat_path):
        assert path(first) != path(second), path.__name__
    then = time.time() - 20 * 60
    os.utime(A.heartbeat_path(first), (then, then))
    assert set(A.stale_siblings(second)) == {one}
    assert A.stale_siblings(first) == {}


def test_names_differing_only_in_case_are_one_name(tmp_path, home):
    assert A.project_key(_project(tmp_path / "a", "Api")) == "Api"
    assert A.project_key(_project(tmp_path / "b", "api")).startswith("api-")


def test_a_lost_registry_gives_each_project_its_own_name_back(tmp_path, home):
    first = _project(tmp_path / "work")
    second = _project(tmp_path / "personal")
    names = A.project_key(first), A.project_key(second)
    os.replace(A.project_registry_path(), str(tmp_path / "set-aside.json"))

    assert (A.project_key(second), A.project_key(first)) == (names[1], names[0])


def test_a_copied_ao_directory_does_not_claim_the_original_name(tmp_path, home):
    original = _project(tmp_path / "work")
    assert A.project_key(original) == "api"
    copy = _project(tmp_path / "copy")
    mark = open(os.path.join(original, ".ao", A.PROJECT_KEY_FILE), encoding="utf-8").read()
    open(os.path.join(copy, ".ao", A.PROJECT_KEY_FILE), "w", encoding="utf-8").write(mark)

    assert A.project_key(copy).startswith("api-")


def test_a_name_whose_project_is_gone_is_taken_by_the_next(tmp_path, home):
    retired = _project(tmp_path / "old")
    assert A.project_key(retired) == "api"
    os.rename(os.path.join(retired, ".ao"), os.path.join(retired, "ao-was-here"))

    assert A.project_key(_project(tmp_path / "new")) == "api"


def test_a_directory_that_is_not_a_project_is_named_but_not_recorded(tmp_path, home):
    plain = tmp_path / "scratch"
    plain.mkdir()

    assert A.project_key(str(plain)) == "scratch"
    assert A.project_registry() == {}


def test_ao_projects_names_the_projects_that_would_have_collided(tmp_path, home, capsys, monkeypatch):
    monkeypatch.setattr(A, "all_workspaces", lambda: [])
    first = _project(tmp_path / "work")
    second = _project(tmp_path / "personal")
    A.project_key(first), A.project_key(second)

    cli.cmd_projects({}, SimpleNamespace())

    out = capsys.readouterr().out
    assert "2 projects are named api" in out
    assert os.path.realpath(first) in out and os.path.realpath(second) in out
