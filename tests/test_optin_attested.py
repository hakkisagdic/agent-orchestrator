"""A person's opt-in counts only where this machine's checkpoint store records ao appending it (OPTIN-ATTESTED).

An opt-in weakens a guarantee - review off, a same-family reviewer - and the implementer can write any file in the
tree. The first row of a chain needs no digest at all, and a row after ao's last append needs only the digest of the
row before it: an implementer that wrote `"features": {"review": false}` and one such row into
`.ao/ledger/opt-ins.jsonl` was granted on verification alone. The landing review of WIN-JOB found it; a probe on
main confirmed it for `review`.
"""
import json
import os

import pytest

from ao import cli, features as F, lib as A
from ao.storage import CHAIN_PREVIOUS_FIELD, LedgerCorruption, chained_row_digest

LEDGER = os.path.join(".ao", "ledger", "opt-ins.jsonl")
OFF = {"at": 1, "setting": "features.review", "value": False, "by": "Ada", "user": "ada", "interactive": True}


def _forge(root, row, previous=None):
    """A row written into the ledger by hand, chained to `previous` as an append would chain it."""
    path = os.path.join(root, LEDGER)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row, **{CHAIN_PREVIOUS_FIELD: previous})) + "\n")


def _rows(root):
    with open(os.path.join(root, LEDGER), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _review_off(project):
    """The project's config with review switched off in .ao/config.json alone, as the implementer can write it."""
    root = project["root"]
    document = A.project_config_document(root)["config"]
    document["features"] = dict(document.get("features") or {}, review=False)
    A.write_project_config(root, json.dumps(document, indent=2))
    return dict(A.load_config(root), root=root)


def test_a_first_row_written_by_hand_turns_no_switch_off(project):
    root = project["root"]
    _forge(root, OFF)

    assert A.recorded_opt_in(root, "features.review") is None
    assert F.enabled(_review_off(project), "review") is True
    assert F.unrecorded(_review_off(project)) == ["review"]


def test_a_row_written_by_hand_after_aos_own_is_nobodys_and_ao_appends_nothing_after_it(project):
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    _forge(root, OFF, previous=chained_row_digest(_rows(root)[-1], A.OPT_IN_CHAIN))

    assert A.recorded_opt_in(root, "features.review") is None
    assert A.recorded_opt_in(root, "features.inventory_review")["by"] == "Ada"
    assert F.enabled(_review_off(project), "review") is True
    # Appended after it, ao's record would vouch for the row it never wrote.
    with pytest.raises(LedgerCorruption, match="does not record"):
        A.record_opt_in(root, "features.review", False, "Ada")
    assert len(_rows(root)) == 2


def test_an_opt_in_ao_appends_turns_the_switch_off(project):
    root = project["root"]
    A.record_opt_in(root, "features.review", False, "Ada")

    assert A.recorded_opt_in(root, "features.review")["by"] == "Ada"
    assert F.enabled(_review_off(project), "review") is False
    assert F.unrecorded(_review_off(project)) == []


def test_a_same_family_opt_in_written_by_hand_is_no_persons_record(project):
    root = project["root"]
    _forge(root, dict(OFF, setting="review.same_family", value=True))

    assert A.recorded_opt_in(root, "review.same_family") is None


def test_doctor_names_the_rows_this_machine_did_not_record(project):
    root = project["root"]
    _forge(root, OFF)

    found = dict(cli.doctor_problems(_review_off(project)))

    assert "1 row(s)" in found["opt-ins-unattested"] and "turn nothing off" in found["opt-ins-unattested"]
    assert "unrecorded-switch:review" in found


def test_a_checkpoint_store_inside_the_project_attests_nothing_and_takes_no_opt_in(project, monkeypatch):
    """The review of the first round: the implementer can point AO_LEDGER_CHECKPOINTS at a file in the tree and write
    a forged row and a count to match it."""
    root = project["root"]
    store = os.path.join(root, ".ao", "checkpoints.json")
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", store)
    _forge(root, OFF)
    identity = os.path.join(os.path.realpath(root), LEDGER)
    with open(store, "w", encoding="utf-8") as fh:
        json.dump({identity: {"count": 1, "digest": chained_row_digest(_rows(root)[0], A.OPT_IN_CHAIN)}}, fh)

    assert A.recorded_opt_in(root, "features.review") is None
    assert F.enabled(_review_off(project), "review") is True
    with pytest.raises(LedgerCorruption, match="inside the project"):
        A.record_opt_in(root, "features.review", False, "Ada")


def _store_with_a_forged_count(root, store):
    """A forged row, and a store at `store` that records a count for it."""
    _forge(root, OFF)
    identity = os.path.join(os.path.realpath(root), LEDGER)
    os.makedirs(os.path.dirname(store), exist_ok=True)
    with open(store, "w", encoding="utf-8") as fh:
        json.dump({identity: {"count": 1, "digest": chained_row_digest(_rows(root)[0], A.OPT_IN_CHAIN)}}, fh)


def test_a_store_in_the_tree_named_with_other_letter_case_attests_nothing(project, monkeypatch):
    """The review of the second round: on a file system that folds case, `/A/Proj/x` is `/a/proj/x`, and a path
    compared as text took it for a store outside the tree."""
    root = project["root"]
    other = root.swapcase()
    if os.path.normcase(other) == os.path.normcase(root) or not os.path.isdir(other):
        pytest.skip("this file system tells letter case apart")
    _store_with_a_forged_count(root, os.path.join(root, ".ao", "checkpoints.json"))
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", os.path.join(other, ".ao", "checkpoints.json"))

    assert A.recorded_opt_in(root, "features.review") is None
    with pytest.raises(LedgerCorruption, match="inside the project"):
        A.record_opt_in(root, "features.review", False, "Ada")


def test_a_store_reached_through_a_link_from_outside_the_tree_attests_nothing(project, tmp_path, monkeypatch):
    root = project["root"]
    _store_with_a_forged_count(root, os.path.join(root, ".ao", "checkpoints.json"))
    link = tmp_path / "elsewhere"
    try:
        os.symlink(os.path.join(root, ".ao"), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", str(link / "checkpoints.json"))

    assert A.recorded_opt_in(root, "features.review") is None


def test_a_store_in_the_tree_is_not_read_at_all(project, monkeypatch):
    """The review of the second round: the store was read - its intent, its seal - before ao asked where it lay."""
    root = project["root"]
    store = os.path.join(root, ".ao", "checkpoints.json")
    _forge(root, OFF)
    identity = os.path.join(os.path.realpath(root), LEDGER)
    with open(store, "w", encoding="utf-8") as fh:              # an intent a recovery would act on, and refuse
        json.dump({identity: {"count": 1, "intent": "a seal under way"}}, fh)
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", store)

    assert A.recorded_opt_in(root, "features.review") is None


def test_rows_after_aos_own_are_named_for_removal_and_aos_own_are_kept(project):
    """The review of the second round: moving the whole file aside lost the opt-ins ao had recorded."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    _forge(root, OFF, previous=chained_row_digest(_rows(root)[-1], A.OPT_IN_CHAIN))

    with pytest.raises(LedgerCorruption) as refused:
        A.record_opt_in(root, "features.review", False, "Ada")
    assert "the first 1 row(s) are ao's own and stay" in str(refused.value)
    assert "the last 1 line(s)" in str(refused.value)


def test_a_store_named_through_a_link_in_the_tree_is_the_trees_wherever_the_link_points(project, tmp_path,
                                                                                         monkeypatch):
    """The review of the third round: resolved first, a path through a link in the tree that pointed outside passed,
    and the link could point into the tree by the time the store was read."""
    root = project["root"]
    outside = tmp_path / "outside"
    outside.mkdir()
    _store_with_a_forged_count(root, str(outside / "checkpoints.json"))
    try:
        os.symlink(str(outside), os.path.join(root, "out-link"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", os.path.join(root, "out-link", "checkpoints.json"))

    assert A.recorded_opt_in(root, "features.review") is None
    with pytest.raises(LedgerCorruption, match="inside the project"):
        A.record_opt_in(root, "features.review", False, "Ada")


def test_a_broken_row_after_aos_own_leaves_aos_own_readable_and_doctor_counts_it(project):
    """The review of the third round: the whole chain was checked before the rows past ao's record were cut off, so a
    row of rubbish appended by hand made ao's own opt-ins unreadable."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    with open(os.path.join(root, LEDGER), "a", encoding="utf-8") as fh:
        fh.write("{ this is no row\n")

    assert A.recorded_opt_in(root, "features.inventory_review")["by"] == "Ada"
    found = dict(cli.doctor_problems(_review_off(project)))
    said = found["opt-ins-unattested"]
    assert "1 row(s)" in said and "the first 1 row(s) are ao's own" in said


def test_doctor_says_why_an_opt_in_ledger_cannot_be_read(project):
    """The review of the third round: doctor swallowed the error and said nothing."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    rows = _rows(root)
    rows[0]["by"] = "Someone else"                              # ao's own row, changed by hand
    with open(os.path.join(root, LEDGER), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in rows))

    found = dict(cli.doctor_problems(_review_off(project)))
    assert "cannot be read" in found["opt-ins-unreadable"]


def test_a_store_named_with_dot_dot_is_the_trees(project, tmp_path, monkeypatch):
    """The review of the fourth round: `outside/link/../x` is the link target's parent to the file system and
    `outside/x` to a path read as text, so either reading could hide a store in the tree."""
    root = project["root"]
    _store_with_a_forged_count(root, os.path.join(root, ".ao", "checkpoints.json"))
    (tmp_path / "outside").mkdir()
    try:
        os.symlink(os.path.join(root, ".ao", "inner"), tmp_path / "outside" / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")
    os.makedirs(os.path.join(root, ".ao", "inner"))
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", str(tmp_path / "outside" / "link" / ".." / "checkpoints.json"))

    assert A.recorded_opt_in(root, "features.review") is None
    with pytest.raises(LedgerCorruption, match="inside the project"):
        A.record_opt_in(root, "features.review", False, "Ada")


def test_doctor_says_nothing_of_an_opt_in_ledger_that_is_not_there(project, monkeypatch):
    """The review of the fourth round: an empty answer came back as a list where a count was asked for."""
    root = project["root"]
    assert "opt-ins-unreadable" not in dict(cli.doctor_problems(_review_off(project)))
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", os.path.join(root, ".ao", "checkpoints.json"))
    assert "opt-ins-unreadable" not in dict(cli.doctor_problems(_review_off(project)))


def test_a_blank_line_before_aos_own_row_changes_nothing_a_read_or_an_append_counts(project):
    """The review of the fourth round: an append counted parsed rows and a read counted raw lines, so a blank line
    moved which rows a read took as ao's."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    path = os.path.join(root, LEDGER)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n" + text)

    assert A.recorded_opt_in(root, "features.inventory_review")["by"] == "Ada"
    assert "opt-ins-unattested" not in dict(cli.doctor_problems(_review_off(project)))
    _forge(root, OFF, previous=chained_row_digest(_rows_skipping_blanks(root)[-1], A.OPT_IN_CHAIN))
    with pytest.raises(LedgerCorruption) as refused:
        A.record_opt_in(root, "features.review", False, "Ada")
    assert "1 row(s)" in str(refused.value) and "the first 1 row(s) are ao's own" in str(refused.value)


def _rows_skipping_blanks(root):
    with open(os.path.join(root, LEDGER), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_a_row_put_after_a_carriage_return_on_aos_own_line_is_no_row_of_aos(project):
    """The review of the fifth round: a count split at `\\n` alone saw one line where the row reader saw two records,
    so a row put after `\\r` on ao's own line read as ao's."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    path = os.path.join(root, LEDGER)
    with open(path, "rb") as fh:
        own = fh.read()
    forged = dict(OFF, **{CHAIN_PREVIOUS_FIELD: chained_row_digest(_rows(root)[-1], A.OPT_IN_CHAIN)})
    with open(path, "wb") as fh:
        fh.write(own.rstrip(b"\n") + b"\r" + json.dumps(forged).encode() + b"\n")

    assert A.recorded_opt_in(root, "features.review") is None
    assert F.enabled(_review_off(project), "review") is True
    with pytest.raises(LedgerCorruption, match="1 row"):
        A.record_opt_in(root, "features.review", False, "Ada")


def test_a_line_of_blanks_after_aos_own_rows_is_counted_as_the_row_reader_counts_it(project):
    """The review of the fifth round: a line of blanks was no line to the count and a malformed record to the reader."""
    root = project["root"]
    A.record_opt_in(root, "features.inventory_review", True, "Ada")
    with open(os.path.join(root, LEDGER), "a", encoding="utf-8") as fh:
        fh.write("   \n")

    assert A.recorded_opt_in(root, "features.inventory_review")["by"] == "Ada"
    assert "1 row(s)" in dict(cli.doctor_problems(_review_off(project)))["opt-ins-unattested"]
