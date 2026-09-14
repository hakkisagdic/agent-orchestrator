import os
import re
import shutil
import stat
import subprocess
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


def _active(inv, role):
    return next(t for t in inv["targets"] if t["active"] and t["role"] == role)


def _strip_colour(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


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


def test_doctor_has_commit_problems_but_never_a_push_alarm(project, tmp_path):
    root = project["root"]
    keys = [key for key, _ in cli.doctor_problems(project)]
    assert "commit-hook" in keys
    assert not any("push-hook" in key for key in keys)

    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked", str(linked))
    assert cli.cmd_hooks(project, _args("install", allow=True)) == 0
    keys = [key for key, _ in cli.doctor_problems(project)]
    assert "commit-hook" not in keys
    assert "commit-hook-routing-unverified" in keys
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
