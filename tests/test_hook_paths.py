import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _git(root, *args, env=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _args(action, allow=False):
    return SimpleNamespace(action=action, allow_shared_hooks=allow)


def _cfg(root):
    return {
        "root": str(root),
        "mailbox": "agent-mail",
        "reviews": "semantic-review",
        "implementer": {"adapter": "kiro", "session": "s1", "name": "kiro"},
    }


def _init_repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(
        path,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    return path


def _enroll_project(root, commit=True):
    marker = Path(root) / cli.PROJECT_MARKER
    marker.write_bytes(cli.PROJECT_MARKER_BYTES)
    _git(root, "add", cli.PROJECT_MARKER)
    staged = _git(
        root, "diff", "--cached", "--quiet", "--", cli.PROJECT_MARKER,
        check=False,
    ).returncode != 0
    if commit and staged:
        _git(
            root,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "enroll ao",
        )
    return marker


def _commit_marker_absence(root):
    if _git(
        root, "ls-files", "--error-unmatch", "--", cli.PROJECT_MARKER,
        check=False,
    ).returncode != 0:
        return
    _git(root, "rm", "-q", "--", cli.PROJECT_MARKER)
    _git(
        root,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "fixture without ao marker",
    )


def _init_args(**overrides):
    values = {
        "name": None,
        "profile": None,
        "implementer": None,
        "model": None,
        "effort": None,
        "reviewer_model": None,
        "agent": None,
        "no_mcp": True,
        "rules": False,
        "watchdog": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _active(inv, role):
    return next(t for t in inv["targets"] if t["active"] and t["role"] == role)


def _strip_colour(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _make_current_ao_available(monkeypatch, tmp_path, body=None):
    """Put a deterministic `ao` command on the hook's PATH.

    The subprocess must import this worktree's source, not whichever editable AO
    happens to be installed on the machine running the tests.
    """
    bindir = tmp_path / "probe-bin"
    bindir.mkdir(exist_ok=True)
    executable = bindir / "ao"
    if body is None:
        source = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
        python = sys.executable.replace("\\", "/")
        source = source.replace("\\", "/")
        body = (
            "#!/bin/sh\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            f"export PYTHONPATH={shlex.quote(source)}\n"
            f"exec {shlex.quote(python)} -m ao.cli \"$@\"\n"
        )
    executable.write_text(body, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ.get("PATH", "")
    )
    return executable


def _git_snapshot(root):
    index = os.path.join(root, ".git", "index")
    return {
        "head": _git(root, "rev-parse", "HEAD").stdout,
        "status": _git(root, "status", "--porcelain=v1").stdout,
        "index": open(index, "rb").read() if os.path.exists(index) else None,
        "objects": _git(root, "count-objects", "-v").stdout,
    }


def test_relative_hooks_path_uses_linked_worktree_top_not_private_git_dir(project, tmp_path):
    root = project["root"]
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked", str(linked))
    _git(root, "config", "core.hooksPath", ".githooks")

    inv = cli._ao_hook_inventory(str(linked))

    assert inv["error"] is None
    assert inv["active_dir"] == os.path.realpath(linked / ".githooks")
    assert inv["git_dir"].endswith(os.path.join("worktrees", "linked"))
    assert inv["active_dir"] != os.path.join(inv["git_dir"], "hooks")
    assert inv["directory_class"] == "project-local"
    assert cli._ao_hook_paths(str(linked))["pre-commit"] == os.path.join(
        os.path.realpath(linked / ".githooks"), "pre-commit"
    )


def test_unconfigured_linked_worktrees_share_common_hooks(project, tmp_path):
    root = project["root"]
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked", str(linked))

    inv = cli._ao_hook_inventory(root)

    assert inv["error"] is None
    assert inv["active_dir"] == os.path.realpath(os.path.join(root, ".git", "hooks"))
    assert inv["directory_class"] == "shared"
    assert len([w for w in inv["worktrees"] if w["status"] == "reachable"]) == 2


def test_absolute_external_hooks_path_needs_explicit_authorization(project, tmp_path):
    root = project["root"]
    external = tmp_path / "external-hooks"
    _git(root, "config", "core.hooksPath", str(external))

    inv = cli._ao_hook_inventory(root)
    assert inv["directory_class"] == "external"
    assert cli.cmd_hooks(project, _args("install")) == 1
    assert not external.exists()

    assert cli.cmd_hooks(project, _args("install", allow=True)) == 0
    assert (external / "pre-commit").exists()
    assert (external / "pre-push").exists()
    assert str(os.path.realpath(os.path.join(root, ".git"))) in (
        external / "pre-commit"
    ).read_text(encoding="utf-8")


def test_global_scope_requires_authorization_even_when_path_is_project_local(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    home = tmp_path / "git-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _git(root, "config", "--global", "core.hooksPath", ".global-hooks")

    inv = cli._ao_hook_inventory(root)

    assert inv["config"]["scope"] == "global"
    assert inv["globally_configured"] is True
    assert inv["directory_class"] == "project-local"
    assert cli.cmd_hooks(project, _args("install")) == 1
    assert not os.path.exists(os.path.join(root, ".global-hooks", "pre-commit"))
    assert cli.cmd_hooks(project, _args("install", allow=True)) == 0


def test_git_environment_poisoning_is_removed(project, tmp_path, monkeypatch):
    root = project["root"]
    poison = _init_repo(tmp_path / "poison")
    monkeypatch.setenv("GIT_DIR", os.path.join(poison, ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(poison))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "poison-hooks"))
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "0")

    inv = cli._ao_hook_inventory(root)

    assert inv["error"] is None
    assert inv["top"] == os.path.realpath(root)
    assert inv["config"]["set"] is False
    assert inv["active_dir"] == os.path.realpath(os.path.join(root, ".git", "hooks"))


def test_config_parser_is_nul_safe_and_set_empty_uses_git_path(project):
    root = project["root"]
    weird = "hooks\twith-tab"
    _git(root, "config", "core.hooksPath", weird)
    inv = cli._ao_hook_inventory(root)
    assert inv["config"]["value"] == weird
    assert inv["active_dir"] == os.path.realpath(os.path.join(root, weird))

    _git(root, "config", "core.hooksPath", "")
    inv = cli._ao_hook_inventory(root)
    config = inv["config"]
    assert config["set"] is True
    assert config["scope"] == "local"
    assert config["value"] == ""
    # Git owns origin spelling: versions may emit file:.git/config or an
    # absolute file:<top>/.git/config. The resolver preserves it verbatim.
    assert config["origin"].startswith("file:")
    assert config["origin"][5:] in {
        ".git/config",
        os.path.join(root, ".git", "config"),
    }
    assert inv["active_dir"] == os.path.realpath(root)


def test_bare_repository_with_attached_worktree_uses_common_hooks(tmp_path):
    seed = _init_repo(tmp_path / "seed")
    (seed / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(seed, "add", "seed.txt")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
    _git(seed, "branch", "-M", "main")
    bare = tmp_path / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    linked = tmp_path / "from-bare"
    _git(bare, "worktree", "add", "-q", str(linked), "main")

    inv = cli._ao_hook_inventory(str(linked))

    assert inv["error"] is None
    assert inv["common_dir"] == os.path.realpath(bare)
    assert inv["active_dir"] == os.path.realpath(bare / "hooks")
    assert any(w["bare"] for w in inv["worktrees"])
    assert inv["directory_class"] == "shared"


def test_locked_missing_worktree_is_conservative_and_stale_unlocked_is_ignored(
    project, tmp_path
):
    root = project["root"]
    locked = tmp_path / "locked"
    stale = tmp_path / "stale"
    _git(root, "worktree", "add", "-q", "-b", "locked", str(locked))
    _git(root, "worktree", "lock", str(locked))
    shutil.rmtree(locked)
    _git(root, "worktree", "add", "-q", "-b", "stale", str(stale))
    shutil.rmtree(stale)

    inv = cli._ao_hook_inventory(root)

    rows = {os.path.realpath(w["path"]): w for w in inv["worktrees"]}
    assert rows[os.path.realpath(locked)]["status"] == "locked"
    assert rows[os.path.realpath(stale)]["status"] == "stale"
    assert inv["directory_class"] == "shared"


def test_unknown_symlinked_admin_fails_closed(project):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = project["root"]
    admins = os.path.join(root, ".git", "worktrees")
    os.makedirs(admins, exist_ok=True)
    os.symlink(os.path.join(root, ".git"), os.path.join(admins, "unknown"))

    inv = cli._ao_hook_inventory(root)

    assert inv["error"] is not None
    assert "unknown worktree admin" in inv["error"]


def test_tracked_missing_active_hook_is_never_recreated(project):
    root = project["root"]
    _git(root, "config", "core.hooksPath", ".githooks")
    hooks = os.path.join(root, ".githooks")
    os.makedirs(hooks)
    path = os.path.join(hooks, "pre-commit")
    with open(path, "wb") as fh:
        fh.write(b"#!/bin/sh\nexit 0\n")
    _git(root, "add", ".githooks/pre-commit")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "hook")
    os.remove(path)

    inv = cli._ao_hook_inventory(root)
    target = _active(inv, "pre-commit")
    assert target["static_state"] == "absent"
    assert target["track_state"] == "tracked"
    assert cli.cmd_hooks(project, _args("install")) == 1
    assert not os.path.exists(path)


def test_install_roles_are_independent_and_foreign_push_is_byte_identical(project):
    root = project["root"]
    _git(root, "config", "core.hooksPath", ".githooks")
    hooks = os.path.join(root, ".githooks")
    os.makedirs(hooks)
    pre_push = os.path.join(hooks, "pre-push")
    foreign = b"#!/bin/sh\necho custom\n"
    with open(pre_push, "wb") as fh:
        fh.write(foreign)

    assert cli.cmd_hooks(project, _args("install")) == 1

    pre_commit = os.path.join(hooks, "pre-commit")
    assert os.path.exists(pre_commit)
    assert os.stat(pre_commit).st_mode & 0o777 == 0o755
    assert root not in open(pre_commit, encoding="utf-8").read()
    assert open(pre_push, "rb").read() == foreign


def test_uninstall_authorization_is_command_wide_and_prevents_partial_local_delete(
    project, tmp_path
):
    root = project["root"]
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked", str(linked))
    _git(root, "config", "core.hooksPath", ".githooks")
    assert cli.cmd_hooks(project, _args("install")) == 0
    local_commit = os.path.join(root, ".githooks", "pre-commit")

    # Simulate a body left by the old <git-dir>/hooks resolver in the common dir.
    common = os.path.join(root, ".git", "hooks")
    legacy = os.path.join(common, "pre-commit")
    os.makedirs(common, exist_ok=True)
    with open(legacy, "w", encoding="utf-8") as fh:
        fh.write(cli.PRE_COMMIT_HOOK.format(ao="/x/ao", root=str(linked)))

    assert cli.cmd_hooks(project, _args("uninstall")) == 1
    assert os.path.exists(local_commit)
    assert os.path.exists(legacy)

    assert cli.cmd_hooks(project, _args("uninstall", allow=True)) == 0
    assert not os.path.exists(local_commit)
    assert not os.path.exists(legacy)


def test_dead_protected_repository_source_warns_but_does_not_block_remove(
    project, monkeypatch
):
    root = project["root"]
    _commit_marker_absence(root)
    source = os.path.join(root, ".githooks")
    os.makedirs(source)
    hook = os.path.join(source, "pre-commit")
    with open(hook, "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))
    _git(root, "add", ".githooks/pre-commit")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "source-hook")

    real_run = subprocess.run

    def fake_watchdog(argv, *args, **kwargs):
        if "watchdog" in argv and "uninstall" in argv:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", fake_watchdog)
    assert cli.cmd_remove(project, SimpleNamespace(yes=True, allow_shared_hooks=False)) == 0
    assert not os.path.exists(os.path.join(root, ".ao"))
    assert os.path.exists(hook)


def test_protected_effective_legacy_aborts_remove_with_state_intact(project, monkeypatch):
    root = project["root"]
    _commit_marker_absence(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    hooks = os.path.join(root, ".githooks")
    os.makedirs(hooks)
    hook = os.path.join(hooks, "pre-commit")
    with open(hook, "w", encoding="utf-8") as fh:
        fh.write(cli.PRE_COMMIT_HOOK.format(ao="/old/ao", root=root))
    _git(root, "add", ".githooks/pre-commit")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "legacy")

    assert cli.cmd_remove(project, SimpleNamespace(yes=True, allow_shared_hooks=False)) == 1
    assert os.path.isdir(os.path.join(root, ".ao"))
    assert os.path.exists(hook)


def test_doctor_has_commit_problems_but_never_a_push_alarm(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    keys = [key for key, _ in cli.doctor_problems(project)]
    assert "commit-hook" in keys
    assert not any("push-hook" in key for key in keys)

    _enroll_project(root)
    _make_current_ao_available(monkeypatch, tmp_path)
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked", str(linked))
    assert cli.cmd_hooks(project, _args("install", allow=True)) == 0
    keys = [key for key, _ in cli.doctor_problems(project)]
    assert "commit-hook" not in keys
    assert "commit-hook-routing-unverified" not in keys
    assert not any("push-hook" in key for key in keys)


def test_status_names_config_path_class_track_state_and_misplaced(
    project, capsys
):
    root = project["root"]
    _git(root, "config", "core.hooksPath", ".githooks")
    old = os.path.join(root, ".git", "hooks", "pre-commit")
    with open(old, "w", encoding="utf-8") as fh:
        fh.write(cli.PRE_COMMIT_HOOK.format(ao="/old/ao", root=root))

    assert cli.cmd_hooks(project, _args("status")) == 0
    output = _strip_colour(capsys.readouterr().out)
    assert "effective hooks:" in output
    assert "class: project-local" in output
    assert "core.hooksPath: local" in output
    assert "pre-commit: absent / untracked" in output
    assert "misplaced pre-commit: legacy (behavior unverified)" in output
    assert "potentially-effective" in output


@pytest.mark.parametrize("body_kind", ("v1", "v2"))
def test_user_extended_ao_hook_is_ambiguous_and_never_mutated(
    project, body_kind
):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    if body_kind == "v1":
        body = cli.PRE_COMMIT_HOOK.format(ao="/old/ao", root=root).encode("utf-8")
    else:
        body = cli._render_local_hook("pre-commit", ".")
    extended = body.replace(
        b"exec ", b"./scripts/my-lint.sh || exit 1\nexec ", 1
    )
    with open(path, "wb") as fh:
        fh.write(extended)

    target = _active(cli._ao_hook_inventory(root), "pre-commit")
    assert target["static_state"] == "ambiguous-ao"
    assert target["eligible"] is False

    assert cli.cmd_hooks(project, _args("install")) == 1
    assert open(path, "rb").read() == extended
    assert cli.cmd_hooks(project, _args("uninstall")) == 1
    assert open(path, "rb").read() == extended


@pytest.mark.parametrize("body_kind", ("v1", "v2"))
def test_lf_shebang_with_crlf_body_is_ambiguous(project, body_kind):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    if body_kind == "v1":
        body = cli.PRE_COMMIT_HOOK.format(ao="/old/ao", root=root).encode("utf-8")
    else:
        body = cli._render_local_hook("pre-commit", ".")
    shebang = b"#!/bin/sh\n"
    assert body.startswith(shebang)
    mixed = shebang + body[len(shebang):].replace(b"\n", b"\r\n")
    with open(path, "wb") as fh:
        fh.write(mixed)

    target = _active(cli._ao_hook_inventory(root), "pre-commit")
    assert target["static_state"] == "ambiguous-ao"
    assert target["eligible"] is False


@pytest.mark.parametrize(
    ("role", "template"),
    (
        ("pre-commit", cli.PRE_COMMIT_HOOK),
        ("pre-push", cli.PRE_PUSH_HOOK),
    ),
)
def test_shell_quoted_v1_generated_body_is_legacy(project, role, template):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", role)
    body = template.format(
        ao="'/opt/AO Tool/bin/ao'",
        root="'/tmp/My Projects/repo'",
    ).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(body)

    target = _active(cli._ao_hook_inventory(root), role)
    assert target["static_state"] == "legacy (behavior unverified)"
    assert target["eligible"] is True


def test_nul_in_scoped_candidate_is_ambiguous_without_resolver_failure(project):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    body = cli._render_scoped_hook(
        "pre-commit", ".", os.path.join(root, ".git"), "shared"
    )
    corrupt = b"".join(
        b"expected=/private/tmp/fam\x00/.git\n"
        if line.startswith(b"expected=") else line
        for line in body.splitlines(keepends=True)
    )
    assert corrupt != body
    with open(path, "wb") as fh:
        fh.write(corrupt)

    assert cli._legacy_hook_role(corrupt) is None
    inv = cli._ao_hook_inventory(root)
    assert inv["error"] is None
    target = _active(inv, "pre-commit")
    assert target["static_state"] == "ambiguous-ao"
    assert target["eligible"] is False
    assert cli.cmd_hooks(project, _args("status")) == 0
    assert "commit-hook" in {key for key, _ in cli.doctor_problems(project)}


def test_static_classifier_handles_crlf_mixed_prior_undecodable_and_symlink(project):
    root = project["root"]
    body = cli._render_local_hook("pre-commit", ".")
    hooks = os.path.join(root, ".git", "hooks")
    path = os.path.join(hooks, "pre-commit")

    with open(path, "wb") as fh:
        fh.write(body.replace(b"\n", b"\r\n"))
    assert cli._ao_hook_inventory(root)["targets"][0]["static_state"] == "legacy (behavior unverified)"

    with open(path, "wb") as fh:
        fh.write(body.replace(b"\n", b"\r\n", 1))
    assert _active(cli._ao_hook_inventory(root), "pre-commit")["static_state"] in {
        "foreign",
        "ambiguous-ao",
    }

    prior = body.replace(
        b"initial_cwd=$(CDPATH= cd -- . && pwd -P)\n",
        b"initial_cwd=$PWD\n",
        1,
    )
    with open(path, "wb") as fh:
        fh.write(prior)
    assert _active(cli._ao_hook_inventory(root), "pre-commit")["static_state"] == \
        "legacy (behavior unverified)"

    with open(path, "wb") as fh:
        fh.write(b"\xff\xfe agent-orchestrator commit-check")
    assert _active(cli._ao_hook_inventory(root), "pre-commit")["static_state"] == "foreign"

    os.remove(path)
    if hasattr(os, "symlink"):
        os.symlink(os.path.join(root, "missing"), path)
        assert _active(cli._ao_hook_inventory(root), "pre-commit")["static_state"] == "foreign"


@pytest.mark.parametrize("failing_command", ("rev-parse", "ls-files"))
def test_hook_track_state_query_failures_are_indeterminate(
    project, monkeypatch, failing_command
):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    with open(path, "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))
    real_hook_git = cli._hook_git

    def failing_hook_git(cwd, *args, **kwargs):
        if args and args[0] == failing_command:
            raise cli._HookResolutionError("measured query failure")
        return real_hook_git(cwd, *args, **kwargs)

    monkeypatch.setattr(cli, "_hook_git", failing_hook_git)
    assert cli._hook_track_state(path) == "indeterminate"


def test_common_fallback_is_never_remove_inert_local(project):
    root = project["root"]
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    with open(path, "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))

    inv = cli._ao_hook_inventory(root)
    target = _active(inv, "pre-commit")
    assert target["static_state"] == "current-local (behavior unverified)"
    assert os.path.realpath(target["directory"]) == os.path.realpath(
        os.path.join(inv["common_dir"], "hooks")
    )
    assert cli._remove_inert_local(target, inv) is False

    protected = dict(target, protected=True, track_state="indeterminate", eligible=False)
    altered = dict(inv, targets=[
        protected if row is target else row for row in inv["targets"]
    ])
    safe, _, _ = cli._remove_hook_preflight(altered, allow=False)
    assert safe is False


def test_rendered_hooks_capture_physical_cwd(project):
    root = project["root"]
    bodies = (
        cli._render_local_hook("pre-commit", "."),
        cli._render_scoped_hook(
            "pre-commit", ".", os.path.join(root, ".git"), "shared"
        ),
    )
    for body in bodies:
        assert b"initial_cwd=$(CDPATH= cd -- . && pwd -P)\n" in body
        assert b"initial_cwd=$PWD\n" not in body


def test_repository_hook_contract_forces_lf_mode_and_preserves_custom_pre_push(tmp_path):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    attrs = open(os.path.join(repo, ".gitattributes"), encoding="utf-8").read().splitlines()
    assert ".githooks/pre-commit text eol=lf" in attrs

    pre_commit = os.path.join(repo, ".githooks", "pre-commit")
    body = open(pre_commit, "rb").read()
    assert body == cli._render_local_hook("pre-commit", ".")
    assert b"\r\n" not in body and body.endswith(b"\n")
    assert os.stat(pre_commit).st_mode & 0o777 == 0o755
    mode = _git(repo, "ls-files", "-s", ".githooks/pre-commit").stdout.split()[0]
    assert mode == "100755"

    tracked_push = subprocess.run(
        ["git", "show", "HEAD:.githooks/pre-push"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert open(os.path.join(repo, ".githooks", "pre-push"), "rb").read() == tracked_push

    fixture = _init_repo(tmp_path / "source")
    (fixture / ".githooks").mkdir()
    (fixture / ".gitattributes").write_text(
        ".githooks/pre-commit text eol=lf\n", encoding="utf-8"
    )
    fixture_hook = fixture / ".githooks" / "pre-commit"
    fixture_hook.write_bytes(body)
    fixture_hook.chmod(0o755)
    _git(fixture, "add", ".gitattributes", ".githooks/pre-commit")
    _git(
        fixture,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "hook contract",
    )
    clone = tmp_path / "autocrlf"
    subprocess.run(
        ["git", "-c", "core.autocrlf=true", "clone", "-q", str(fixture), str(clone)],
        check=True,
    )
    assert b"\r\n" not in open(clone / ".githooks" / "pre-commit", "rb").read()



def test_execution_probe_uses_git_hook_runner_without_repository_residue(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _enroll_project(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0

    inventory = cli._ao_hook_inventory(root)
    before = _git_snapshot(root)
    proof = cli._hook_execution_probe(inventory)
    after = _git_snapshot(root)

    assert proof == {
        "installed": True,
        "state": "installed",
        "detail": "Git executed pre-commit and AO refused the synthetic candidate",
        "exit": 1,
    }
    assert after == before
    assert not any(name.startswith(".ao-hook-probe-") for name in os.listdir(root))


@pytest.mark.parametrize(
    "missing",
    (
        "AO_HOOK_PROBE_NONCE",
        "AO_HOOK_PROBE_PATH",
        "AO_HOOK_PROBE_HEAD",
        "AO_HOOK_PROBE_INDEX",
    ),
)
def test_partial_hook_probe_environment_is_inert_for_live_commit_path(
    project, monkeypatch, capsys, missing
):
    index = os.path.abspath(os.path.join(project["root"], "probe-index"))
    values = {
        "AO_HOOK_PROBE_NONCE": "0" * 32,
        "AO_HOOK_PROBE_PATH": ".ao-hook-probe-" + "0" * 32,
        "AO_HOOK_PROBE_HEAD": "1" * 40,
        "AO_HOOK_PROBE_INDEX": index,
    }
    monkeypatch.setenv("GIT_INDEX_FILE", index)
    for name, value in values.items():
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert cli._commit_hook_probe_response(project["root"]) is None
    assert capsys.readouterr().out == ""


def test_complete_hook_probe_binds_namespaced_index_to_git_index(
    project, monkeypatch, capsys
):
    root = project["root"]
    nonce = "0" * 32
    monkeypatch.setenv("AO_HOOK_PROBE_NONCE", nonce)
    monkeypatch.setenv("AO_HOOK_PROBE_PATH", ".ao-hook-probe-" + nonce)
    monkeypatch.setenv("AO_HOOK_PROBE_HEAD", "1" * 40)
    monkeypatch.setenv(
        "AO_HOOK_PROBE_INDEX", os.path.abspath(os.path.join(root, "probe-index"))
    )
    monkeypatch.setenv(
        "GIT_INDEX_FILE", os.path.abspath(os.path.join(root, "different-index"))
    )

    assert cli._commit_hook_probe_response(root) == 1
    output = _strip_colour(capsys.readouterr().out)
    assert "COMMIT REFUSED" in output
    assert "index does not match Git's active index" in output
    assert "AO-HOOK-PROBE-REFUSED" not in output


def test_installed_hook_resolves_relative_alternate_index_from_repository_root(
    project, tmp_path, monkeypatch
):
    root = Path(project["root"])
    _enroll_project(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0

    alternate = root / "relative-hook-index"
    setup_env = os.environ.copy()
    setup_env["GIT_INDEX_FILE"] = str(alternate)
    _git(root, "read-tree", "HEAD", env=setup_env)
    hook_env = os.environ.copy()
    hook_env["GIT_INDEX_FILE"] = alternate.name

    result = _git(root, "hook", "run", "pre-commit", env=hook_env, check=False)
    output = _strip_colour(result.stdout + result.stderr)

    assert result.returncode == 1
    assert "COMMIT REFUSED" in output
    assert "no staged candidate" in output
    assert "active index marker query failed" not in output


def test_status_doctor_and_init_report_the_same_execution_proof(
    project, tmp_path, monkeypatch, capsys
):
    root = project["root"]
    _enroll_project(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0
    capsys.readouterr()

    assert cli.cmd_hooks(project, _args("status")) == 0
    status = _strip_colour(capsys.readouterr().out)
    assert "pre-commit execution: installed (execution proved)" in status
    assert "commit-hook" not in {key for key, _ in cli.doctor_problems(project)}

    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)
    cli.cmd_doctor(project, SimpleNamespace(check=False))
    doctor = _strip_colour(capsys.readouterr().out)
    assert re.search(r"^commit proof\s+installed \(execution proved\)$", doctor, re.M)

    args = _init_args()
    assert cli.cmd_init(project, args) == 0
    initialized = _strip_colour(capsys.readouterr().out)
    assert "commit hook installed (execution proved)" in initialized


def test_marker_preserving_exit_zero_and_misplaced_body_are_not_installed(
    project, capsys
):
    root = project["root"]
    _git(root, "config", "core.hooksPath", ".githooks")
    active_dir = os.path.join(root, ".githooks")
    os.makedirs(active_dir)
    active = os.path.join(active_dir, "pre-commit")
    body = cli._render_local_hook("pre-commit", ".")
    with open(active, "wb") as fh:
        fh.write(
            b"#!/bin/sh\n"
            b"# agent-orchestrator: ao-hook-v2 role=pre-commit binding=project-local\n"
            b"exit 0\n"
        )
    os.chmod(active, 0o755)

    assert cli.cmd_hooks(project, _args("status")) == 0
    rewritten = _strip_colour(capsys.readouterr().out)
    assert "pre-commit execution: not installed" in rewritten
    assert "installed (execution proved)" not in rewritten
    assert "commit-hook" in {key for key, _ in cli.doctor_problems(project)}

    os.remove(active)
    misplaced = os.path.join(root, ".git", "hooks", "pre-commit")
    with open(misplaced, "wb") as fh:
        fh.write(body)
    os.chmod(misplaced, 0o755)

    assert cli.cmd_hooks(project, _args("status")) == 0
    moved = _strip_colour(capsys.readouterr().out)
    assert "pre-commit: absent" in moved
    assert "pre-commit execution: not installed" in moved
    assert "misplaced pre-commit: current-local (behavior unverified)" in moved
    assert "commit-hook" in {key for key, _ in cli.doctor_problems(project)}


def test_nonzero_hook_without_nonce_bound_refusal_is_not_proof(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _enroll_project(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path, "#!/bin/sh\nexit 1\n")
    hooks = os.path.join(root, ".githooks")
    os.makedirs(hooks)
    path = os.path.join(hooks, "pre-commit")
    with open(path, "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))
    os.chmod(path, 0o755)

    proof = cli._hook_execution_probe(cli._ao_hook_inventory(root))

    assert proof["installed"] is False
    assert "without AO's nonce-bound refusal proof" in proof["detail"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode is not a Windows hook property")
def test_non_executable_current_body_is_not_installed(project, tmp_path, monkeypatch):
    root = project["root"]
    _enroll_project(root)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    hooks = os.path.join(root, ".githooks")
    os.makedirs(hooks)
    path = os.path.join(hooks, "pre-commit")
    with open(path, "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))
    os.chmod(path, 0o644)

    proof = cli._hook_execution_probe(cli._ao_hook_inventory(root))

    assert proof["installed"] is False
    assert proof["exit"] != 0
    assert "without AO's nonce-bound refusal proof" in proof["detail"]


def test_shared_worktree_hook_is_installed_only_after_scoped_route_executes(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _enroll_project(root)
    linked = tmp_path / "linked-execution"
    _git(root, "worktree", "add", "-q", "-b", "linked-execution", str(linked))
    _make_current_ao_available(monkeypatch, tmp_path)

    assert cli.cmd_hooks(project, _args("install", allow=True)) == 0
    inventory = cli._ao_hook_inventory(root)
    target = _active(inventory, "pre-commit")
    proof = cli._hook_execution_probe(inventory)

    assert target["static_state"] == "current-scoped (behavior unverified)"
    assert proof["installed"] is True
    assert "commit-hook" not in {key for key, _ in cli.doctor_problems(project)}



def test_fresh_repository_and_incidental_ao_directory_skip_hook_silently(tmp_path):
    root = _init_repo(tmp_path / "plain")
    (root / ".ao" / "ledger").mkdir(parents=True)
    hooks = root / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_bytes(cli._render_local_hook("pre-commit", "."))
    hook.chmod(0o755)
    _git(root, "config", "core.hooksPath", ".githooks")

    state = cli._project_enrollment(str(root))
    result = _git(root, "hook", "run", "pre-commit", check=False)

    assert state["state"] == "uninitialized"
    assert cli.PROJECT_MARKER in state["detail"]
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (None, "broken"),
        (b"", "broken"),
        (b"{", "broken"),
        (b"[]", "broken"),
        (b"{}", "broken"),
        (b'{"project":"fixture"}\n', "enrolled"),
    ),
)
def test_project_enrollment_golden_config_states(tmp_path, payload, expected):
    root = _init_repo(tmp_path / "state")
    (root / ".ao").mkdir()
    if payload is not None:
        (root / ".ao" / "config.json").write_bytes(payload)
    _enroll_project(root, commit=False)

    state = cli._project_enrollment(str(root))

    assert state["state"] == expected
    if expected == "broken":
        assert cli.PROJECT_MARKER in state["detail"]
        assert "AO project state is missing/unreadable" in state["detail"]
        assert cli.PROJECT_INIT_COMMAND in state["detail"]
    else:
        assert state["source"] == "index"


_CONFIG_LIMIT = 1_048_576


def _sized_config(size):
    prefix = b'{"project":"fixture"}'
    assert size >= len(prefix)
    return prefix + (b" " * (size - len(prefix)))


def _nested_config(depth):
    assert depth >= 1
    return b'{"value":' + (b"[" * (depth - 1)) + b"0" + (b"]" * (depth - 1)) + b"}"


@pytest.mark.parametrize(
    ("case", "payload", "accepted", "problem_fragment"),
    (
        ("exact-byte-limit", _sized_config(_CONFIG_LIMIT), True, None),
        ("one-byte-over", _sized_config(_CONFIG_LIMIT + 1), False, "1,048,576-byte limit"),
        ("exact-depth-limit", _nested_config(64), True, None),
        ("one-level-over", _nested_config(65), False, "64-level container depth"),
    ),
    ids=("exact-byte-limit", "one-byte-over", "exact-depth-limit", "one-level-over"),
)
def test_project_config_resource_boundaries_are_shared_by_load_and_enforcement(
    tmp_path, capsys, case, payload, accepted, problem_fragment
):
    root = _init_repo(tmp_path / case)
    (root / ".ao").mkdir()
    (root / ".ao" / "config.json").write_bytes(payload)
    _enroll_project(root, commit=False)

    loaded = A.load_config(str(root))
    problem = cli._project_config_problem(str(root))
    state = cli._project_enrollment(str(root))

    if accepted:
        assert problem is None
        assert "_config_problem" not in loaded
        if case == "exact-depth-limit":
            assert "value" in loaded
        else:
            assert loaded["project"] == "fixture"
        assert state["state"] == "enrolled"
    else:
        assert problem_fragment in problem
        assert loaded["_config_problem"] == problem
        assert state["state"] == "broken"
        assert problem_fragment in state["detail"]
        assert cli.cmd_commit_check(loaded, SimpleNamespace()) == 1
        assert problem_fragment in _strip_colour(capsys.readouterr().out)


@pytest.mark.parametrize("payload", (b"{", b"[]", b"{}"))
def test_invalid_project_config_has_one_non_raising_pre_dispatch_result(
    tmp_path, payload
):
    root = _init_repo(tmp_path / "invalid-config")
    (root / ".ao").mkdir()
    (root / ".ao" / "config.json").write_bytes(payload)

    loaded = A.load_config(str(root))
    problem = cli._project_config_problem(str(root))

    assert problem is not None
    assert loaded["_config_problem"] == problem


def test_first_adoption_carries_staged_marker_into_execution_probe(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _commit_marker_absence(root)
    _enroll_project(root, commit=False)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0

    state = cli._project_enrollment(root)
    proof = cli._hook_execution_probe(cli._ao_hook_inventory(root))

    assert state["state"] == "enrolled"
    assert state["source"] == "index"
    assert proof["installed"] is True


def test_staged_marker_deletion_remains_enrolled_through_head(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    marker = _enroll_project(root)
    marker.unlink()
    _git(root, "add", "-u", "--", cli.PROJECT_MARKER)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0

    state = cli._project_enrollment(root)
    proof = cli._hook_execution_probe(cli._ao_hook_inventory(root))

    assert state["state"] == "enrolled"
    assert state["source"] == "head"
    assert proof["installed"] is True


def test_alternate_index_first_adoption_is_enrolled_and_probed(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _commit_marker_absence(root)
    marker = Path(root) / cli.PROJECT_MARKER
    marker.write_bytes(cli.PROJECT_MARKER_BYTES)
    alternate = tmp_path / "alternate-index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(alternate)
    _git(root, "read-tree", "HEAD", env=env)
    _git(root, "add", cli.PROJECT_MARKER, env=env)
    _git(root, "config", "core.hooksPath", ".githooks")
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_hooks(project, _args("install")) == 0
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))

    state = cli._project_enrollment(root)
    proof = cli._hook_execution_probe(cli._ao_hook_inventory(root))

    assert state["state"] == "enrolled"
    assert state["source"] == "index"
    assert proof["installed"] is True


def test_relative_alternate_index_resolves_from_repository_root(
    project, monkeypatch
):
    root = Path(project["root"])
    _commit_marker_absence(root)
    marker = root / cli.PROJECT_MARKER
    marker.write_bytes(cli.PROJECT_MARKER_BYTES)
    alternate = root / "relative-index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(alternate)
    _git(root, "read-tree", "HEAD", env=env)
    _git(root, "add", cli.PROJECT_MARKER, env=env)
    monkeypatch.setenv("GIT_INDEX_FILE", alternate.name)

    state = cli._project_enrollment(str(root))

    assert state["state"] == "enrolled"
    assert state["source"] == "index"
    assert state["index"]["oid"] == _git(
        root, "hash-object", cli.PROJECT_MARKER
    ).stdout.strip()


def test_empty_active_index_override_fails_closed(project, monkeypatch):
    root = project["root"]
    _enroll_project(root)
    monkeypatch.setenv("GIT_INDEX_FILE", "")

    state = cli._project_enrollment(root)

    assert state["state"] == "broken"
    assert "GIT_INDEX_FILE is set but empty" in state["detail"]


def test_wrong_staged_marker_cannot_disable_canonical_head(project):
    root = project["root"]
    marker = _enroll_project(root)
    marker.write_text("not-ao\n", encoding="utf-8")
    _git(root, "add", cli.PROJECT_MARKER)

    state = cli._project_enrollment(root)

    assert state["state"] == "broken"
    assert "active index marker bytes are not ao-project-v1" in state["detail"]


def test_linked_worktree_reads_canonical_head_marker(project, tmp_path):
    root = project["root"]
    _enroll_project(root)
    linked = tmp_path / "linked-marker"
    _git(root, "worktree", "add", "-q", "-b", "linked-marker", str(linked))
    (linked / ".ao").mkdir()
    (linked / ".ao" / "config.json").write_text(
        '{"project":"linked"}\n', encoding="utf-8"
    )

    state = cli._project_enrollment(str(linked))

    assert state["state"] == "enrolled"
    assert state["head"]["status"] == "canonical"


def test_public_clone_marker_without_state_refuses_with_init_remediation(
    tmp_path, capsys
):
    root = _init_repo(tmp_path / "public-clone")
    _enroll_project(root)
    cfg = _cfg(root)

    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    output = _strip_colour(capsys.readouterr().out)

    assert cli.PROJECT_MARKER in output
    assert "AO project state is missing/unreadable" in output
    assert cli.PROJECT_INIT_COMMAND in output
    assert "unconfigured" not in output


def test_exact_v2_hook_is_legacy_and_upgrades_to_v3(project):
    root = project["root"]
    path = Path(root) / ".git" / "hooks" / "pre-commit"
    path.write_bytes(cli._render_v2_local_hook("pre-commit", "."))
    path.chmod(0o755)

    before = _active(cli._ao_hook_inventory(root), "pre-commit")
    assert before["static_state"] == "legacy (behavior unverified)"
    assert cli.cmd_hooks(project, _args("install")) == 0
    assert path.read_bytes() == cli._render_local_hook("pre-commit", ".")
    assert b"ao-hook-v3" in path.read_bytes()


def test_init_writes_exact_unstaged_marker_and_refuses_wrong_reinit(
    tmp_path, capsys, monkeypatch
):
    root = _init_repo(tmp_path / "init-marker")
    cfg = _cfg(root)

    assert cli.cmd_init(cfg, _init_args(profile="claude-kiro")) == 0
    marker = root / cli.PROJECT_MARKER
    assert marker.read_bytes() == cli.PROJECT_MARKER_BYTES
    assert _git(
        root, "ls-files", "--error-unmatch", "--", cli.PROJECT_MARKER,
        check=False,
    ).returncode == 1
    manifest = (root / ".ao" / "init-manifest.json").read_text(encoding="utf-8")
    assert cli.PROJECT_MARKER in manifest

    marker.write_text("wrong-version\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.cmd_init(cfg, _init_args(profile="claude-kiro")) == 1
    output = _strip_colour(capsys.readouterr().out)
    assert cli.PROJECT_MARKER in output
    assert "AO project state is missing/unreadable" in output
    assert cli.PROJECT_INIT_COMMAND in output
    assert marker.read_text(encoding="utf-8") == "wrong-version\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
@pytest.mark.parametrize("relative", (".ao/config.json", cli.PROJECT_MARKER))
def test_init_refuses_broken_symlink_state_without_replacing_it(
    tmp_path, capsys, relative
):
    root = _init_repo(tmp_path / ("broken-" + relative.replace("/", "-")))
    link = root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    missing = root / "missing-target"
    os.symlink(str(missing), str(link))

    assert cli.cmd_init(_cfg(root), _init_args(profile="claude-kiro")) == 1
    output = _strip_colour(capsys.readouterr().out)

    assert link.is_symlink()
    assert os.readlink(link) == str(missing)
    assert not missing.exists()
    assert "AO project state is missing/unreadable" in output
    assert cli.PROJECT_INIT_COMMAND in output


def test_remove_is_two_phase_and_keeps_state_until_marker_commit(
    project, monkeypatch, capsys
):
    root = project["root"]
    marker = _enroll_project(root)
    real_run = subprocess.run

    def fake_watchdog(argv, *args, **kwargs):
        if "watchdog" in argv and "uninstall" in argv:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", fake_watchdog)
    args = SimpleNamespace(yes=True, allow_shared_hooks=False)

    assert cli.cmd_remove(project, args) == 0
    phase_one = _strip_colour(capsys.readouterr().out)
    assert "phase 1/2" in phase_one
    assert not marker.exists()
    assert Path(root, ".ao", "config.json").exists()
    assert cli._project_enrollment(root)["state"] == "enrolled"

    _git(root, "add", "-u", "--", cli.PROJECT_MARKER)
    _git(
        root,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "decommission ao marker",
    )
    assert cli._project_enrollment(root)["state"] == "uninitialized"

    assert cli.cmd_remove(project, args) == 0
    phase_two = _strip_colour(capsys.readouterr().out)
    assert "phase 2/2 complete" in phase_two
    assert not Path(root, ".ao").exists()




def test_complete_hook_probe_namespace_never_falls_through_to_live_commit(
    project, monkeypatch, capsys
):
    root = project["root"]
    nonce = "0" * 32
    index = os.path.abspath(os.path.join(root, ".git", "index"))
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("AO_HOOK_PROBE_NONCE", nonce)
    monkeypatch.setenv("AO_HOOK_PROBE_PATH", ".ao-hook-probe-" + nonce)
    monkeypatch.setenv("AO_HOOK_PROBE_HEAD", head)
    monkeypatch.setenv("AO_HOOK_PROBE_INDEX", index)
    monkeypatch.setenv("GIT_INDEX_FILE", index)

    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    output = _strip_colour(capsys.readouterr().out)

    assert "hook execution challenge does not match the synthetic index" in output
    assert "no recorded authority decision" not in output
    assert "AO-HOOK-PROBE-REFUSED" not in output


def test_direct_commit_check_resolves_relative_index_from_root_not_caller_cwd(
    project, tmp_path, monkeypatch, capsys
):
    root = Path(project["root"])
    _commit_marker_absence(root)
    marker = root / cli.PROJECT_MARKER
    marker.write_bytes(cli.PROJECT_MARKER_BYTES)
    alternate = root / "relative-direct-index"
    setup_env = os.environ.copy()
    setup_env["GIT_INDEX_FILE"] = str(alternate)
    _git(root, "read-tree", "HEAD", env=setup_env)
    _git(root, "add", cli.PROJECT_MARKER, env=setup_env)

    caller = tmp_path / "caller-cwd"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("GIT_INDEX_FILE", alternate.name)

    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    output = _strip_colour(capsys.readouterr().out)

    assert "no recorded authority decision" in output
    assert "active index marker query failed" not in output
    assert "AO project state is missing/unreadable" not in output


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_project_config_reader_rejects_symlink_with_one_shared_problem(project):
    root = Path(project["root"])
    config = root / ".ao" / "config.json"
    target = root / "config-target.json"
    target.write_text('{"project":"target"}\n', encoding="utf-8")
    config.unlink()
    config.symlink_to(target)

    document = A.project_config_document(str(root))
    loaded = A.load_config(str(root))

    assert document["problem"] == ".ao/config.json is not a regular file"
    assert loaded["_config_problem"] == document["problem"]
    assert cli._project_config_problem(str(root)) == document["problem"]


def test_init_revalidates_profile_output_before_writing_remaining_state(
    tmp_path, monkeypatch, capsys
):
    root = _init_repo(tmp_path / "init-profile-bound")

    def write_oversized_profile(profile_root, args):
        path = Path(profile_root) / ".ao" / "config.json"
        path.write_bytes(_sized_config(_CONFIG_LIMIT + 1))
        return ["reviewer"]

    monkeypatch.setattr(cli, "_apply_profile", write_oversized_profile)

    assert cli.cmd_init(_cfg(root), _init_args(profile="claude-kiro")) == 1
    output = _strip_colour(capsys.readouterr().out)

    assert "1,048,576-byte limit" in output
    assert (root / cli.PROJECT_MARKER).read_bytes() == cli.PROJECT_MARKER_BYTES
    assert _git(
        root, "ls-files", "--error-unmatch", "--", cli.PROJECT_MARKER,
        check=False,
    ).returncode == 1
    assert not (root / ".ao" / "board.md").exists()
