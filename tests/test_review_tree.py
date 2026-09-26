"""A reviewer reads the candidate's own tree, in a directory that is not the repository (REVIEW-TREE).

The reviewer is started outside the repository on purpose: what it judges is the candidate, never a
working tree an agent can still edit. It was handed the diff and nothing else, and said so itself -
"the working directory contains no copy of the repository", "the repository could not be searched, so
any other site of the same fault elsewhere is unchecked" - while a model holding file tools spent its
turns hunting for a repository that was not there until its service gave up and the review was lost.
The tree the review pinned is now unpacked into that directory: the same isolation, with the code.
"""
import os
import subprocess
import sys

from ao import cli, lib as A


def _tree_of_head():
    read = subprocess.run([A.git_binary(), "rev-parse", "HEAD^{tree}"], cwd=A.REPO,
                          capture_output=True, text=True, timeout=60)
    return read.stdout.strip()


def test_the_pinned_tree_is_written_whole_into_a_directory_of_its_own(tmp_path):
    written = cli._unpack_candidate(A.REPO, _tree_of_head(), str(tmp_path))

    assert written == str(tmp_path)
    assert (tmp_path / "pyproject.toml").is_file() and (tmp_path / "src" / "ao" / "lib.py").is_file()
    assert not (tmp_path / ".git").exists()          # a copy of the tree, never the repository itself


def test_a_tree_that_is_no_tree_leaves_the_directory_as_it_was(tmp_path):
    assert cli._unpack_candidate(A.REPO, "0" * 40, str(tmp_path)) is None
    assert cli._unpack_candidate(A.REPO, "", str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_the_reviewer_runs_where_that_tree_is():
    """The proof is the reviewer's own working directory: it reads a file only the tree has."""
    look = "import os, sys; sys.stdout.write(str(os.path.isfile('pyproject.toml')))"

    with_tree = cli._run_reviewer(A.REPO, [sys.executable, "-c", look], 120, tree=_tree_of_head())
    without = cli._run_reviewer(A.REPO, [sys.executable, "-c", look], 120)

    assert with_tree["ok"] and with_tree["out"].strip() == "True"
    assert without["ok"] and without["out"].strip() == "False"


def test_the_reviewer_is_told_where_the_tree_is_in_both_languages_without_changing_the_prompt_above():
    """A note of its own, so the Turkish prompt a sectioned review resumes on stays byte for byte."""
    from ao import language

    note = language.TEXTS["prompt.review-tree"]
    assert note["en"].startswith("--- TREE:") and note["tr"].startswith("--- AĞAÇ:")
    assert "TREE" not in language.TEXTS["prompt.review"]["en"] and "AĞAÇ" not in language.TEXTS["prompt.review"]["tr"]


def test_the_tree_is_never_written_inside_the_repository():
    """`_run_reviewer` refuses a temporary directory inside the repository; the tree follows it there."""
    assert cli._reviewer_temp_is_inside(A.REPO, os.path.join(A.REPO, "anything"))
    assert not cli._reviewer_temp_is_inside(A.REPO, os.path.realpath(os.sep + "tmp"))


def test_a_member_that_climbs_out_is_refused_by_where_it_lands_on_either_platform():
    """`..\\..\\x` is one odd word to POSIX and two parent directories to Windows; both are judged right."""
    import ntpath
    import posixpath

    assert not cli._lands_inside("C:\\tmp\\review", "..\\..\\pwned.txt", path=ntpath)
    assert not cli._lands_inside("/tmp/review", "../escape.txt", path=posixpath)
    assert cli._lands_inside("/tmp/review", "..\\..\\name-with-backslashes.txt", path=posixpath)
    assert cli._lands_inside("C:\\tmp\\review", "src\\ao\\lib.py", path=ntpath)


def test_a_request_a_person_carries_holds_no_note_about_a_tree_it_does_not_have(tmp_path):
    """The note describes ao's own reviewer; a session a person carries the request to has no such tree."""
    from ao import language

    cfg = {"root": str(tmp_path), "language": "en"}
    note = language.text(cfg, "prompt.review-tree")
    written = A.write_review_request(str(tmp_path), cfg, {"digest": "sha256:x", "index_tree": "t"}, {}, "d",
                                     "boundary", "SLICE", [], "the prompt\n\n" + note + "\n")

    text = open(written["path"], encoding="utf-8").read()
    assert "the prompt" in text and "--- TREE:" not in text


def test_the_note_says_what_an_empty_directory_means_in_both_languages():
    from ao import language

    assert "empty" in language.TEXTS["prompt.review-tree"]["en"]
    assert "boşsa" in language.TEXTS["prompt.review-tree"]["tr"]

