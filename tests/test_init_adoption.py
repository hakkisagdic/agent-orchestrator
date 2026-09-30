"""A project an older ao set up is adopted in one step, and keeps everything it holds (INIT-ADOPTION).

Such a project has AO state - a config, a board, ledgers of grants and decisions, a commit hook
that runs `ao commit-check` - and no `.ao-project` anywhere in its history. Its config governs its
commits, the current hooks stand aside while no marker is tracked, and `ao hooks install` refuses
it; every message about it spelled out a recipe of its own. `ao init --adopt` writes and stages
the exact marker, installs the current hooks, proves the commit hook by having Git run it, and
names the commit left to land, writing nothing under .ao/. Each test builds that project in a
temporary repository.
"""
import json
import os
import re
from pathlib import Path

import pytest

from ao import cli, lib as A
from tests.test_hook_paths import (
    SHARED_HOOKS_REFUSED, _git, _legacy_absolute_hook, _make_current_ao_available,
)

COMMIT = ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--no-verify")


def _legacy(tmp_path, monkeypatch):
    """A repository an older ao set up: config, board, two ledgers with rows, the old hook, and no marker ever."""
    root = tmp_path / "legacy"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, *COMMIT, "--allow-empty", "-m", "init")
    ledger = root / ".ao" / "ledger"
    ledger.mkdir(parents=True)
    cfg = {"project": "legacy", "mailbox": "agent-mail", "reviews": "semantic-review",
           "implementer": {"adapter": "kiro", "session": "s1", "name": "kiro"}}
    (root / ".ao" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / ".ao" / "board.md").write_text("# Board\n\n## running\n\n## queued\n\n## done\n", encoding="utf-8")
    (ledger / "authority.jsonl").write_text('{"granted": false, "token": "C-1"}\n', encoding="utf-8")
    (ledger / "decisions.jsonl").write_text('{"id": "D-1", "decision": "an epoch counter"}\n', encoding="utf-8")
    (root / "agent-mail").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(A, "HOME", str(home))
    from ao import watchdog as W
    monkeypatch.setattr(W, "STATE_DIR", str(home / ".ao"))
    return root, dict(cfg, root=str(root)), _legacy_absolute_hook(root)


def _init(*argv):
    """`ao init` with these options, as the parser hands them to the command."""
    return cli.build_parser().parse_args(["init", *argv])


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _ao_state(root):
    """Every file under .ao/ with its bytes: what adoption must leave exactly as it found it."""
    base = Path(root) / ".ao"
    return {path.relative_to(base).as_posix(): path.read_bytes() for path in sorted(base.rglob("*"))
            if path.is_file()}


def _staged(root):
    return _git(root, "diff", "--cached", "--name-only").stdout.split()


def test_adoption_enrolls_a_legacy_project_and_keeps_every_ledger_and_decision(tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    before = _ao_state(root)
    assert cli._project_enrollment(str(root))["state"] == "legacy"

    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    out = _plain(capsys)

    assert (root / cli.PROJECT_MARKER).read_bytes() == cli.PROJECT_MARKER_BYTES
    assert _staged(root) == [cli.PROJECT_MARKER]
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    state = cli._project_enrollment(str(root))
    assert (state["state"], state["source"]) == ("enrolled", "index")
    assert _ao_state(root) == before
    assert hook.read_bytes() == cli._render_local_hook("pre-commit", ".", cli._hook_fallback())
    assert "wrote  .ao-project (ao-project-v1)" in out and "staged .ao-project" in out
    assert "kept   .ao/ with its 2 ledger(s) (authority.jsonl, decisions.jsonl)" in out
    assert "commit hook installed (execution proved)" in out
    assert "land the staged .ao-project through the loop" in out


def test_adopting_again_changes_nothing_before_or_after_the_marker_lands(tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_init(cfg, _init("--adopt")) == 0

    def everything():
        return (_git(root, "ls-files", "--stage").stdout, (root / cli.PROJECT_MARKER).read_bytes(),
                hook.read_bytes(), _ao_state(root))

    first = everything()
    capsys.readouterr()
    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    again = _plain(capsys)
    assert everything() == first
    assert "kept   .ao-project (staged already)" in again
    assert "wrote" not in again and "installed pre-commit" not in again

    _git(root, *COMMIT, "-m", "adopt the ao marker")
    landed = everything()
    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    after = _plain(capsys)
    assert everything() == landed
    assert "kept   .ao-project (committed already)" in after
    assert "nothing to commit: .ao-project is in HEAD" in after


def test_adoption_never_replaces_a_marker_that_is_not_exact(tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    marker = root / cli.PROJECT_MARKER
    marker.write_text("ao-project-v0\n", encoding="utf-8")
    old_hook = hook.read_bytes()

    assert cli.cmd_init(cfg, _init("--adopt")) == 1

    assert "ao never replaces it" in _plain(capsys)
    assert marker.read_text(encoding="utf-8") == "ao-project-v0\n"
    assert _staged(root) == []
    assert hook.read_bytes() == old_hook
    assert cli._project_enrollment(str(root))["state"] == "legacy"


def test_a_staged_marker_that_does_not_read_back_is_unstaged_again(tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    # A clean filter stores other bytes than the file holds: the index would carry a marker
    # that is not ao-project-v1, which refuses every commit as broken state.
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "attributes").write_text(".ao-project filter=shout\n", encoding="utf-8")
    _git(root, "config", "filter.shout.clean", "tr a-z A-Z")
    old_hook = hook.read_bytes()

    assert cli.cmd_init(cfg, _init("--adopt")) == 1
    out = _plain(capsys)

    assert "does not enroll the project" in out and "unstaged again" in out
    assert _staged(root) == []
    assert cli._project_enrollment(str(root))["state"] == "legacy"
    assert hook.read_bytes() == old_hook


def test_adoption_refuses_a_repository_that_has_no_ao_state(tmp_path, capsys):
    root = tmp_path / "plain"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, *COMMIT, "--allow-empty", "-m", "init")

    assert cli.cmd_init({"root": str(root)}, _init("--adopt")) == 1

    assert "no .ao/config.json here" in _plain(capsys)
    assert not (root / cli.PROJECT_MARKER).exists()
    assert _staged(root) == []


def test_adoption_leaves_a_completed_removal_removed(tmp_path, monkeypatch, capsys):
    root, cfg, _ = _legacy(tmp_path, monkeypatch)
    (root / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
    _git(root, "add", cli.PROJECT_MARKER)
    _git(root, *COMMIT, "-m", "enroll ao")
    _git(root, "rm", "-q", "--", cli.PROJECT_MARKER)
    _git(root, *COMMIT, "-m", "decommission ao")

    assert cli.cmd_init(cfg, _init("--adopt")) == 1

    assert "its removal is committed" in _plain(capsys)
    assert not (root / cli.PROJECT_MARKER).exists()
    assert cli._project_enrollment(str(root))["state"] == "uninitialized"


@pytest.mark.parametrize("argv", (("--adopt", "--profile", "claude-kiro"), ("--allow-shared-hooks",)))
def test_options_that_do_not_belong_together_are_refused_before_anything_is_written(
        tmp_path, monkeypatch, capsys, argv):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    before, old_hook = _ao_state(root), hook.read_bytes()

    assert cli.cmd_init(cfg, _init(*argv)) == 1

    assert "init refused" in _plain(capsys)
    assert not (root / cli.PROJECT_MARKER).exists()
    assert _staged(root) == []
    assert (_ao_state(root), hook.read_bytes()) == (before, old_hook)


def test_a_pre_push_hook_of_the_projects_own_is_kept_and_does_not_fail_the_adoption(tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    pre_push = hook.parent / "pre-push"
    pre_push.write_text("#!/bin/sh\necho the project's own checks\n", encoding="utf-8")
    pre_push.chmod(0o755)

    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    out = _plain(capsys)

    assert pre_push.read_text(encoding="utf-8") == "#!/bin/sh\necho the project's own checks\n"
    assert "preserved foreign pre-push" in out
    assert "commit hook installed (execution proved)" in out


@pytest.mark.skipif(os.name == "nt", reason=SHARED_HOOKS_REFUSED)
def test_where_git_shares_the_hooks_adoption_asks_for_the_authorization_and_finishes_with_it(
        tmp_path, monkeypatch, capsys):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    # A linked worktree runs the same .git/hooks, so writing there changes what another checkout runs.
    _git(root, "worktree", "add", "-q", "--detach", str(tmp_path / "linked"))
    old_hook = hook.read_bytes()

    assert cli.cmd_init(cfg, _init("--adopt")) == 1
    refused = _plain(capsys)
    assert hook.read_bytes() == old_hook
    assert _staged(root) == [cli.PROJECT_MARKER]
    assert "ao init --adopt --allow-shared-hooks" in refused

    assert cli.cmd_init(cfg, _init("--adopt", "--allow-shared-hooks")) == 0
    assert "commit hook installed (execution proved)" in _plain(capsys)
    assert b"ao-hook-v4" in hook.read_bytes()


def test_doctor_names_the_adoption_on_the_commit_hook_row(tmp_path, monkeypatch, capsys):
    root, cfg, _ = _legacy(tmp_path, monkeypatch)
    # A doctor that finds ao on an agent's PATH goes on to ask agent binaries their version; the row
    # this reads comes before that, and no agent CLI is started for it.
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)

    cli.cmd_doctor(cfg, cli.build_parser().parse_args(["doctor"]))

    # Whatever the old hook is read as, the row that shows it names the one step.
    assert re.search(r"^commit hook\s+\S.* / untracked  no \.ao-project tracked yet — ao init --adopt$",
                     _plain(capsys), re.M)


def test_doctor_names_the_adoption_once_where_a_current_hook_stands_aside(tmp_path, monkeypatch):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    hook.write_bytes(cli._render_local_hook("pre-commit", "."))

    finding = dict(cli.doctor_problems(cfg))["commit-hook"]

    assert finding.count(cli.PROJECT_ADOPT_COMMAND) == 1
    assert finding.startswith("execution proof failed: no .ao-project has ever been tracked here")


def test_once_adopted_the_doctor_has_nothing_to_say_about_the_commit_hook(tmp_path, monkeypatch):
    root, cfg, _ = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    assert "commit-hook" in dict(cli.doctor_problems(cfg))

    assert cli.cmd_init(cfg, _init("--adopt")) == 0

    assert "commit-hook" not in dict(cli.doctor_problems(cfg))


def test_prove_sends_a_legacy_project_to_adoption_rather_than_to_a_refused_install(tmp_path, monkeypatch, capsys):
    _, cfg, _ = _legacy(tmp_path, monkeypatch)

    assert cli.cmd_prove(cfg, cli.build_parser().parse_args(["prove", "--no-review"])) == 1
    out = _plain(capsys)

    assert cli.PROJECT_ADOPT_COMMAND in out
    assert "— ao hooks install" not in out
