"""A merge after the first commit that carries Ao- trailers matches nothing, and nothing is merged to say so
(LEADS-GATE-MERGE).

`ao commit-check --range` passed a merge whatever its tree held: a conflict resolved by hand, or an edit made while
merging, landed unreviewed and passed the check CI runs. A grant binds one parent, so trailers on a merge say
nothing of what else it merges; a lane lands squashed.
"""
import os
import subprocess
import sys
from pathlib import Path

from ao import lib as A
from tests.test_commit_trailer import _check, _git, _landed

def _run(cwd, *argv):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *argv], cwd=str(cwd),
                          capture_output=True, text=True)


# ---- dbe70e2958e7eec6: a merge is held to its trailers, and nothing is merged to say so -------------

def _write(root, path, text):
    with open(os.path.join(root, path), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", path)


def test_a_merge_without_trailers_does_not_match_whatever_its_tree_holds(project, monkeypatch, capsys):
    root, base, _ = _landed(project, monkeypatch)
    landed = _git(root, "rev-parse", "HEAD").strip()
    clean = _git(root, "commit-tree", _git(root, "rev-parse", "HEAD^{tree}").strip(), "-p", landed, "-p", base,
                 "-m", "merge base").strip()
    _git(root, "update-ref", "HEAD", clean)

    # git's own merge: no review looked at what its two parents do together, so it is no grant's either - a merge
    # lands through ao commit, as any commit does, and a passing merge-check, which runs gates, is no review.
    A.record_merge_check(root, {"id": "MC-1", "at": 1, "into": landed, "branch": base,
                                "tree": _git(root, "rev-parse", "HEAD^{tree}").strip(), "profile": "quick",
                                "gates_digest": "x", "passed": True, "gates": [], "conflict": None})
    code, out = _check(root, capsys, f"{landed}..HEAD")
    assert code == 1 and "NO MATCH" in out and "a grant binds one: ao lands no merge" in out, out

    # The same two parents, and a file neither of them holds, written into the merge: refused the same way.
    _write(root, "src/unreviewed.py", "unreviewed = True\n")
    evil = _git(root, "commit-tree", _git(root, "write-tree").strip(), "-p", landed, "-p", base,
                "-m", "merge base").strip()
    _git(root, "update-ref", "HEAD", evil)

    code, out = _check(root, capsys, f"{landed}..HEAD")
    assert code == 1 and "NO MATCH" in out and "ao lands no merge" in out, out


def test_a_conflict_resolved_by_hand_does_not_match_and_the_check_runs_no_merge_driver(project, monkeypatch, capsys,
                                                                                       tmp_path):
    """`git merge-tree` runs the merge driver a repository configures for a path, a command an implementer can
    choose: the check merged a merge's parents again, and ran it. It merges nothing now (LEADS-GATE)."""
    root, _, _ = _landed(project, monkeypatch)
    landed = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "checkout", "-q", "-b", "side")
    _write(root, "src/a.py", "value = 2\n")
    _git(root, "commit", "-q", "-m", "side")
    _git(root, "checkout", "-q", "-")
    _write(root, "src/a.py", "value = 3\n")
    _git(root, "commit", "-q", "-m", "main")
    assert _run(root, "merge", "-q", "--no-ff", "-m", "merge side", "side").returncode != 0   # both change src/a.py
    _write(root, "src/a.py", "value = 4\nunreviewed = True\n")
    _git(root, "commit", "-q", "--no-edit")
    # Configured once the merge is made: the driver would have run for git's own merge as well.
    ran = tmp_path / "driver-ran"
    _git(root, "config", "merge.planted.driver",
         f'"{Path(sys.executable).as_posix()}" -c "open(\'{ran.as_posix()}\', \'w\').close()"; exit 1')
    os.makedirs(os.path.join(root, ".git", "info"), exist_ok=True)
    with open(os.path.join(root, ".git", "info", "attributes"), "a", encoding="utf-8") as fh:
        fh.write("src/a.py merge=planted\n")
    objects = _git(root, "count-objects", "-v")

    code, out = _check(root, capsys, f"{landed}..HEAD")

    assert not ran.exists()
    assert code == 1 and _git(root, "count-objects", "-v") == objects, out
    merge = [commit for commit in A.trailer_audit(root, f"{landed}..HEAD")["commits"] if commit["merge"]]
    assert len(merge) == 1 and "ao lands no merge" in merge[0]["problems"][0]


def test_a_merge_with_copied_trailers_and_the_granted_tree_does_not_match(project, monkeypatch, capsys):
    """Trailers on a merge say nothing of what else it merges: a merge of the granted tree, with a landed commit's
    trailers copied onto it, matched the range check while its other parent was granted nowhere (LEADS-GATE)."""
    root, base, _ = _landed(project, monkeypatch)
    landed = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "switch", "-q", "-c", "ungranted", base)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "anything")
    other = _git(root, "rev-parse", "HEAD").strip()
    message = _git(root, "log", "-1", "--format=%B", landed)
    copied = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit-tree",
                             _git(root, "rev-parse", f"{landed}^{{tree}}").strip(), "-p", landed, "-p", other],
                            cwd=root, input=message, capture_output=True, text=True, check=True).stdout.strip()

    code, out = _check(root, capsys, f"{landed}..{copied}")

    assert code == 1 and "ao lands no merge" in out, out
