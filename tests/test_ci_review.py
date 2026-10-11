"""`ao ci review`: a pull request's change reviewed in CI by a model of another family, with no setup (GITHUB-ACTION).

ao reviewed a change only where it governs the repository, with a reviewer a person had set in the project's config,
so a repository that does not use ao had no way to have a pull request read by another model family before merging
it. `ao ci review` reviews what a pull request adds - from the merge base of its base and head, as the pull request
shows it - the way catch-up reviews a landed range, with a reviewer composed for the run alone; it holds the change to
the family the workflow states wrote it and writes the verdict and the findings where the job reads them. These run it
on a repository with no .ao/ at all, with a stand-in reviewer, and once end to end through the API reviewer against a
stand-in of its endpoint.
"""
import json
import os
import subprocess
import sys

import pytest

from ao import cli, lib as A
from tests.test_api_review import APPROVED, endpoint  # noqa: F401  (endpoint is a fixture these tests use)

# The stand-in reviewer: it keeps the prompt it was handed and answers with what the test put beside it.
STAND_IN = ("import pathlib, sys\n"
            "here = pathlib.Path(sys.argv[1])\n"
            "(here / 'prompt.txt').write_text(sys.argv[2], encoding='utf-8')\n"
            "print((here / 'answer.txt').read_text(encoding='utf-8'))\n")
NEEDS_CHANGES = ("VERDICT: NEEDS_CHANGES\nBLOCKER: 0\nHIGH: 1\nMEDIUM: 0\nLOW: 0\n\n## Findings\n"
                 "- [HIGH] b.py:1 - b is set to 2 where the change says 1\n"
                 "  How it breaks: a caller reading b gets 2.\n\n"
                 "## Notes\n- [LOW] a.py:1 - outside the change, a note and no finding\n")


def _git(root, *args):
    return subprocess.run([A.git_binary(), *args], cwd=root, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def _write(root, name, text):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def repository(tmp_path):
    """A repository that does not use ao: a base, a branch that adds b.py, and a base that moved on after it.

    The branch's change is b.py alone; read from the base as it is now, two dots, it would also undo c.py.
    """
    root = tmp_path / "repository"
    root.mkdir()
    root = str(root)
    _git(root, "init", "-q")
    _write(root, "a.py", "a = 1\n")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "a")
    _git(root, "branch", "-M", "main")
    _git(root, "checkout", "-q", "-b", "feature")
    _write(root, "b.py", "b = 2\n")
    _git(root, "add", "b.py")
    _git(root, "commit", "-q", "-m", "feat: add b, which is 1\n\nb starts at 1 for every caller.")
    _git(root, "checkout", "-q", "main")
    _write(root, "c.py", "c = 3\n")
    _git(root, "add", "c.py")
    _git(root, "commit", "-q", "-m", "c lands on the base after the branch")
    return root


def _stand_in(monkeypatch, tmp_path, answer, family="reviewers"):
    (tmp_path / "answer.txt").write_text(answer, encoding="utf-8")
    route = {"id": "stand-in", "family": family, "argv": [sys.executable, "-c", STAND_IN, str(tmp_path), "{prompt}"]}
    monkeypatch.setattr(A, "compose_reviewer", lambda *args, **kwargs: dict(route))


def _ci(root, tmp_path, *words):
    """`ao ci review` as a workflow runs it, its summary and outputs written to files of the test's own."""
    summary, outputs = tmp_path / "summary.md", tmp_path / "outputs.txt"
    code = cli.main(["-C", root, "ci", "review", "--reviewer", "stand-in", "--family", "reviewers",
                     "--summary", str(summary), "--outputs", str(outputs), *words])
    read = lambda path: path.read_text(encoding="utf-8") if path.exists() else ""  # noqa: E731
    found = dict(line.split("=", 1) for line in read(outputs).splitlines())
    return code, read(summary), found


def test_what_a_pull_request_adds_is_reviewed_from_its_merge_base_and_the_findings_reach_the_job(
        repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, NEEDS_CHANGES)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "writers", "--boundary", "add b, set to 1")

    assert code == 1
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    # The branch's own change, from the merge base: b.py, and nothing that landed on the base since.
    assert "+b = 2" in prompt and "c.py" not in prompt
    # Judged against what its author says, and its commit messages, as claims rather than facts.
    assert "add b, set to 1" in prompt and "pull request that has not been merged" in prompt
    assert "b starts at 1 for every caller." in prompt
    assert "**NEEDS_CHANGES** · BLOCKER 0 · HIGH 1 · MEDIUM 0 · LOW 0" in summary
    assert "- [HIGH] b.py:1 - b is set to 2 where the change says 1\nHow it breaks: a caller reading b gets 2." in summary
    assert "outside the change" not in summary                     # a note is no finding
    assert "`writers`, as the workflow states it" in summary and "tier: another model family" in summary
    assert "grants nothing" in summary
    assert outputs["verdict"] == "NEEDS_CHANGES" and outputs["high"] == "1" and outputs["blocker"] == "0"
    assert outputs["base"] == _git(repository, "merge-base", "main", "feature")
    assert outputs["head"] == _git(repository, "rev-parse", "feature")
    assert os.path.isfile(os.path.join(repository, *outputs["artefact"].split("/")))
    # Nothing landed and nothing was staged: a review in CI grants nothing.
    assert _git(repository, "log", "--oneline", "main").count("\n") == 1
    assert _git(repository, "diff", "--cached", "--name-only") == ""


def test_a_reviewer_of_the_family_that_wrote_the_change_is_refused_before_it_runs(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, APPROVED)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "Reviewers")

    assert code == 2 and not (tmp_path / "prompt.txt").exists()
    assert "**not reviewed**" in summary and "it declares the author's model family (reviewers)" in summary
    assert outputs["verdict"] == "none"


def test_an_author_of_unknown_family_is_reviewed_and_no_independence_is_claimed(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, APPROVED)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature", "--author-family",
                                 "unknown")

    assert code == 0 and outputs["verdict"] == "APPROVED"
    assert "unknown, so the reviewer's independence from it is not checked" in summary
    assert "tier: not established" in summary and outputs["tier"] == "not established"


def test_a_head_that_adds_nothing_or_a_base_the_checkout_lacks_is_said(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, APPROVED)

    nothing = _ci(repository, tmp_path, "--base", "feature", "--head", "main~1", "--author-family", "writers")
    (tmp_path / "summary.md").unlink()
    (tmp_path / "outputs.txt").unlink()
    missing = _ci(repository, tmp_path, "--base", "no-such-branch", "--author-family", "writers")

    assert nothing[0] == 0 and "adds no commit" in nothing[1] and nothing[2]["verdict"] == "none"
    assert missing[0] == 2 and "fetch-depth: 0" in missing[1] and not (tmp_path / "prompt.txt").exists()


def test_commits_whose_diff_is_empty_are_not_reviewed_and_fail_nothing(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, NEEDS_CHANGES)
    _git(repository, "checkout", "-q", "-b", "undone")
    _write(repository, "d.py", "d = 4\n")
    _git(repository, "add", "d.py")
    _git(repository, "commit", "-q", "-m", "add d")
    _git(repository, "rm", "-q", "d.py")
    _git(repository, "commit", "-q", "-m", "take d out again")

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "undone",
                                 "--author-family", "writers")

    assert code == 0 and outputs["verdict"] == "none" and "**not reviewed**" in summary
    assert not (tmp_path / "prompt.txt").exists()


def test_a_revision_that_reads_as_an_option_is_refused(repository, monkeypatch, tmp_path, capsys):
    _stand_in(monkeypatch, tmp_path, APPROVED)

    code, _, _ = _ci(repository, tmp_path, "--base=--output=x", "--author-family", "writers")

    assert code == 2 and "reads as an option" in capsys.readouterr().out


def test_a_pull_request_is_reviewed_end_to_end_through_the_api_reviewer(repository, monkeypatch, tmp_path, endpoint):
    """The adapter the action names, its client run as a tool, asking a stand-in of a provider's endpoint."""
    served = endpoint()
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True)
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"ci {served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))                    # the client reads the machine's settings, as in CI
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (
        (client, "ao-api-review") if name == "ao-api-review" else (None, None)))
    summary = tmp_path / "summary.md"

    code = cli.main(["-C", repository, "ci", "review", "--base", "main", "--head", "feature", "--reviewer",
                     "openai-api", "--model", "ci/glm-5.3", "--family", "zhipu", "--author-family", "anthropic",
                     "--summary", str(summary)])

    assert code == 0
    (asked,) = served.requests
    assert asked["body"]["model"] == "glm-5.3" and "+b = 2" in asked["body"]["messages"][0]["content"]
    text = summary.read_text(encoding="utf-8")
    assert "**APPROVED**" in text and "family `zhipu`" in text and "a-key-for-this-test" not in text
