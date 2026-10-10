"""`ao demo` plays the guarantee on a project of its own and leaves nothing behind (DEMO).

The README states what ao guarantees and `ao prove` checks it on a project that has ao already, so
the first refusal anyone saw was on their own repository. `ao demo` plays one change through the
command line a person types, in a scratch folder with a home of its own: a `divide` whose tests pass
and which does not handle b == 0 is found by a stand-in reviewer of another family, and neither
`ao commit-ok` nor a plain `git commit` lets it land; its fix is reviewed, granted and committed with
the Ao- trailers that name its grant. These tests run it end to end, as `ao demo` and as
`ao demo --keep`, in a temporary directory of the test's own, and once with a reviewer that approves
everything, which the demo must name as the step that did not behave.
"""
import json
import os
import re
import subprocess
import tempfile

from ao import cli, demo

# A reviewer that answers the probe and approves whatever it is shown: the bug then lands nowhere
# only if the demo notices that the review did not find it.
APPROVES_EVERYTHING = (
    "import sys\n"
    "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
    "if prompt.startswith('Reviewer invocation probe'):\n"
    "    print(prompt.strip().splitlines()[-1])\n"
    "else:\n"
    "    print('VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')\n"
)


def _play(tmp_path, monkeypatch, capsys, *words):
    """`ao <words>` as the parser reads it, with the temporary directory the test's own; (exit, what it printed)."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    args = cli.build_parser().parse_args(list(words))
    capsys.readouterr()
    code = args.fn({}, args)
    return code, re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _home():
    """Every path under the home the suite runs in."""
    home = os.path.expanduser("~")
    return sorted(os.path.relpath(os.path.join(folder, name), home)
                  for folder, folders, files in os.walk(home) for name in folders + files)


def _git(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def test_a_change_with_a_bug_is_refused_three_ways_and_its_fix_lands_naming_its_grant(tmp_path, monkeypatch,
                                                                                     capsys):
    home = _home()

    code, out = _play(tmp_path, monkeypatch, capsys, "demo")

    assert code == 0, out
    assert "DEMO PASSED" in out
    # In the order the demo plays them: the review finds the bug, commit-ok and the hook refuse, the fix lands.
    marks = ["NEEDS_CHANGES  BLOCKER 0 · HIGH 1", "- [HIGH] calc.py:9 - divide(1, 0) raises ZeroDivisionError",
             "$ ao commit-ok\n     REFUSED", "$ git commit -m \"add divide\"\n     COMMIT REFUSED", "HEAD is still",
             "APPROVED  BLOCKER 0 · HIGH 0", "GRANTED  C-", "Ao-Grant: C-", "TRAILERS MATCH  1 commit(s)"]
    at = [out.find(mark) for mark in marks]
    assert -1 not in at and at == sorted(at), dict(zip(marks, at))
    # Nothing is left behind: the scratch folder is gone, the home the suite runs in is as it was, and the
    # event log ao's variable names for this test was never written - the demo's commands had a home of their own.
    assert "removed " in out and os.listdir(tmp_path) == []
    assert _home() == home
    assert not os.path.exists(os.environ["AO_EVENTS"])


def test_keep_leaves_the_project_with_its_commit_its_reviews_and_its_ledger(tmp_path, monkeypatch, capsys):
    code, out = _play(tmp_path, monkeypatch, capsys, "demo", "--keep")

    assert code == 0, out
    (folder,) = os.listdir(tmp_path)
    scratch = os.path.join(str(tmp_path), folder)
    assert folder.startswith(demo.PREFIX) and f"kept: {scratch}" in out
    project = os.path.join(scratch, "project")
    assert _git(project, "log", "--format=%s").splitlines() == [
        "feat: add divide, which refuses b == 0", "adopt ao", "calc: add(a, b) and its test"]
    message = _git(project, "log", "-1", "--format=%B")
    assert "Ao-Grant: C-" in message and "Ao-Review: " in message
    # The stand-in reviewed as another model family through the project's own configuration, both times...
    reviews = os.path.join(project, "semantic-review")
    bodies = [open(os.path.join(reviews, name), encoding="utf-8").read()
              for name in os.listdir(reviews) if name.endswith(".md")]
    assert sorted(re.search(r"^VERDICT: (\w+)$", body, re.M).group(1) for body in bodies) == \
        ["APPROVED", "NEEDS_CHANGES"]
    assert all("family: `stand-in`" in body and "- tier: another model family" in body for body in bodies)
    # ...and the authority ledger holds the refusal, then the grant.
    with open(os.path.join(project, ".ao", "ledger", "authority.jsonl"), encoding="utf-8") as fh:
        assert [json.loads(line)["granted"] for line in fh if line.strip()] == [False, True]


def test_a_step_that_does_not_behave_is_named_and_the_demo_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "REVIEWER", APPROVES_EVERYTHING)

    code, out = _play(tmp_path, monkeypatch, capsys, "demo")

    assert code == 1
    assert ("DEMO STOPPED at step 3, the agent stages a change with a bug: ao review exited 0, where the review was "
            "to find the bug and say NEEDS_CHANGES") in out
    assert "ao demo --keep" in out and "GRANTED" not in out and "DEMO PASSED" not in out
    assert os.listdir(tmp_path) == []


def test_only_a_folder_the_demo_made_in_the_temporary_directory_is_removed(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    elsewhere = tmp_path / (demo.PREFIX + "made-elsewhere")
    elsewhere.mkdir()
    named_otherwise = temporary / "a-folder"
    named_otherwise.mkdir()

    assert "left alone" in demo._remove(str(elsewhere)) and elsewhere.is_dir()
    assert "left alone" in demo._remove(str(named_otherwise)) and named_otherwise.is_dir()

    made = tempfile.mkdtemp(prefix=demo.PREFIX)
    # Git writes its objects read-only, and Windows deletes no read-only file.
    read_only = os.path.join(made, "object")
    with open(read_only, "w", encoding="utf-8") as fh:
        fh.write("x")
    os.chmod(read_only, 0o444)

    assert demo._remove(made) is None and not os.path.exists(made)
