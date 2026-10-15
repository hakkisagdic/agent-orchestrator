"""A `.git` planted in the project must not move ao's commit authority to another repository (NESTED-GIT).

ao runs every enrollment and candidate query as `git -C <project root>`. Where the project is a subdirectory of its
repository, an implementer agent - which may write any file in the working tree (docs/threat-model.md) - can plant
`<project>/.git`, or a `.git` anywhere between the project and the repository's top: a directory holding a second
repository whose history added and then removed `.ao-project`, a `gitdir:` file, or a symlink. Git then resolves
`git -C <project>` to that other repository, `_project_enrollment` read the project as uninitialized, and
`ao commit-check` exited 0, so a commit of the OUTER repository landed unchecked. The v5 hook asked for the marker
from the top and was not fooled, but it handed the decision to ao, which was.

The fix: the hook is version 6. It names in AO_COMMIT_TOP the top of the working tree Git ran it in - taken from
`pwd -P`, not from a Git query, so a planted `.git` cannot move it - and ao refuses when `git -C <project>` resolves
to another repository. The test shows `ao commit-check` refuses and a real `git commit` through the installed hook
does not land, for a `.git` directory, a `gitdir:` file and a symlink; a project at its repository's top behaves
exactly as before; and a genuine nested repository committed through its own top is governed as its own.
A redirect is not broken enrollment: refusing it records nothing in either repository, and permitted
enrollment writes use the hook's top even if a .git appears after enrollment was measured.
"""
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli
from tests.test_commit_authority import _persist_exact_candidate_grant
from tests.test_hook_paths import _make_current_ao_available, _strip_colour

MARKER = cli.PROJECT_MARKER_BYTES
REDIRECT = "will not move its commit authority to another repository"


def _run(cwd, *argv):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *argv], cwd=str(cwd),
                          capture_output=True, text=True)


def _outer_with_subproject(tmp_path, project_rel="proj"):
    """An outer repository whose ao project is its `project_rel` subdirectory, the marker committed, the v6 hook in."""
    outer = tmp_path / "outer"
    proj = outer / project_rel
    (proj / ".ao").mkdir(parents=True)
    assert _run(outer, "init", "-q").returncode == 0
    (proj / cli.PROJECT_MARKER).write_bytes(MARKER)
    (proj / ".ao" / "config.json").write_text('{"project":"p"}\n', encoding="utf-8")
    (proj / ".gitignore").write_text(".ao/\n", encoding="utf-8")
    (proj / "work.txt").write_text("one\n", encoding="utf-8")
    assert _run(outer, "add", "--", project_rel).returncode == 0
    assert _run(outer, "commit", "-q", "-m", "enrol " + project_rel).returncode == 0
    hook = outer / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(cli._render_local_hook("pre-commit", project_rel))
    hook.chmod(0o755)
    # A staged change for the commit the test then tries to make.
    (proj / "work.txt").write_text("two\n", encoding="utf-8")
    assert _run(outer, "add", "--", project_rel + "/work.txt").returncode == 0
    return outer, proj


def _inner_repo_that_dropped_the_marker(path):
    """A throwaway repository whose history added and then removed `.ao-project`: reads as uninitialized."""
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init", "-q").returncode == 0
    (path / cli.PROJECT_MARKER).write_bytes(MARKER)
    assert _run(path, "add", cli.PROJECT_MARKER).returncode == 0
    assert _run(path, "commit", "-q", "-m", "add marker").returncode == 0
    assert _run(path, "rm", "-q", cli.PROJECT_MARKER).returncode == 0
    assert _run(path, "commit", "-q", "-m", "drop marker").returncode == 0
    return path


def _plant(tmp_path, proj, kind):
    """Plant `proj/.git` of `kind` ('directory', 'gitfile', 'symlink') pointing at an uninitialized repository."""
    if kind == "directory":
        _inner_repo_that_dropped_the_marker(proj)      # git init proj makes proj/.git a directory
        return proj / ".git"
    inner = _inner_repo_that_dropped_the_marker(tmp_path / ("inner-" + kind))
    if kind == "gitfile":
        (proj / ".git").write_text("gitdir: %s\n" % (inner / ".git"), encoding="utf-8")
    elif kind == "symlink":
        os.symlink(str(inner / ".git"), str(proj / ".git"))
    else:
        raise AssertionError(kind)
    return inner / ".git"


# ---- ao commit-check refuses a planted .git, whatever its kind ------------------------------------

@pytest.mark.parametrize("kind", ("directory", "gitfile", "symlink"))
@pytest.mark.parametrize("project_rel", ("proj", "parent/proj"))
@pytest.mark.parametrize("command", ("commit-check", "hooks"))
@pytest.mark.parametrize("anchored", (True, False))
def test_commit_check_refuses_when_a_planted_git_redirects_the_project(
        tmp_path, monkeypatch, capsys, kind, project_rel, command, anchored):
    if kind == "symlink" and not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    outer, proj = _outer_with_subproject(tmp_path, project_rel)
    planted = proj if project_rel == "proj" else proj.parent
    inner_git = _plant(tmp_path, planted, kind)
    # The hook names the real top; ao must hold its authority to it and refuse the redirect.
    if anchored:
        monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    else:
        monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)

    state = cli._project_enrollment(str(proj))
    if command == "commit-check":
        code = cli.cmd_commit_check({"root": str(proj)}, SimpleNamespace())
    else:
        code = cli.cmd_hooks({"root": str(proj)}, SimpleNamespace(action="install"))
    out = _strip_colour(capsys.readouterr().out)

    assert not (inner_git / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()
    assert state["state"] == "redirected"
    assert REDIRECT in state["detail"]
    assert code == 1 and "refused" in out.lower() and REDIRECT in out
    assert "redirect" in out and "remove the planted .git" in out
    assert "cannot record" not in out


def test_the_outer_tracked_marker_supplies_the_anchor_outside_a_hook(tmp_path, monkeypatch):
    """The outer tracked marker supplies the same binding when no hook exports it (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    _plant(tmp_path, proj, "directory")

    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    assert REDIRECT in (cli._commit_repository_binding(str(proj)) or "")

    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    assert REDIRECT in (cli._commit_repository_binding(str(proj)) or "")


@pytest.mark.parametrize("evidence", ("index", "head"))
def test_an_intermediate_repository_cannot_claim_the_outer_project(tmp_path, monkeypatch, capsys, evidence):
    """An intervening repository can forge the same marker; search beyond it (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path, "parent/proj")
    parent = proj.parent
    assert _run(parent, "init", "-q").returncode == 0
    assert _run(parent, "add", "proj/.ao-project").returncode == 0
    if evidence == "head":
        assert _run(parent, "commit", "-q", "-m", "forged enrollment").returncode == 0
        assert _run(parent, "read-tree", "--empty").returncode == 0
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)

    args = SimpleNamespace(range="HEAD..HEAD") if evidence == "head" else SimpleNamespace()
    code = cli.cmd_commit_check({"root": str(proj)}, args)
    out = _strip_colour(capsys.readouterr().out)
    assert REDIRECT in out and "remove the planted .git" in out
    assert code == (2 if evidence == "head" else 1)
    assert cli._project_repository_top(str(proj)) == str(outer)
    assert not (parent / ".git" / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()


def _redirect_command(proj, command):
    cfg = cli.A.load_config(str(proj))
    if command == "commit-check":
        return cli.cmd_commit_check(cfg, SimpleNamespace())
    if command == "range":
        return cli.cmd_commit_check(cfg, SimpleNamespace(range="HEAD..HEAD"))
    if command == "doctor":
        return cli.cmd_doctor(cfg, SimpleNamespace(check=True))
    if command == "remove":
        return cli.cmd_remove(cfg, SimpleNamespace(yes=True))
    if command == "adopt":
        return cli._adopt_project(cfg, SimpleNamespace())
    if command == "prove":
        return cli.cmd_prove(cfg, SimpleNamespace(no_review=True))
    return cli.cmd_hooks(cfg, SimpleNamespace(action=command.split("-")[1]))


@pytest.mark.parametrize("command", ("commit-check", "range", "doctor", "remove", "adopt", "prove",
                                    "hooks-install", "hooks-status", "hooks-uninstall"))
def test_standalone_commands_keep_outer_enrollment(tmp_path, monkeypatch, capsys, command):
    """Without a hook top, standalone commands still use ancestor enrollment evidence (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    inner_git = _plant(tmp_path, proj, "gitfile")
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)

    code = _redirect_command(proj, command)
    out = _strip_colour(capsys.readouterr().out)
    assert REDIRECT in out and "remove the planted .git" in out
    assert code == (2 if command == "range" else 1)
    assert (proj / ".ao" / "config.json").is_file()
    assert not (inner_git / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()


def _branch_without_the_project_marker(outer, evidence):
    """Leave only another ref's history or the common-directory record as enrollment evidence."""
    old_ref = _run(outer, "symbolic-ref", "HEAD").stdout.strip()
    if evidence in ("record", "unreadable-record"):
        assert cli.A.record_enrollment(str(outer)) is None
        assert _run(outer, "update-ref", "-d", old_ref).returncode == 0
        if evidence == "unreadable-record":
            (outer / ".git" / "ao-enrolled.json").write_text("broken json", encoding="utf-8")
    assert _run(outer, "symbolic-ref", "HEAD", "refs/heads/without-marker").returncode == 0
    assert _run(outer, "read-tree", "--empty").returncode == 0
    if evidence == "pre-marker":
        tree = _run(outer, "write-tree").stdout.strip()
        head = _run(outer, "commit-tree", tree, "-m", "before enrollment").stdout.strip()
        assert _run(outer, "update-ref", "HEAD", head).returncode == 0


@pytest.mark.parametrize("evidence", ("orphan", "pre-marker", "record", "unreadable-record"))
@pytest.mark.parametrize("command", ("commit-check", "hooks-install"))
def test_outer_enrollment_survives_a_branch_without_the_marker(
        tmp_path, monkeypatch, capsys, evidence, command):
    """An empty current index/HEAD cannot erase an ancestor's enrollment (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    _branch_without_the_project_marker(outer, evidence)
    record = outer / ".git" / "ao-enrolled.json"
    before = record.read_bytes() if record.exists() else None
    inner_git = _plant(tmp_path, proj, "gitfile")
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)

    code = _redirect_command(proj, command)
    out = _strip_colour(capsys.readouterr().out)
    assert REDIRECT in out and "remove the planted .git" in out
    assert code == 1
    assert cli._project_repository_top(str(proj)) == str(outer)
    assert not (inner_git / "ao-enrolled.json").exists()
    assert (record.read_bytes() if record.exists() else None) == before


@pytest.mark.parametrize("evidence", ("orphan", "record"))
def test_hooks_rechecks_a_git_planted_between_enrollment_and_record_selection(
        tmp_path, monkeypatch, capsys, evidence):
    """No hook environment, and the redirect exists before the record destination is selected."""
    outer, proj = _outer_with_subproject(tmp_path)
    _branch_without_the_project_marker(outer, evidence)
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    enrollment = cli._project_enrollment
    record = outer / ".git" / "ao-enrolled.json"
    before = record.read_bytes() if record.exists() else None
    planted = []

    def enroll_then_plant(root):
        state = enrollment(root)
        assert state["state"] == "broken"
        planted.append(_plant(tmp_path, proj, "gitfile"))
        return state

    monkeypatch.setattr(cli, "_project_enrollment", enroll_then_plant)
    code = cli.cmd_hooks({"root": str(proj)}, SimpleNamespace(action="install"))
    out = _strip_colour(capsys.readouterr().out)
    assert planted and not (planted[0] / "ao-enrolled.json").exists()
    assert (record.read_bytes() if record.exists() else None) == before
    assert code == 1 and REDIRECT in out and "remove the planted .git" in out


@pytest.mark.parametrize("mode", ("since-first-trailer", "range"))
def test_commit_check_usage_and_unreadable_ranges_keep_exit_two(tmp_path, monkeypatch, capsys, mode):
    """Binding failure must not turn a usage or unreadable-range error into a grant refusal."""
    root = tmp_path / "broken"
    root.mkdir()
    (root / ".git").write_text("gitdir: %s\n" % (tmp_path / "absent"), encoding="utf-8")
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    args = (SimpleNamespace(since_first_trailer=True) if mode == "since-first-trailer"
            else SimpleNamespace(range="HEAD~1..HEAD"))

    code = cli.cmd_commit_check({"root": str(root)}, args)
    out = _strip_colour(capsys.readouterr().out)
    assert code == 2
    assert ("--since-first-trailer goes with --range" if mode == "since-first-trailer"
            else "cannot check HEAD~1..HEAD") in out
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("command", ("commit-check", "hooks"))
def test_an_unresolvable_repository_is_refused_not_guessed(tmp_path, monkeypatch, capsys, command):
    """Where ao cannot resolve the project's repository at all, it refuses rather than judge the wrong one."""
    outer, proj = _outer_with_subproject(tmp_path)
    # `proj/.git` is a gitfile pointing nowhere: `git -C proj` cannot resolve a repository.
    (proj / ".git").write_text("gitdir: %s\n" % (tmp_path / "absent"), encoding="utf-8")
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))

    state = cli._project_enrollment(str(proj))
    if command == "commit-check":
        code = cli.cmd_commit_check({"root": str(proj)}, SimpleNamespace())
    else:
        code = cli.cmd_hooks({"root": str(proj)}, SimpleNamespace(action="install"))
    out = _strip_colour(capsys.readouterr().out)

    assert "cannot record" not in out
    assert state["state"] == "redirected" and "cannot confirm" in state["detail"]
    assert code == 1 and "refused" in out.lower()
    assert "redirect" in out and "remove that redirect" in out
    assert not (tmp_path / "absent").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()


# ---- a real git commit through the installed hook does not land -----------------------------------

def _redirect_project_directory(tmp_path, outer, proj, inside, at_parent):
    """Forge enrollment history beyond a directory symlink, with no outer enrollment record (NESTED-GIT)."""
    fake = (outer if inside else tmp_path) / "fake"
    target = fake / "proj"
    (target / ".ao").mkdir(parents=True)
    assert _run(fake, "init", "-q").returncode == 0
    (target / cli.PROJECT_MARKER).write_bytes(MARKER)
    assert _run(fake, "add", "proj/.ao-project").returncode == 0
    assert _run(fake, "commit", "-q", "-m", "forge enrollment").returncode == 0
    assert _run(fake, "rm", "-q", "proj/.ao-project").returncode == 0
    assert _run(fake, "commit", "-q", "-m", "drop enrollment").returncode == 0
    (target / ".ao" / "config.json").write_text('{"project":"fake"}\n', encoding="utf-8")
    replaced = proj.parent if at_parent else proj
    replaced.rename(outer / "saved-project")
    replaced.symlink_to(fake if at_parent else target, target_is_directory=True)
    return fake


@pytest.mark.parametrize("inside", (False, True))
@pytest.mark.parametrize("at_parent", (False, True))
@pytest.mark.parametrize("entry", ("hook", "resolved", "standalone"))
def test_directory_symlinks_cannot_move_repository_authority(
        tmp_path, monkeypatch, capsys, inside, at_parent, entry):
    outer, proj = _outer_with_subproject(tmp_path, "parent/proj" if at_parent else "proj")
    fake = _redirect_project_directory(tmp_path, outer, proj, inside, at_parent)
    head = _run(outer, "rev-parse", "HEAD").stdout
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    if entry == "hook":
        _make_current_ao_available(monkeypatch, tmp_path)
        done = _run(outer, "commit", "-m", "unreviewed symlink redirect")
        code, out = done.returncode, done.stdout + done.stderr
    else:
        root = str(proj)
        if entry == "resolved":
            monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
            root = os.path.realpath(root)
        code = cli.cmd_commit_check({"root": root}, SimpleNamespace())
        out = capsys.readouterr().out
    assert code != 0, out
    assert "redirect" in out and str(outer) in out
    assert _run(outer, "rev-parse", "HEAD").stdout == head
    assert not (fake / ".git" / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()


def test_hook_top_wins_even_when_an_outer_repository_claims_the_project(tmp_path, monkeypatch):
    """An independent nested repository's own hook is authoritative, even under an enrolled ancestor."""
    outer, proj = _outer_with_subproject(tmp_path)
    _plant(tmp_path, proj, "directory")
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(proj))
    assert cli._project_repository_top(str(proj)) == str(proj)
    assert cli._commit_repository_binding(str(proj)) is None


@pytest.mark.parametrize("anchored", (False, True))
def test_directory_symlink_cannot_substitute_a_marker_path_in_the_same_repository(
        tmp_path, monkeypatch, anchored):
    """Matching repository identities alone must not authorize a different project path (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    target = outer / "other"
    (target / ".ao").mkdir(parents=True)
    (target / ".ao" / "config.json").write_text('{"project":"other"}\n', encoding="utf-8")
    proj.rename(outer / "saved-project")
    proj.symlink_to(target, target_is_directory=True)
    if anchored:
        monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    else:
        monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    assert cli._repository_identity(str(proj)) == cli._repository_identity(str(outer))
    assert cli._project_enrollment(str(proj))["state"] == "redirected"


@pytest.mark.parametrize("kind", ("directory", "gitfile", "symlink"))
def test_a_git_commit_through_the_hook_does_not_land_over_a_planted_git(tmp_path, monkeypatch, kind):
    outer, proj = _outer_with_subproject(tmp_path)
    inner_git = _plant(tmp_path, proj, kind)
    _make_current_ao_available(monkeypatch, tmp_path)
    head = _run(outer, "rev-parse", "HEAD").stdout

    done = _run(outer, "commit", "-m", "land unchecked over a planted .git")

    assert not (inner_git / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()
    assert done.returncode != 0
    assert "remove the planted .git" in (done.stdout + done.stderr)
    assert REDIRECT in (done.stdout + done.stderr)
    assert _run(outer, "rev-parse", "HEAD").stdout == head          # nothing landed


def test_a_clean_subproject_commit_reaches_ao_and_is_refused_for_lack_of_a_grant(tmp_path, monkeypatch):
    """The hook path is intact for an honest project: with no `.git` planted the commit reaches ao, which refuses it
    for want of a grant rather than standing aside."""
    outer, proj = _outer_with_subproject(tmp_path)
    _make_current_ao_available(monkeypatch, tmp_path)
    head = _run(outer, "rev-parse", "HEAD").stdout

    done = _run(outer, "commit", "-m", "honest change")

    assert done.returncode != 0
    assert "no recorded authority decision" in (done.stdout + done.stderr)
    assert REDIRECT not in (done.stdout + done.stderr)
    assert _run(outer, "rev-parse", "HEAD").stdout == head


# ---- the top-level case, and a genuine nested repository, are unchanged ---------------------------

def test_a_project_at_the_repository_top_is_unchanged(project, monkeypatch):
    """The common case - the project is the whole repository - resolves its own top and behaves as before, whether or
    not the hook names it."""
    root = project["root"]
    before = cli._project_enrollment(root)
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, root)
    after = cli._project_enrollment(root)

    assert before["state"] == "enrolled" and after["state"] == "enrolled"
    assert cli._commit_repository_binding(root) is None


@pytest.mark.parametrize("gitfile", (False, True))
def test_a_genuine_nested_repository_committed_through_its_own_top_is_governed_as_its_own(
        tmp_path, monkeypatch, gitfile):
    """A separate repository a person made inside another and ran `ao init` in is committed through its own top, which
    the hook there names, so ao governs it as its own and does not read it as a redirect."""
    outer = tmp_path / "outer"
    inner = outer / "subproject"
    (inner / ".ao").mkdir(parents=True)
    assert _run(outer, "init", "-q").returncode == 0
    assert _run(inner, "init", "-q").returncode == 0                # a real repository of its own
    (inner / cli.PROJECT_MARKER).write_bytes(MARKER)
    (inner / ".ao" / "config.json").write_text('{"project":"inner"}\n', encoding="utf-8")
    assert _run(inner, "add", cli.PROJECT_MARKER).returncode == 0
    assert _run(inner, "commit", "-q", "-m", "enrol inner").returncode == 0

    if gitfile:
        admin = outer / ".git" / "modules" / "subproject"
        admin.parent.mkdir()
        (inner / ".git").rename(admin)
        (inner / ".git").write_text("gitdir: %s\n" % admin, encoding="utf-8")
        head = _run(inner, "rev-parse", "HEAD").stdout.strip()
        assert _run(outer, "update-index", "--add", "--cacheinfo", "160000", head, "subproject").returncode == 0
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    assert cli._project_enrollment(str(inner))["state"] == "enrolled"
    assert cli._commit_repository_binding(str(inner)) is None

    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(inner))              # its own commit names its own top
    state = cli._project_enrollment(str(inner))

    assert state["state"] == "enrolled"
    assert cli._commit_repository_binding(str(inner)) is None


# ---- a v5 hook reads as legacy and `ao hooks install` upgrades it to v6 ---------------------------

def test_a_v5_hook_reads_as_legacy_and_install_puts_v6_in_its_place(project):
    root = project["root"]
    hooks = Path(cli._ao_hook_inventory(root)["active_dir"])
    for role in ("pre-commit", "pre-push"):
        (hooks / role).write_bytes(cli._render_v5_local_hook(role, "."))
        (hooks / role).chmod(0o755)

    before = cli._ao_hook_inventory(root)
    assert {t["static_state"] for t in before["targets"] if t["active"]} == {"legacy (behavior unverified)"}
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=False)) == 0

    for target in cli._ao_hook_inventory(root)["targets"]:
        if target["active"]:
            assert target["static_state"] == "current-local (behavior unverified)"
            assert b"# agent-orchestrator: ao-hook-v6 " in (hooks / target["role"]).read_bytes()


@pytest.mark.parametrize("command", ("commit-check", "broken-commit-check", "hooks"))
def test_enrollment_write_uses_the_hook_top_if_a_git_is_planted_after_the_check(
        tmp_path, monkeypatch, capsys, command):
    """A checked project can gain a .git before the write; the write stays at the hook's top (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    if command == "broken-commit-check":
        (proj / ".ao" / "config.json").write_text("invalid", encoding="utf-8")
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    record = cli.A.record_enrollment
    written_from = []

    def plant_then_record(root):
        inner_git = _plant(tmp_path, proj, "gitfile")
        written_from.append(root)
        assert record(root) is None
        assert not (inner_git / "ao-enrolled.json").exists()
        return None

    measured = []
    measure = cli.A.index_candidate

    def measure_after_plant(root):
        candidate = measure(root)
        measured.append(candidate)
        return candidate

    monkeypatch.setattr(cli.A, "index_candidate", measure_after_plant)
    monkeypatch.setattr(cli.A, "record_enrollment", plant_then_record)
    if command == "hooks":
        code = cli.cmd_hooks({"root": str(proj)}, SimpleNamespace(action="install"))
    else:
        code = cli.cmd_commit_check({"root": str(proj)}, SimpleNamespace())
    assert code == 1
    assert written_from == [str(outer)]
    if command == "commit-check":
        assert measured and measured[0]["head"] == _run(outer, "rev-parse", "HEAD").stdout.strip()
    kept = json.loads((outer / ".git" / "ao-enrolled.json").read_text(encoding="utf-8"))
    assert kept["repository"] == str(outer / ".git")


@pytest.mark.parametrize("command", ("adopt", "prove", "hooks-status", "hooks-uninstall", "remove",
                                    "doctor", "doctor-problems"))
def test_other_enrollment_callers_refuse_a_redirect(tmp_path, monkeypatch, capsys, command):
    """No adoption, proof or hook management treats a redirect as an enrolled project (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    inner_git = _plant(tmp_path, proj, "gitfile")
    monkeypatch.delenv(cli.COMMIT_TOP_ENV, raising=False)
    cfg = cli.A.load_config(str(proj))
    if command == "adopt":
        code = cli._adopt_project(cfg, SimpleNamespace())
    elif command == "remove":
        code = cli.cmd_remove(cfg, SimpleNamespace(yes=True))
    elif command == "doctor":
        code = cli.cmd_doctor(cfg, SimpleNamespace())
    elif command == "doctor-problems":
        problems = dict(cli.doctor_problems(cfg))
        assert REDIRECT in problems["repository-redirect"]
        return
    elif command == "prove":
        code = cli.cmd_prove(cfg, SimpleNamespace(no_review=True))
    else:
        code = cli.cmd_hooks(cfg, SimpleNamespace(action=command.split("-")[1]))
    out = _strip_colour(capsys.readouterr().out)
    assert code == 1 and REDIRECT in out and "remove the planted .git" in out
    assert not (inner_git / "ao-enrolled.json").exists()
    assert not (outer / ".git" / "ao-enrolled.json").exists()


def _fake_head_repository(tmp_path, outer, proj, monkeypatch):
    """Share outer objects and index, but substitute the base commit a grant judges (NESTED-GIT)."""
    inner = tmp_path / "fake-head"
    inner.mkdir()
    assert _run(inner, "init", "-q").returncode == 0
    (inner / ".git" / "objects" / "info" / "alternates").write_text(
        str(outer / ".git" / "objects") + "\n", encoding="utf-8")
    tree = _run(outer, "write-tree").stdout.strip()
    with monkeypatch.context() as env:
        env.setenv("GIT_INDEX_FILE", str(tmp_path / "fake-index"))
        assert _run(inner, "read-tree", tree).returncode == 0
        harmless = tmp_path / "harmless"
        harmless.write_text("almost two\n", encoding="utf-8")
        blob = _run(inner, "hash-object", "-w", str(harmless)).stdout.strip()
        assert _run(inner, "update-index", "--cacheinfo", "100644", blob, "proj/work.txt").returncode == 0
        fake_tree = _run(inner, "write-tree").stdout.strip()
        fake_head = _run(inner, "commit-tree", fake_tree, "-m", "fake base").stdout.strip()
        assert _run(inner, "update-ref", "HEAD", fake_head).returncode == 0
    assert _run(inner, "config", "core.worktree", str(outer)).returncode == 0
    return inner / ".git", fake_head


def test_a_fake_base_grant_cannot_authorize_the_outer_commit_after_enrollment(
        tmp_path, monkeypatch, capsys):
    """The record succeeds, then a planted gitfile tries to substitute the grant's fake base (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    real_head = _run(outer, "rev-parse", "HEAD").stdout.strip()
    inner_git, fake_head = _fake_head_repository(tmp_path, outer, proj, monkeypatch)
    planted = proj / ".git"
    planted.write_text("gitdir: %s\n" % inner_git, encoding="utf-8")
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer / ".git" / "index"))
    cfg = {"root": str(proj), "reviews": ".ao/reviews"}
    (proj / ".ao" / "reviews").mkdir()
    fake_candidate = _persist_exact_candidate_grant(cfg)
    assert fake_candidate["head"] == fake_head != real_head
    planted.unlink()
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    record = cli.A.record_enrollment
    measure = cli.A.index_candidate
    measured = []

    def record_then_plant(root):
        result = record(root)
        assert result is None
        planted.write_text("gitdir: %s\n" % inner_git, encoding="utf-8")
        return result

    def capture_candidate(root):
        candidate = measure(root)
        measured.append(candidate)
        return candidate

    monkeypatch.setattr(cli.A, "record_enrollment", record_then_plant)
    monkeypatch.setattr(cli.A, "index_candidate", capture_candidate)
    code = cli.cmd_commit_check(cfg, SimpleNamespace())
    out = _strip_colour(capsys.readouterr().out)
    assert code == 1 and "AUTHORIZED" not in out
    assert measured and measured[0]["head"] == real_head
    assert "latest grant does not match the current index candidate" in out
    assert not (inner_git / "ao-enrolled.json").exists()
    assert measure(str(proj))["head"] == fake_head


@pytest.mark.parametrize("stage", ("candidate", "worktree", "review"))
def test_later_grant_measurements_keep_the_repository_when_git_is_planted(
        tmp_path, monkeypatch, capsys, stage):
    """Every measurement runs under the pinned binding, even when a redirect appears between them (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    inner_git, fake_head = _fake_head_repository(tmp_path, outer, proj, monkeypatch)
    real_head = _run(outer, "rev-parse", "HEAD").stdout.strip()
    cfg = {"root": str(proj), "reviews": ".ao/reviews"}
    (proj / ".ao" / "reviews").mkdir()
    _persist_exact_candidate_grant(cfg)
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(outer))
    measured = []

    def plant_and_measure(fn, root, *args, **kwargs):
        (proj / ".git").write_text("gitdir: %s\n" % inner_git, encoding="utf-8")
        measured.append(cli.A._git_output(root, "rev-parse", "HEAD").decode("ascii").strip())
        return fn(root, *args, **kwargs)

    if stage == "candidate":
        original = cli.A.index_candidate
        monkeypatch.setattr(cli.A, "index_candidate", lambda root: plant_and_measure(original, root))
    elif stage == "worktree":
        original = cli.A.candidate_worktree_issues
        monkeypatch.setattr(cli.A, "candidate_worktree_issues",
                            lambda *args, **kwargs: plant_and_measure(original, *args, **kwargs))
    else:
        original = cli.A.candidate_review_integrity
        monkeypatch.setattr(cli.A, "candidate_review_integrity",
                            lambda *args, **kwargs: plant_and_measure(original, *args, **kwargs))
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    assert measured == [real_head] and real_head != fake_head
    assert REDIRECT in _strip_colour(capsys.readouterr().out)
    assert cli.A._git_output(str(proj), "rev-parse", "HEAD").decode("ascii").strip() == fake_head


def test_a_linked_worktree_check_uses_its_own_index_and_keeps_the_project_root(
        tmp_path, monkeypatch, capsys):
    """Git's private worktree admin directory, not the common index, names the candidate (NESTED-GIT)."""
    outer, proj = _outer_with_subproject(tmp_path)
    linked = tmp_path / "linked"
    assert _run(outer, "worktree", "add", "-q", "-b", "linked", str(linked)).returncode == 0
    root = linked / "proj"
    (root / ".ao" / "reviews").mkdir(parents=True)
    (root / ".ao" / "config.json").write_text('{"project":"p"}\n', encoding="utf-8")
    (root / "work.txt").write_text("linked candidate\n", encoding="utf-8")
    assert _run(linked, "add", "proj/work.txt").returncode == 0
    cfg = {"root": str(root), "reviews": ".ao/reviews"}
    candidate = _persist_exact_candidate_grant(cfg)
    outer_candidate = cli.A.index_candidate(str(outer))
    assert candidate["index_tree"] != outer_candidate["index_tree"]
    monkeypatch.setenv(cli.COMMIT_TOP_ENV, str(linked))
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    assert "AUTHORIZED" in _strip_colour(capsys.readouterr().out)
    assert cli.A.index_candidate(str(outer)) == outer_candidate
