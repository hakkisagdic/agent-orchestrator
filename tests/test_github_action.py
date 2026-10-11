"""action.yml, the GitHub Action that has a pull request reviewed by another model family, runs only what ao has (GITHUB-ACTION).

A composite action is YAML, which nothing in the standard library reads, and a wrong word in it shows only on someone's
pull request. So it is read as text here: every ao command its steps run is one the parser accepts; no input reaches a
script but through the environment, since a pull request's title written into a script is a command its author
chose; the key is never an input; every input and output is documented; the release it installs is the one this
commit is; and `fail-on` decides what the review's exit does to the job.
"""
import os
import re
import shutil
import subprocess

import pytest

import ao
from ao import cli
from tests.test_docs_commands import _commands, _refusals

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTION = os.path.join(ROOT, "action.yml")
DOC = os.path.join(ROOT, "docs", "github-action.md")


def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _mapping(text, key):
    """{name: its lines} of one top-level mapping of the action, read by its indentation."""
    names, current, inside = {}, None, False
    for line in text.splitlines():
        if re.fullmatch(rf"{key}:\s*", line):
            inside = True
            continue
        if inside and re.match(r"\S", line):
            break
        named = re.fullmatch(r"  ([a-z][a-z0-9-]*):\s*", line) if inside else None
        if named:
            current = named.group(1)
            names[current] = []
        elif inside and current:
            names[current].append(line.strip())
    return names


def _scripts(text):
    """Each step's `run:` script, a line its backslash continues joined to the next."""
    scripts, lines = [], text.splitlines()
    for at, line in enumerate(lines):
        found = re.fullmatch(r"(\s*)run:\s*(.*)", line)
        if not found:
            continue
        indent, rest = len(found.group(1)), found.group(2).strip()
        if rest not in ("|", "|-", ">", ">-"):
            scripts.append(rest)
            continue
        body = []
        for follow in lines[at + 1:]:
            if follow.strip() and len(follow) - len(follow.lstrip()) <= indent:
                break
            body.append(follow.strip())
        scripts.append("\n".join(body).replace("\\\n", " "))
    return scripts


def test_every_ao_command_a_step_runs_is_one_the_parser_accepts():
    parser, ran = cli.build_parser(), []
    for script in _scripts(_text(ACTION)):
        for line in script.splitlines():
            for written, words in _commands(line, synopsis=False):
                ran.append(written)
                assert _refusals(parser, words[1:]) == [], written

    assert [written.split(" --")[0] for written in ran] == ["ao config set review.api_providers \"ci "
                                                            "$REVIEW_BASE_URL $REVIEW_KEY_ENV\"", "ao ci review"]


def test_no_input_reaches_a_script_but_through_the_environment():
    scripts = _scripts(_text(ACTION))

    assert len(scripts) == 3 and not [script for script in scripts if "${{" in script]


def test_every_step_that_runs_a_script_names_its_shell():
    """A composite action's `run:` step without `shell:` is refused when the action loads, on someone's pull request."""
    steps = re.split(r"\n    - ", _text(ACTION).split("\n  steps:\n", 1)[1])
    runs = [step for step in steps if re.search(r"^\s*run:", step, re.M)]

    assert len(runs) == 3 and all(re.search(r"^\s+shell: bash$", step, re.M) for step in runs)


def test_the_key_is_named_by_an_input_and_never_given_as_one():
    inputs = _mapping(_text(ACTION), "inputs")

    assert "api-key-env" in inputs
    assert [name for name in inputs if re.search(r"(^|-)(key|token|secret|password)$", name)] == []


def test_every_input_and_output_is_documented_and_each_output_is_the_review_step_s():
    text, doc = _text(ACTION), _text(DOC)
    inputs, outputs = _mapping(text, "inputs"), _mapping(text, "outputs")

    assert sorted(inputs) == sorted(["model", "base-url", "api-key-env", "reviewer-family", "author-family", "base",
                                     "head", "boundary", "fail-on", "ao-version", "python-version"])
    assert [name for name in list(inputs) + list(outputs) if f"`{name}`" not in doc] == []
    assert all(f"value: ${{{{ steps.review.outputs.{name} }}}}" in lines for name, lines in outputs.items())
    assert re.search(r"^\s+id: review$", text, re.M) and re.search(r"^  using: composite$", text, re.M)


def test_the_release_it_installs_is_the_one_this_commit_is():
    default = next(line for line in _mapping(_text(ACTION), "inputs")["ao-version"] if line.startswith("default:"))

    assert default == f'default: "{ao.__version__}"'


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="the stand-in ao runs through its shebang")
@pytest.mark.parametrize("fail_on, exits", [
    ("needs-changes", {0: 0, 1: 1, 2: 2, 3: 3}),
    ("no-verdict", {0: 0, 1: 0, 2: 2, 3: 3}),
    ("never", {0: 0, 1: 0, 2: 0, 3: 0}),
    ("sometimes", {0: 2, 1: 2}),
])
def test_fail_on_decides_what_the_review_s_exit_does_to_the_job(tmp_path, fail_on, exits):
    review = _scripts(_text(ACTION))[-1]
    stand_in = tmp_path / "ao"
    stand_in.write_text('#!/bin/sh\nexit "$STAND_IN_EXIT"\n', encoding="utf-8")
    stand_in.chmod(0o755)
    for code, job in exits.items():
        env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}", STAND_IN_EXIT=str(code),
                   FAIL_ON=fail_on, GITHUB_STEP_SUMMARY=str(tmp_path / "summary"), GITHUB_OUTPUT=str(tmp_path / "out"))

        done = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", review], env=env,
                              capture_output=True, text=True, encoding="utf-8")

        assert done.returncode == job, (fail_on, code, done.stderr)



def test_every_action_the_composite_uses_is_pinned_to_a_commit():
    """A tag can be moved to other code; a commit cannot (GITHUB-ACTION)."""
    import re
    with open(ACTION, encoding="utf-8") as fh:
        uses = re.findall(r"^\s*uses:\s*(\S+)", fh.read(), re.M)

    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use) for use in uses), uses
