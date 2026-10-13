"""`ao commit` writes the commit itself: the granted tree on the granted commit, and no hook runs
(LEADS-GATE-COMMIT).

`git commit` read the index again once its hooks returned, so a path another process staged after the check - or
one a hook staged into the index git handed it - landed inside the granted commit, the branch could be switched
under it, and a hook kept in the tree ran as the person committing.
"""
import os
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_trailer import _git
from tests.test_landed_tree import _granted_candidate

# ---- 9d600dfe4147618d: ao commit lands the granted tree on the granted commit, and runs no hook ----

def _after_the_check(monkeypatch, act):
    """Run `act` once ao commit's check has passed: what a process beside the commit does in that instant."""
    real = getattr(cli.cmd_commit_check, "unwrapped", cli.cmd_commit_check)

    def checked(cfg, args):
        code = real(cfg, args)
        if code == 0:
            act()
        return code
    checked.unwrapped = real
    monkeypatch.setattr(cli, "cmd_commit_check", checked)


def test_a_path_staged_after_the_check_stays_staged_and_the_granted_tree_lands(project, monkeypatch):
    """`git commit` read the index again once its hooks returned: a path staged in between landed inside the granted
    commit, as did one staged into the index Git handed a hook, or written into a copy of it (LEADS-GATE)."""
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    granted = A.latest_authority_decision(root)["candidate"]["index_tree"]

    def stage():
        with open(os.path.join(root, "src", "unreviewed.py"), "w", encoding="utf-8") as fh:
            fh.write("unreviewed = True\n")
        _git(root, "add", "src/unreviewed.py")
    _after_the_check(monkeypatch, stage)

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 0

    assert _git(root, "rev-parse", "HEAD^{tree}").strip() == granted
    assert A.landed_commit_problem(root) is None
    assert _git(root, "diff", "--cached", "--name-only").split() == ["src/unreviewed.py"]


def test_ao_commit_runs_no_hook(project, monkeypatch):
    """A repository that keeps its hooks in its tree keeps files the implementer edits: `git commit` ran them as the
    person committing, and a hook staged into the commit it was checking (LEADS-GATE)."""
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    ran = os.path.join(root, "hook-ran")
    for name in ("pre-commit", "commit-msg", "post-commit", "reference-transaction"):
        path = os.path.join(root, ".git", "hooks", name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"#!/bin/sh\necho {name} >> '{ran}'\n")
        os.chmod(path, 0o755)

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 0

    assert not os.path.exists(ran)
    assert A.landed_commit_problem(root) is None


def test_a_branch_moved_or_switched_while_ao_commit_ran_takes_no_commit(project, monkeypatch):
    """The branch moves from the granted commit only: one that moved meanwhile is left where it is and nothing lands;
    switched to another branch at the granted commit, the granted tree lands there (LEADS-GATE)."""
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    _after_the_check(monkeypatch, lambda: _git(root, "commit", "-q", "--allow-empty", "-m", "moved meanwhile"))

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 1

    moved = _git(root, "log", "-1", "--format=%s").strip()
    assert moved == "moved meanwhile"
    _git(root, "reset", "-q", "--soft", "HEAD~1")
    granted = A.latest_authority_decision(root)["candidate"]["index_tree"]
    _after_the_check(monkeypatch, lambda: _git(root, "switch", "-q", "-c", "side"))

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 0

    assert _git(root, "rev-parse", "side^{tree}").strip() == granted


def test_ao_commit_lands_no_merge_and_a_lane_lands_squashed(project, monkeypatch, capsys):
    """A grant binds one parent, the commit its candidate was built on: a merge in progress, or a cherry-pick, is sent
    back to git, and a lane squashed lands as any commit (LEADS-GATE)."""
    root = project["root"]
    trunk = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(root, "switch", "-q", "-c", "lane")
    with open(os.path.join(root, "side.txt"), "w", encoding="utf-8") as fh:
        fh.write("side\n")
    _git(root, "add", "side.txt")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "lane")
    _git(root, "switch", "-q", trunk)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "--no-commit", "lane")
    cfg = _granted_candidate(project, monkeypatch)
    head = _git(root, "rev-parse", "HEAD").strip()
    capsys.readouterr()

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land the lane", file=None)) == 1
    assert "a merge is in progress" in capsys.readouterr().out and _git(root, "rev-parse", "HEAD").strip() == head

    _git(root, "merge", "--abort")
    _git(root, "merge", "-q", "--squash", "lane")
    cfg = _granted_candidate(project, monkeypatch)
    cherry = os.path.join(root, ".git", "CHERRY_PICK_HEAD")
    with open(cherry, "w", encoding="ascii") as fh:
        fh.write(_git(root, "rev-parse", "lane").strip() + "\n")

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land the lane", file=None)) == 1

    os.remove(cherry)
    assert cli.cmd_commit(cfg, SimpleNamespace(message="land the lane", file=None)) == 0

    assert len(_git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 2
    assert A.trailer_audit(root, f"{head}..HEAD")["commits"][0]["problems"] == []


def test_a_grant_that_names_no_merge_lands_none(project, monkeypatch, capsys):
    """MERGE_HEAD was read when the commit was written, while the grant bound HEAD and the tree alone: a merge started
    after an ordinary grant, the granted tree staged again, landed as a merge of anything (LEADS-GATE)."""
    root = project["root"]
    trunk = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(root, "switch", "-q", "-c", "unreviewed")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "anything")
    unreviewed = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "switch", "-q", trunk)
    cfg = _granted_candidate(project, monkeypatch)
    head = _git(root, "rev-parse", "HEAD").strip()
    with open(os.path.join(root, _git(root, "rev-parse", "--git-path", "MERGE_HEAD").strip()), "w",
              encoding="ascii") as fh:
        fh.write(unreviewed + "\n")
    capsys.readouterr()

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 1

    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert "a merge is in progress" in capsys.readouterr().out


def test_a_commit_gpgsign_git_cannot_read_refuses_the_commit(project, monkeypatch, capsys):
    """git commit refuses a commit.gpgSign it cannot read as a boolean; ao commit read the failed query as unset and
    wrote the commit unsigned (LEADS-GATE)."""
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    head = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "config", "commit.gpgSign", "bogus")
    capsys.readouterr()

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 1

    assert "commit.gpgSign cannot be read" in capsys.readouterr().out
    assert _git(root, "rev-parse", "HEAD").strip() == head
