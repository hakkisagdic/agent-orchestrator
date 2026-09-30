"""A commit ao makes names the grant it was made under, and a range check holds each landed commit to it
(COMMIT-TRAILER).

`ao commit` ends the message with Ao- trailers read from the grant it held the index to, and
`ao commit-check --range` checks that each commit of a range has the tree they name: in the checkout
against its ledger too, in a clone with no .ao/ by tree alone, as CI runs it.
"""
import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.test_landed_tree import _granted_candidate


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _trailers(root, rev="HEAD"):
    return [line for line in _git(root, "log", "-1", "--format=%(trailers:only,unfold)", rev).splitlines() if line]


def _check(root, capsys, spec, *more):
    capsys.readouterr()
    code = cli.main(["-C", str(root), "commit-check", "--range", spec, *more])
    return code, capsys.readouterr().out


def _landed(project, monkeypatch, message="land a"):
    """The fixture's first commit, then one ao commit of a granted candidate: (root, base, grant)."""
    root = project["root"]
    base = _git(root, "rev-parse", "HEAD").strip()
    cfg = _granted_candidate(project, monkeypatch)
    grant = A.latest_authority_decision(root)
    assert cli.cmd_commit(cfg, SimpleNamespace(message=message, file=None)) == 0
    return root, base, grant


def test_ao_commit_ends_the_message_with_the_grant_it_checked(project, monkeypatch):
    root, _, grant = _landed(project, monkeypatch, "land a\n\nCo-Authored-By: someone <someone@example.com>\n")

    tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
    assert tree == grant["candidate"]["index_tree"]
    # One trailer block: the message's own trailer first, then the grant's, the review switch off here.
    assert _trailers(root) == ["Co-Authored-By: someone <someone@example.com>", f"Ao-Grant: {grant['token']}",
                               f"Ao-Tree: {tree}", "Ao-Verification: V-1", "Ao-Review: off"]
    assert _git(root, "log", "-1", "--format=%s").strip() == "land a"


def test_a_message_that_ends_in_prose_gets_the_trailers_as_a_paragraph_of_their_own():
    message = "fix: a thing\n\nWhy: it broke\nand this line is prose, so the paragraph is no trailer block\n\n\n"

    text = A.with_trailers(message, [("Ao-Grant", "C-1"), ("Ao-Tree", "t" * 40)])

    assert text.endswith("so the paragraph is no trailer block\n\nAo-Grant: C-1\nAo-Tree: " + "t" * 40 + "\n")


@pytest.mark.parametrize("message", ["land a\n\nAo-Grant: C-1\n", "land a\n\nCo-Authored-By: x <x@x>\nao-tree: abc\n"])
def test_a_message_that_already_carries_ao_trailers_is_refused_and_nothing_is_committed(
        project, monkeypatch, capsys, message):
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    head = _git(root, "rev-parse", "HEAD").strip()

    assert cli.cmd_commit(cfg, SimpleNamespace(message=message, file=None)) == 2

    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert "ao commit writes the Ao- trailers itself" in capsys.readouterr().out


def test_the_range_check_passes_on_a_commit_ao_made_and_finds_its_grant_on_record(project, monkeypatch, capsys):
    root, base, grant = _landed(project, monkeypatch)

    code, out = _check(root, capsys, f"{base}..HEAD")

    assert code == 0, out
    assert f"{grant['token']} · tree" in out and "on record" in out and "TRAILERS MATCH  1 commit(s)" in out


def test_a_commit_amended_after_its_grant_does_not_match(project, monkeypatch, capsys):
    root, base, _ = _landed(project, monkeypatch)
    with open(os.path.join(root, "src", "a.py"), "a", encoding="utf-8") as fh:
        fh.write("unreviewed = True\n")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "--amend", "--no-edit")

    code, out = _check(root, capsys, f"{base}..HEAD")

    assert code == 1
    assert "NO MATCH" in out and "it changed after the grant" in out


def test_history_before_the_first_trailer_is_left_out_only_when_asked(project, monkeypatch, capsys):
    root, _, _ = _landed(project, monkeypatch)

    code, out = _check(root, capsys, "HEAD")
    assert code == 1 and "carries no Ao- trailers, which ao commit writes" in out       # the fixture's init

    code, out = _check(root, capsys, "HEAD", "--since-first-trailer")
    assert code == 0, out
    assert "1 commit(s) not checked: older than" in out and "TRAILERS MATCH  1 commit(s)" in out


def test_a_commit_without_trailers_after_the_first_one_fails(project, monkeypatch, capsys):
    root, base, _ = _landed(project, monkeypatch)
    _git(root, "commit", "-q", "--allow-empty", "-m", "made by hand")

    code, out = _check(root, capsys, f"{base}..HEAD", "--since-first-trailer")

    assert code == 1
    assert "TRAILERS DO NOT MATCH  1 of 2 commit(s)" in out


def test_a_clone_with_no_ao_state_checks_by_tree_alone(project, monkeypatch, capsys, tmp_path):
    root, _, _ = _landed(project, monkeypatch)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", root, str(clone)], check=True, capture_output=True)
    assert not (clone / ".ao").exists()

    code, out = _check(clone, capsys, "HEAD", "--since-first-trailer")

    assert code == 0, out
    assert "checked by tree alone: this checkout keeps no authority ledger" in out
    assert "on record" not in out


def test_a_merge_is_skipped_and_a_bad_range_is_refused(project, monkeypatch, capsys):
    root, base, _ = _landed(project, monkeypatch)
    landed = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "checkout", "-q", "-b", "side", base)
    _git(root, "commit", "-q", "--allow-empty", "-m", "side")
    _git(root, "checkout", "-q", "-")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    audit = A.trailer_audit(root, f"{landed}..HEAD")
    merge = [commit for commit in audit["commits"] if commit["merge"]]
    assert len(merge) == 1 and merge[0]["problems"] == []

    code, out = _check(root, capsys, "HEAD~1...HEAD")
    assert code == 2 and "is not a range ao reads" in out
