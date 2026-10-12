"""A release is made from a version tag on main, by actions pinned to commits (RELEASE-PINNED).

The bug hunter's first run read .github/workflows/release.yml and named two leads. The workflow can be dispatched
on any ref, and its one check compared the ref's name with the packaged version: a branch named like a version
passed it, that branch's code was published to PyPI, and `gh release create` then tagged main's head with the
name - a release no tag reproduces. And the publishing job ran pypa/gh-action-pypi-publish by its branch
release/v1, beside the OIDC token PyPI trusts: whoever moves that branch chooses the code that holds the token.
Every workflow named its actions by tags, which can be moved as a branch can.

So the build refuses a ref that is not a tag and a tag on a commit main does not hold, the publishing jobs run
for a tag alone, and every action every workflow uses is named by a commit, its release written beside it.
"""
import os
import re
import subprocess

import pytest

from tests.test_github_action import _bash, _scripts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
RELEASE = os.path.join(WORKFLOWS, "release.yml")


def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _job(text, name):
    """The lines of one job of a workflow, read by its indentation."""
    lines = text.split("\n  " + name + ":\n", 1)[1].splitlines()
    return [line for line in lines[:next((at for at, line in enumerate(lines) if re.match(r"  \S", line)),
                                         len(lines))]]


def test_every_action_every_workflow_uses_is_named_by_a_commit_with_its_release_beside_it():
    names = sorted(name for name in os.listdir(WORKFLOWS) if name.endswith((".yml", ".yaml")))
    uses = [(name, line.strip()) for name in names for line in _text(os.path.join(WORKFLOWS, name)).splitlines()
            if re.match(r"\s*(- )?uses:", line)]

    assert names and len(uses) >= 20
    unpinned = [(name, line) for name, line in uses
                if not re.fullmatch(r"(- )?uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*", line)]
    assert unpinned == []


def test_the_jobs_that_publish_run_for_a_tag_alone():
    text = _text(RELEASE)

    for job in ("pypi", "github-release"):
        assert "    if: github.ref_type == 'tag'" in _job(text, job), job
    assert "id-token: write" in "\n".join(_job(text, "pypi"))


@pytest.fixture
def repository(tmp_path):
    """(path, main's commit, a commit main does not hold): a clone as the build checks it out, main fetched."""
    path = str(tmp_path / "clone")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", path], check=True)
    commits = []
    for name in ("on-main", "elsewhere"):
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
        subprocess.run(git + ["add", name], cwd=path, check=True)
        subprocess.run(git + ["commit", "-q", "-m", name], cwd=path, check=True)
        commits.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True,
                                      check=True).stdout.strip())
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", commits[0]], cwd=path, check=True)
    return path, commits[0], commits[1]


@pytest.mark.skipif(not _bash(), reason="the workflow's steps are bash scripts, and there is no bash here to run one")
def test_the_build_refuses_a_branch_and_a_tag_on_a_commit_main_does_not_hold(repository):
    path, on_main, elsewhere = repository
    (guard,) = [script for script in _scripts(_text(RELEASE)) if "merge-base --is-ancestor" in script]
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}

    def build(kind, sha):
        ref = ("refs/tags/" if kind == "tag" else "refs/heads/") + "v0.5.3"
        return subprocess.run([_bash(), "--noprofile", "--norc", "-e", "-c", guard], cwd=path, capture_output=True,
                              text=True, encoding="utf-8", env=dict(env, GITHUB_REF_TYPE=kind, GITHUB_REF=ref,
                                                                    GITHUB_REF_NAME="v0.5.3", GITHUB_SHA=sha))

    branch, outside, tagged = build("branch", on_main), build("tag", elsewhere), build("tag", on_main)

    assert branch.returncode == 1 and "refs/heads/v0.5.3 is a branch" in branch.stderr
    assert outside.returncode == 1 and f"tags {elsewhere}, which main does not hold" in outside.stderr
    assert tagged.returncode == 0, tagged.stderr
