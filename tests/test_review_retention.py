import glob
import os
import subprocess
import time
from types import SimpleNamespace

from ao import cli, lib as A

OLD = time.time() - 40 * 86400
KINDS = ("grant", "verification", "board", "tracked", "loose", "recent")


def _artefact(root, name, when=OLD):
    path = os.path.join(root, "semantic-review", name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"VERDICT: APPROVED\n{name}\n")
    os.utime(path, (when, when))
    return path


def _commit(root, *paths):
    subprocess.run(["git", "add", *paths], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "evidence"],
                   cwd=root, check=True)


def _prune(project, apply):
    return cli.cmd_prune(project, SimpleNamespace(days=7, keep_kb=64, evidence=False, yes=apply, review_days=30))


def test_a_referenced_artefact_survives_a_prune_that_removes_its_neighbours(project, capsys):
    root = project["root"]
    names = {kind: f"2026-08-01-10000{i}-abc{i}.md" for i, kind in enumerate(KINDS)}
    for kind, name in names.items():
        _artefact(root, name, when=time.time() if kind == "recent" else OLD)
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review=names["grant"])
    A.record_verification(root, {"id": "V-2", "at": 1, "passed": True, "review": names["verification"]})
    with open(os.path.join(root, ".ao", "board.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- [S-1] a slice · review: {names['board']}\n")
    _commit(root, os.path.join("semantic-review", names["tracked"]))
    directory = os.path.join(root, "semantic-review")

    _prune(project, apply=False)

    out = capsys.readouterr().out
    assert set(os.listdir(directory)) == set(names.values())
    assert "would move" in out and "1 older than 30 day(s) that nothing rests on" in out
    assert "4 referenced (1 board, 1 grant, 1 tracked in git, 1 verification), 1 recent" in out

    _prune(project, apply=True)

    assert set(os.listdir(directory)) == set(names.values()) - {names["loose"]}
    moved = glob.glob(os.path.join(A.HOME, ".ao", "archive", A.project_key(root), "semantic-review-*",
                                   names["loose"]))
    assert len(moved) == 1 and "VERDICT: APPROVED" in open(moved[0], encoding="utf-8").read()


def test_nothing_is_pruned_when_what_rests_on_the_artefacts_cannot_be_read(project, capsys):
    root = project["root"]
    loose = _artefact(root, "2026-08-01-100000-abc.md")
    with open(A.review_ledger_path(root), "w", encoding="utf-8") as fh:
        fh.write('{"artefact": "2026-08-01-100000-abc.md"}\n')

    _prune(project, apply=True)

    assert os.path.exists(loose)
    assert "every artefact: what rests on them cannot be read" in capsys.readouterr().out


def test_the_doctor_names_a_granted_review_that_lives_only_on_this_disk(project):
    root = project["root"]
    kept = "2026-08-02-100000-abc.md"
    _artefact(root, kept)
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review=kept)
    A.record_authority(root, True, [], "sha256:u", "V-2", "C-2", review="2026-08-03-100000-gone.md")
    A.record_authority(root, False, ["refused"], "sha256:v", "V-3")

    text = "\n".join(cli._review_evidence_lines(project))
    assert "1 untracked, 1 missing of the reviews grants rest on" in text
    assert kept in text and "2026-08-03-100000-gone.md" in text

    _commit(root, os.path.join("semantic-review", kept))
    text = "\n".join(cli._review_evidence_lines(project))
    assert "0 untracked, 1 missing" in text and kept not in text
