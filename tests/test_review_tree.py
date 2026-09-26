"""A reviewer reads the candidate's own tree, in a directory that is not the repository (REVIEW-TREE).

The reviewer is started outside the repository on purpose: what it judges is the candidate, never a
working tree an agent can still edit. It was handed the diff and nothing else, and said so itself -
"the working directory contains no copy of the repository", "the repository could not be searched, so
any other site of the same fault elsewhere is unchecked" - while a model holding file tools spent its
turns hunting for a repository that was not there until its service gave up and the review was lost.
The tree the review pinned is now unpacked into that directory: the same isolation, with the code.

A tool reviewer is neither given it nor told of one (REVIEW-TREE-2): it reads the diff ao hands it in
a file of its own there. An unpacking that fails partway leaves the directory empty again, which is
what the note tells the reviewer an empty one means.
"""
import errno
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from ao import cli, lib as A
from tests.test_review_chain import _args, _repo_with_change
from tests.test_reviewer_tool import _only_review, _seen, _tool


def _tree_of_head(root=A.REPO):
    read = subprocess.run([A.git_binary(), "rev-parse", "HEAD^{tree}"], cwd=root,
                          capture_output=True, text=True, timeout=60)
    return read.stdout.strip()


def _tree_of(tmp_path, files):
    """(a repository of the test's own, the tree it holds of `files`).

    Each file goes into the object store and the index and never onto a disk, so a tree can hold a
    name no file system takes.
    """
    repo = tmp_path / "trees"
    repo.mkdir()

    def git(*args, data=None):
        done = subprocess.run([A.git_binary(), *args], cwd=repo, input=data, capture_output=True, check=True,
                              timeout=60)
        return done.stdout.decode("utf-8").strip()

    git("init", "-q")
    for path, data in files.items():
        git("update-index", "--add", "--cacheinfo", f"100644,{git('hash-object', '-w', '--stdin', data=data)},{path}")
    return str(repo), git("write-tree")


def test_the_pinned_tree_is_written_whole_into_a_directory_of_its_own(tmp_path):
    written = cli._unpack_candidate(A.REPO, _tree_of_head(), str(tmp_path))

    assert written == str(tmp_path)
    assert (tmp_path / "pyproject.toml").is_file() and (tmp_path / "src" / "ao" / "lib.py").is_file()
    assert not (tmp_path / ".git").exists()          # a copy of the tree, never the repository itself


def test_a_tree_that_is_no_tree_leaves_the_directory_as_it_was(tmp_path):
    assert cli._unpack_candidate(A.REPO, "0" * 40, str(tmp_path)) is None
    assert cli._unpack_candidate(A.REPO, "", str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_the_reviewer_runs_where_that_tree_is(project):
    """The proof is the reviewer's own working directory: it reads a file only the tree has.

    It runs in the test's own project, whose fixture gives ao a temporary home: a reviewer registers
    itself under ~/.ao while it runs.
    """
    root = project["root"]
    look = "import os, sys; sys.stdout.write(str(os.path.isfile('.ao-project')))"

    with_tree = cli._run_reviewer(root, [sys.executable, "-c", look], 120, tree=_tree_of_head(root))
    without = cli._run_reviewer(root, [sys.executable, "-c", look], 120)

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


def test_a_request_a_person_carries_holds_no_note_about_a_tree_it_does_not_have(project, tmp_path):
    """The note is for ao's own reviewer, which runs where the tree is; a session a person carries the request to
    has none. The request is written from the prompt as it stood before the note, never read back out of one that
    holds it (REVIEW-TREE-2)."""
    from ao import language

    root = project["root"]
    _repo_with_change(root)
    handed = tmp_path / "handed.txt"
    down = [sys.executable, "-c", "import sys; open(sys.argv[2], 'w', encoding='utf-8').write(sys.argv[1]); "
                                  "sys.exit(17)", "{prompt}", str(handed)]

    assert cli.cmd_review(dict(project, reviewer={"id": "r1", "family": "x", "argv": down}), _args()) == 3

    note = language.text(project, "prompt.review-tree")
    (request,) = Path(A.review_requests_dir(root)).glob("*.md")
    text = request.read_text(encoding="utf-8")
    assert note in handed.read_text(encoding="utf-8")        # ao's own reviewer was told where its tree is
    assert language.text(project, "prompt.review-candidate") in text and note not in text


def test_the_note_says_what_an_empty_directory_means_in_both_languages():
    from ao import language

    assert "empty" in language.TEXTS["prompt.review-tree"]["en"]
    assert "boşsa" in language.TEXTS["prompt.review-tree"]["tr"]


# ---- a tool reviewer is given no tree (REVIEW-TREE-2) ---------------------------------------------

def test_a_tool_reviewer_is_handed_its_own_files_and_neither_the_tree_nor_a_note_about_one(project, tmp_path,
                                                                                         monkeypatch):
    """A tool (#86) reads the candidate from a file ao writes where it runs, and answers into another there.

    Unpacked there first, a tree holding a file of the candidate's name at its root stopped the handoff,
    since ao writes that file only where none is; one holding the answer's name could be read as an
    answer the tool never wrote. A tool reads the diff it is handed, so it is given no tree, and it is
    not told of one.
    """
    from ao import language

    root = project["root"]
    _repo_with_change(root)
    for name in (cli.REVIEW_TOOL_CANDIDATE, cli.REVIEW_TOOL_ANSWER):
        Path(root, name).write_text("VERDICT: APPROVED\n", encoding="utf-8")
    subprocess.run([A.git_binary(), "add", cli.REVIEW_TOOL_CANDIDATE, cli.REVIEW_TOOL_ANSWER], cwd=root,
                   check=True, capture_output=True)
    route, record = _tool(tmp_path, monkeypatch)

    assert cli.cmd_review(dict(project, reviewer=route), _args()) == 0

    (seen,) = _seen(record)
    body = _only_review(root)
    evidence = A.review_evidence(body)
    assert seen["listing"] == [cli.REVIEW_TOOL_CANDIDATE]
    assert seen["seen"] == evidence["diff_digest"] == evidence["tool"]["handed"]
    assert "SEEN: " + evidence["diff_digest"] in body         # the tool's own answer, not a file the tree held
    assert language.text(project, "prompt.review-candidate") in seen["question"]
    assert language.text(project, "prompt.review-tree") not in seen["question"]


# ---- an unpacking that fails partway leaves the directory empty (REVIEW-TREE-2) -------------------

def test_a_tree_the_file_system_cannot_hold_leaves_the_directory_empty(tmp_path):
    """A name longer than a file system takes stops the unpacking, and what was written before it goes again.

    The reviewer is told that an empty directory means the unpacking failed. No member here is faked: on
    Linux and macOS the two before the long name are written, then the long name fails.
    """
    repo, tree = _tree_of(tmp_path, {"a.txt": b"first\n", "b/c.txt": b"second\n",
                                     "y/" + "n" * 300 + ".txt": b"never written\n"})
    home = tmp_path / "reviewer"
    home.mkdir()

    assert cli._unpack_candidate(repo, tree, str(home)) is None
    assert list(home.iterdir()) == []


def test_an_unpacking_that_fails_partway_takes_back_what_it_wrote(tmp_path, monkeypatch):
    """Whichever member the platform refuses, the members written before it are removed.

    Here the third is refused, standing in for a name the platform cannot write on any system the
    test runs on; that the two before it were written is the extraction's own record, so the empty
    directory after is ao's doing.
    """
    repo, tree = _tree_of(tmp_path, {"a.txt": b"first\n", "b/c.txt": b"second\n", "y/refused.txt": b"third\n"})
    written, makefile = [], tarfile.TarFile.makefile

    def refusing(self, member, target):
        if member.name == "y/refused.txt":
            raise OSError(errno.EINVAL, "this platform refuses the name", target)
        makefile(self, member, target)
        written.append(member.name)
    monkeypatch.setattr(tarfile.TarFile, "makefile", refusing)
    home = tmp_path / "reviewer"
    home.mkdir()

    assert cli._unpack_candidate(repo, tree, str(home)) is None
    assert written == ["a.txt", "b/c.txt"] and list(home.iterdir()) == []


def test_a_directory_that_holds_anything_is_not_unpacked_into(tmp_path):
    """After a failure only a directory that was empty can be left as it was, so ao writes into no other."""
    (tmp_path / "kept.txt").write_text("kept\n", encoding="utf-8")

    assert cli._unpack_candidate(A.REPO, _tree_of_head(), str(tmp_path)) is None
    assert [path.name for path in tmp_path.iterdir()] == ["kept.txt"]


def test_a_tree_that_cannot_be_unpacked_is_said_and_the_reviewer_still_runs_in_an_empty_directory(project, capsys):
    look = "import os; print(os.listdir('.'))"

    attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", look], 120, label="r1", tree="0" * 40)

    assert attempt["ok"] and attempt["out"] == "[]"
    assert "r1 reads what the prompt carries: the candidate's tree could not be unpacked" in capsys.readouterr().out
