"""Partial walks hid fuller link targets; empty opt-in reads dirtied setup plans (LEADS-ADAPTERS)."""
import json
import os
from pathlib import Path

import pytest

from ao import cli, lib as A, storage
from tests.test_harness_config import _seen_by_a_person
from tests.test_leads_adapters import _can_link, _git, _ignored, _write
from tests.test_profiles import _init_args
from tests.test_review_tiers import _same_family, _write as _write_config


@pytest.mark.parametrize("name", ["hooks", "instructions"])
@pytest.mark.parametrize("change", ["added", "modified"])
def test_a_full_walk_through_a_link_is_not_stopped_by_its_partial_parent(project, tmp_path, name, change):
    _can_link(tmp_path)
    root = project["root"]
    _ignored(root, ".github/")
    os.makedirs(os.path.join(root, ".github"), exist_ok=True)
    os.symlink(".", os.path.join(root, ".github", name), target_is_directory=True)
    if change == "modified":
        _write(root, ".github/evil.json", "ordinary instructions\n")
    _seen_by_a_person(root)
    assert A.unseen_harness_config(root) == []

    _write(root, ".github/evil.json", "new instructions a harness reads through its link\n")

    expected = ".github/" + name + "/evil.json"
    paths = {path for path, _, _ in A.project_config_changes(root)}
    assert expected in paths and ".github/evil.json" not in paths
    assert expected in {path for path, _, _ in A.unseen_harness_config(root)}
    assert not any(("/" + name) * 3 + "/" in path for path in paths)


@pytest.mark.parametrize("kind", ["untracked", "ignored", "tracked", "hidden", "nested",
                                  "replaced-link", "replaced-folder"])
def test_a_link_at_a_declared_prefix_reads_the_declared_children(project, tmp_path, kind):
    _can_link(tmp_path)
    root = project["root"]
    alias = ".github/.github" if kind == "nested" else ".github"
    _ignored(root, ".github/" if kind == "nested" else ".github" if kind == "ignored" else "payload/")
    _write(root, "payload/hooks/evil.json", "ordinary instructions\n")
    _write(root, "payload/unrelated.txt", "not harness configuration\n")
    os.makedirs(os.path.dirname(os.path.join(root, alias)), exist_ok=True)
    if kind.startswith("replaced-"):
        _write(root, alias, "formerly an ordinary file\n")
        _git(root, "add", "--", alias)
        _git(root, "commit", "-q", "-m", "ordinary prefix file")
        _git(root, "update-index", "--assume-unchanged", alias)
        os.unlink(os.path.join(root, alias))
    if kind == "replaced-folder":
        _write(root, alias + "/hooks/evil.json", "ordinary instructions\n")
        _write(root, alias + "/unrelated.txt", "not harness configuration\n")
    else:
        os.symlink("../payload" if kind == "nested" else "payload", os.path.join(root, alias),
                   target_is_directory=True)
    if kind in ("tracked", "hidden"):
        _git(root, "add", "--", alias)
        _git(root, "commit", "-q", "-m", "configuration prefix")
        if kind == "hidden":
            _git(root, "update-index", "--assume-unchanged", alias)
    _seen_by_a_person(root)
    assert A.unseen_harness_config(root) == []

    changed = alias + "/hooks/evil.json" if kind == "replaced-folder" else "payload/hooks/evil.json"
    _write(root, changed, "changed instructions\n")

    expected = alias + "/hooks/evil.json"
    paths = {path for path, _, _ in A.project_config_changes(root)}
    assert expected in paths
    if kind != "replaced-folder":
        assert alias in paths
    assert alias + "/unrelated.txt" not in paths
    assert expected in {path for path, _, _ in A.unseen_harness_config(root)}


@pytest.mark.parametrize("setting", ["features.review", "review.same_family"])
def test_reading_an_absent_opt_in_leaves_no_lock(project, setting):
    root = project["root"]
    directory = Path(root) / ".ao/ledger"
    directory.mkdir(exist_ok=True)
    (directory / ".gitkeep").touch()
    before = set(directory.iterdir())

    assert A.recorded_opt_in(root, setting) is None

    assert set(directory.iterdir()) == before


def test_init_refused_by_its_probe_leaves_no_opt_in_lock(project, capsys):
    cfg = _write_config(project["root"], dict(_same_family(project, opted=False), review={"same_family": "labeled"}))
    directory = Path(project["root"]) / ".ao/ledger"
    directory.mkdir(exist_ok=True)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    assert cli.cmd_init(cfg, _init_args(profile=None)) == 1

    assert "reviewer probe" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_an_absent_opt_in_still_checks_for_removed_rows_without_creating_a_lock(project):
    root = project["root"]
    A.record_opt_in(root, "features.review", False, "Person")
    path = Path(A.opt_ins_path(root))
    path.unlink()
    lock = Path(str(path) + ".lock")
    lock.unlink()

    with pytest.raises(storage.LedgerCorruption, match="rows were removed"):
        A.recorded_opt_in(root, "features.review")

    assert not lock.exists()


def test_an_absent_opt_in_still_reads_its_seal_without_creating_a_lock(project):
    root = project["root"]
    path = A.opt_ins_path(root)
    seal = {"retired": 1, "digest": "retired-row-digest"}
    Path(path).parent.mkdir(exist_ok=True)
    Path(storage.seal_path(path)).write_text(json.dumps(seal), encoding="utf-8")
    storage._record_committed_length(path, 1, seal["digest"], sealed=seal)

    assert A.recorded_opt_in(root, "features.review") is None

    assert not Path(path + ".lock").exists()


def test_an_absent_opt_in_with_a_pending_seal_still_recovers_under_lock(project):
    root = project["root"]
    path = A.opt_ins_path(root)
    pending = Path(storage.seal_path(path) + ".pending")
    pending.parent.mkdir(exist_ok=True)
    pending.write_text(json.dumps({"retired": 1, "digest": "interrupted-seal"}), encoding="utf-8")

    assert A.recorded_opt_in(root, "features.review") is None

    assert not pending.exists() and Path(path + ".lock").exists()
