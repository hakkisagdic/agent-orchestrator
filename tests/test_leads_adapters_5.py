"""Partial walks hid fuller link targets; empty opt-in reads dirtied setup plans (LEADS-ADAPTERS)."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from ao import cli, features as F, lib as A, storage
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


def test_an_absent_opt_in_ledger_with_a_recorded_seal_is_refused_without_creating_a_lock(project):
    root = project["root"]
    path = A.opt_ins_path(root)
    seal = {"retired": 1, "digest": "retired-row-digest"}
    Path(path).parent.mkdir(exist_ok=True)
    Path(storage.seal_path(path)).write_text(json.dumps(seal), encoding="utf-8")
    storage._record_committed_length(path, 1, seal["digest"], sealed=seal)

    with pytest.raises(storage.LedgerCorruption, match="rows were removed"):
        A.recorded_opt_in(root, "features.review")

    assert not Path(path + ".lock").exists()


def _watch_opt_in_settlement(monkeypatch, path):
    """Observe settlement inside the real ledger lock, not merely beside an existing lock file."""
    original_lock, original_settle = storage._exclusive_lock, storage._settle_seal
    held, settled = [], []

    @contextmanager
    def locked(target, *args, **kwargs):
        with original_lock(target, *args, **kwargs):
            if target == path + ".lock":
                held.append(target)
                try:
                    yield
                finally:
                    held.pop()
            else:
                yield

    def settle(target, *args, **kwargs):
        if target == path:
            assert held == [path + ".lock"]
            settled.append(target)
        return original_settle(target, *args, **kwargs)

    monkeypatch.setattr(storage, "_exclusive_lock", locked)
    monkeypatch.setattr(storage, "_settle_seal", settle)
    return settled


@pytest.mark.parametrize("ledger_exists", [False, True])
def test_an_absent_opt_in_with_an_in_tree_intent_is_refused_under_lock(project, monkeypatch, ledger_exists):
    """An in-tree intent is not ao's: refuse it under the ledger lock, unread and unchanged (LEDGER-HISTORY)."""
    root = project["root"]
    path = A.opt_ins_path(root)
    if ledger_exists:
        A.record_opt_in(root, "features.probe", True, "Person")
    pending = Path(storage.seal_path(path) + ".pending")
    pending.parent.mkdir(exist_ok=True)
    pending.write_text(json.dumps({"retired": 1, "digest": "interrupted-seal"}), encoding="utf-8")
    before = pending.read_bytes()
    checkpoint = Path(storage.checkpoint_path())
    recorded = checkpoint.read_bytes() if checkpoint.exists() else None
    settled = _watch_opt_in_settlement(monkeypatch, path)
    original_bytes = storage._tree_bytes

    def unread(folder, name):
        assert name != Path(path).name, "the opt-in ledger was read despite the in-tree intent"
        return original_bytes(folder, name)

    monkeypatch.setattr(storage, "_tree_bytes", unread)
    with pytest.raises(storage.LedgerCorruption, match="did not begin") as refused:
        A.recorded_opt_in(root, "features.review")

    assert str(pending) in str(refused.value)
    assert pending.read_bytes() == before and settled == [path]
    assert (checkpoint.read_bytes() if checkpoint.exists() else None) == recorded


@pytest.mark.parametrize("stop", ["intent-recorded", "ledger-rewritten"])
def test_an_absent_setting_settles_a_checkpoint_intent_under_lock(project, monkeypatch, stop):
    root = project["root"]
    for value in (True, False, True):
        A.record_opt_in(root, "features.probe", value, "Person")
    path = A.opt_ins_path(root)

    def interrupted(step):
        if step == stop:
            raise OSError("interrupted seal")

    with pytest.raises(OSError, match="interrupted seal"):
        storage.seal_chained_jsonl(path, A.OPT_IN_CHAIN, keep=1, _checkpoint=interrupted)
    assert storage._checkpoint_entry(path)["intent"]["retired"] == 2
    assert not Path(storage.seal_path(path) + ".pending").exists()
    settled = _watch_opt_in_settlement(monkeypatch, path)

    assert A.recorded_opt_in(root, "features.review") is None

    assert settled == [path]
    mark = storage._checkpoint_entry(path)
    assert "intent" not in mark and mark["count"] == 3
    if stop == "ledger-rewritten":
        assert mark["sealed"]["retired"] == 2
    else:
        assert not Path(storage.seal_path(path)).exists()
    assert len(storage.read_chained_history(path, A.OPT_IN_CHAIN)) == 3


def test_an_unreadable_opt_in_keeps_review_required_and_same_family_refused_and_says_so(project):
    cfg = _write_config(project["root"], dict(_same_family(project), features={"review": False}))
    A.record_opt_in(cfg["root"], "features.review", False, "Person")
    assert not F.enabled(cfg, "review") and cli._same_family_opt_in(cfg) is not None
    pending = Path(storage.seal_path(A.opt_ins_path(cfg["root"])) + ".pending")
    pending.write_text("{}", encoding="utf-8")

    assert not F.recorded_off(cfg, "review") and F.enabled(cfg, "review")
    assert cli._same_family_opt_in(cfg) is None
    assert cli._reviewer_ineligible(cfg, cfg["reviewer"])
    problems = dict(cli.doctor_problems(cfg))
    assert "so it stays on" in problems["unrecorded-switch:review"]
    assert "is still refused" in problems["review-opt-in"]
    assert pending.read_text(encoding="utf-8") == "{}"


def test_init_refuses_and_names_an_unreadable_opt_in(project, monkeypatch, capsys):
    cfg = _write_config(project["root"], dict(_same_family(project, opted=False), review={"same_family": "labeled"}))
    path = A.opt_ins_path(cfg["root"])
    pending = Path(storage.seal_path(path) + ".pending")
    pending.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg: {"ok": True})

    assert cli.cmd_init(cfg, _init_args(profile=None, review_tier="same-family", by="Person")) == 1

    out = capsys.readouterr().out
    assert "init stopped" in out and "not in force" in out and str(pending) in out
    assert pending.read_text(encoding="utf-8") == "{}" and not Path(path).exists()
