"""Tests for slice MODES: ao mode lite|full|show."""
import json
import os
import re
import stat
import subprocess
import sys
from types import SimpleNamespace

from ao import allowlist as AL, cli, lib as A, mcp as M, tiers as T
from tests.test_profiles import _init_args
from tests.test_prove import PROBE_OR_APPROVE


def _plain(capsys):
    res = capsys.readouterr()
    return re.sub(r"\x1b\[[0-9;]*m", "", res.out + res.err)


def _git_repo(root, with_marker=True):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@ao.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    paths = ["app.py"]
    if with_marker:
        with open(os.path.join(root, ".ao-project"), "wb") as fh:
            fh.write(cli.PROJECT_MARKER_BYTES)
        paths.append(".ao-project")
    subprocess.run(["git", "add", *paths], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _mode_args(action="show", by="", allow_shared_hooks=False):
    return SimpleNamespace(action=action, by=by, allow_shared_hooks=allow_shared_hooks)


def _make_fake_claude_reviewer(bin_dir):
    os.makedirs(bin_dir, exist_ok=True)
    body = (
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('codex 0.160.0')\n"
        "    raise SystemExit(0)\n"
        "stdin = '' if sys.stdin.isatty() else sys.stdin.read()\n"
        "p = next((a for a in (*sys.argv[1:], stdin) if 'Reviewer invocation probe' in a), stdin or sys.argv[-1])\n"
        "print(p.splitlines()[-1] if p.startswith('Reviewer invocation probe') else "
        "'VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')\n"
    )
    for name in ("codex", "claude"):
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, stat.S_IRWXU)
    return path


def test_mode_show_prints_mode_and_what_each_mode_does_and_does_not_protect(project, capsys):
    root = project["root"]
    cfg = dict(A.load_config(root), root=root)

    assert cli.cmd_mode(cfg, _mode_args("show")) == 0
    out = _plain(capsys)
    assert "mode: full — enforcement: on" in out
    assert "full mode:" in out and "protects:" in out and "does not protect:" in out
    assert "lite mode (enforcement: off - lite):" in out
    assert "no reviewer probe" in out
    assert "no commit hooks" in out
    assert "ao commit refuses because there is no gate to commit through" in out

    assert cli.cmd_mode(cfg, _mode_args("lite", by="Ada Lovelace")) == 0
    _plain(capsys)
    cfg_lite = dict(A.load_config(root), root=root)
    assert cli.cmd_mode(cfg_lite, _mode_args("show")) == 0
    out_lite = _plain(capsys)
    assert "mode: lite — enforcement: off - lite — nothing is enforced" in out_lite


def test_switching_to_lite_is_a_persons_act_recorded_and_refused_to_agents(project, monkeypatch, capsys):
    root = project["root"]
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)
    hook_path = os.path.join(root, ".git", "hooks", "pre-commit")
    assert os.path.exists(hook_path)

    cfg = dict(A.load_config(root), root=root)

    # Missing --by is refused.
    assert cli.cmd_mode(cfg, _mode_args("lite", by="")) == 2
    assert "--by is required" in _plain(capsys)

    # Agent or role names in --by are refused.
    for agent_name in ("implementer", "architect", "reviewer", "kiro", "fable"):
        assert cli.cmd_mode(cfg, _mode_args("lite", by=agent_name)) == 2
        assert "--by names an agent or a role" in _plain(capsys)

    # AO_ROLE is a caller-controlled environment variable, not proof of who invoked ao:
    # switching to lite relies on harness grants (allowlist.FORBIDDEN), --by validation, and record_opt_in.
    safety_doc = open(os.path.join(A.REPO, "docs", "safety.md"), encoding="utf-8").read()
    assert "relies on the harness grants" in safety_doc
    assert "refuses when `AO_ROLE` names an agent role" not in safety_doc
    monkeypatch.setenv("AO_ROLE", "implementer")
    assert cli.cmd_mode(cfg, _mode_args("lite", by="Ada Lovelace")) == 0
    _plain(capsys)
    monkeypatch.delenv("AO_ROLE", raising=False)
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("full")) == 0
    _plain(capsys)
    cfg = dict(A.load_config(root), root=root)

    # Allowlist forbids ao mode lite and ao config set mode lite for agent grants.
    assert any("ao mode lite" in sample for _, sample in AL.FORBIDDEN)
    assert any("ao config set mode lite" in sample for _, sample in AL.FORBIDDEN)
    assert AL.problems(["claude", "--allowedTools", "Bash(ao mode lite --by Ada)"])
    assert AL.problems(["claude", "--allowedTools", "Bash(ao mode:*)"])
    assert AL.problems(["claude", "--allowedTools", "Bash(ao config set mode lite:*)"])

    # A person switches the project to lite; hooks are removed and opt-in is recorded.
    assert cli.cmd_mode(cfg, _mode_args("lite", by="Ada Lovelace")) == 0
    out = _plain(capsys)
    assert "enforcement: off - lite" in out
    assert A.load_config(root)["mode"] == "lite"
    assert not os.path.exists(hook_path)

    rec = A.recorded_opt_in(root, "mode")
    assert rec and rec["setting"] == "mode" and rec["value"] == "lite" and rec["by"] == "Ada Lovelace"

    # Hand-editing .ao/config.json to "lite" without an opt-in record does not activate lite mode.
    fresh = os.path.join(root, "hand-edited")
    os.makedirs(os.path.join(fresh, ".ao"), exist_ok=True)
    with open(os.path.join(fresh, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(A.load_config(root), mode="lite"), fh)
    hand_cfg = dict(A.load_config(fresh), root=fresh)
    assert cli.project_mode(hand_cfg) == "full"
    assert cli.unrecorded_lite(hand_cfg)
    keys = dict(cli.doctor_problems(hand_cfg))
    assert "mode-opt-in" in keys and "no opt-in record" in keys["mode-opt-in"]


def test_every_surface_says_enforcement_off_lite_and_commit_refuses(project, monkeypatch, capsys):
    root = project["root"]
    cfg = dict(A.load_config(root), root=root)
    assert cli.cmd_mode(cfg, _mode_args("lite", by="Ada Lovelace")) == 0
    _plain(capsys)
    cfg = dict(A.load_config(root), root=root)

    # 1. ao status (text and --json)
    assert cli.cmd_status(cfg, SimpleNamespace(json=False, messages=6, window=24.0)) in (0, None)
    status_out = _plain(capsys)
    assert "enforcement: off - lite" in status_out and "nothing is enforced" in status_out

    assert cli.cmd_status(cfg, SimpleNamespace(json=True, messages=6, window=24.0)) in (0, None)
    status_json = json.loads(_plain(capsys))
    assert status_json["mode"] == "lite"
    assert "enforcement: off - lite" in status_json["enforcement"]
    assert "nothing is enforced" in status_json["enforcement"]

    # 2. MCP status payload
    mcp_status = M.status_payload(cfg)
    assert mcp_status["mode"] == "lite"
    assert "enforcement: off - lite" in mcp_status["enforcement"]
    assert "nothing is enforced" in mcp_status["enforcement"]

    # 3. ao doctor and ao doctor --check (runs no reviewer probe, requires no commit hooks)
    probed = []
    monkeypatch.setattr(cli, "_reviewer_probe", lambda *a, **kw: probed.append(1) or {"ok": False})
    assert cli.cmd_doctor(cfg, SimpleNamespace(check=False, notify=False)) == 0
    doc_out = _plain(capsys)
    assert "enforcement: off - lite" in doc_out and "nothing is enforced" in doc_out
    assert probed == []

    cli.cmd_doctor(cfg, SimpleNamespace(check=True, notify=False))
    check_out = _plain(capsys)
    assert "enforcement: off - lite" in check_out and "nothing is enforced" in check_out
    assert "commit-hook" not in check_out and "reviewer" not in check_out

    # 4. ao commit refuses in lite mode: there is no gate to commit through.
    assert cli.cmd_commit(cfg, SimpleNamespace(message=["try commit"])) == 1
    commit_out = _plain(capsys)
    assert "there is no gate to commit through in lite mode" in commit_out
    assert "enforcement: off - lite" in commit_out


def test_init_never_puts_project_into_lite_by_itself(tmp_path, monkeypatch, capsys):
    # Case 1: No reviewer binary available -> init refuses with nothing written and names `ao mode lite --by <name>`.
    repo1 = tmp_path / "no-reviewer-bin"
    repo1.mkdir()
    _git_repo(str(repo1), with_marker=False)
    assert cli.cmd_init({"root": str(repo1)}, _init_args(profile="agy-codex")) == 1
    out1 = _plain(capsys)
    assert "init refused: reviewer probe" in out1
    assert "ao mode lite --by <name>" in out1
    assert not (repo1 / ".ao").exists()
    assert not (repo1 / ".ao-project").exists()

    # Case 2: Reviewer is available, but foreign pre-commit hook present -> init refuses with nothing written.
    bin_dir = tmp_path / "bin"
    _make_fake_claude_reviewer(str(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)

    repo2 = tmp_path / "foreign-hook"
    repo2.mkdir()
    _git_repo(str(repo2), with_marker=False)
    hook_file = repo2 / ".git" / "hooks" / "pre-commit"
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text("#!/bin/sh\necho custom hook\n", encoding="utf-8")
    hook_file.chmod(stat.S_IRWXU)

    assert cli.cmd_init({"root": str(repo2)}, _init_args(profile="agy-codex")) == 1
    out2 = _plain(capsys)
    assert "init refused: commit hooks cannot be installed" in out2 and "foreign" in out2
    assert "ao mode lite --by <name>" in out2
    assert not (repo2 / ".ao").exists()
    assert not (repo2 / ".ao-project").exists()

    # Case 3: A person opts into lite first with `ao mode lite --by Ada`, then runs `ao init`.
    assert cli.cmd_mode({"root": str(repo1)}, _mode_args("lite", by="Ada")) == 0
    _plain(capsys)
    assert cli.cmd_init({"root": str(repo1)}, _init_args(profile="agy-codex")) == 0
    out3 = _plain(capsys)
    assert "enforcement: off - lite" in out3
    assert cli.project_mode(dict(A.load_config(str(repo1)), root=str(repo1))) == "lite"

    # Case 4: A ledger entry with `by: "ao init"` is never accepted as a valid lite opt-in.
    A.record_opt_in(str(repo1), "mode", "lite", "ao init")
    assert cli.project_mode(dict(A.load_config(str(repo1)), root=str(repo1))) == "full"


def test_mode_full_checks_requirements_refuses_without_half_switching_and_switches_when_ready(
        tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(str(repo), with_marker=False)

    hook_file = repo / ".git" / "hooks" / "pre-commit"
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    hook_file.chmod(stat.S_IRWXU)

    assert cli.cmd_mode({"root": str(repo)}, _mode_args("lite", by="Ada")) == 0
    _plain(capsys)
    assert cli.cmd_init({"root": str(repo)}, _init_args(profile="agy-codex")) == 0
    _plain(capsys)
    subprocess.run(["git", "add", ".ao-project"], cwd=repo, check=True)
    cfg = dict(A.load_config(str(repo)), root=str(repo))
    assert cli.project_mode(cfg) == "lite"

    # ao mode full refuses naming BOTH the missing reviewer and the foreign hook, and stays in lite.
    assert cli.cmd_mode(cfg, _mode_args("full")) == 1
    refusal = _plain(capsys)
    assert "mode full refused" in refusal
    assert "independent reviewer:" in refusal and "commit hooks:" in refusal
    assert cli.project_mode(dict(A.load_config(str(repo)), root=str(repo))) == "lite"

    # Provide the independent reviewer binary and remove the foreign hook; now ao mode full succeeds.
    bin_dir = tmp_path / "bin"
    _make_fake_claude_reviewer(str(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    hook_file.unlink()
    cfg = dict(A.load_config(str(repo)), root=str(repo))
    assert cli.cmd_mode(cfg, _mode_args("full")) == 0
    switched = _plain(capsys)
    assert "mode = 'full' — enforcement: on" in switched
    cfg_after = dict(A.load_config(str(repo)), root=str(repo))
    assert cli.project_mode(cfg_after) == "full"
    assert os.path.exists(str(hook_file))


def test_switching_to_lite_with_tracked_githooks_pre_commit_does_not_enforce_commits(project, capsys):
    root = project["root"]
    githooks = os.path.join(root, ".githooks")
    os.makedirs(githooks, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        hp = os.path.join(githooks, name)
        with open(hp, "wb") as fh:
            fh.write(cli._render_local_hook(name, ".", fallback=cli._hook_fallback()))
        os.chmod(hp, 0o755)
    subprocess.run(["git", "add", ".githooks/pre-commit", ".githooks/pre-push"], cwd=root, check=True)
    subprocess.run(["git", "-c", "core.hooksPath=", "commit", "-q", "-m", "track hooks"], cwd=root, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=root, check=True)

    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)

    # In full mode, the tracked .githooks/pre-commit runs `ao commit-check` and refuses without a grant.
    blocked = subprocess.run(["git", "commit", "-q", "-m", "blocked in full"], cwd=root, capture_output=True, text=True)
    assert blocked.returncode != 0

    # Switch to lite mode with a person opt-in; tracked .githooks/pre-commit remains on disk,
    # yet direct `git commit` and `ao push check` succeed without requiring a grant.
    cfg = dict(A.load_config(root), root=root)
    assert cli.cmd_mode(cfg, _mode_args("lite", by="Ada", allow_shared_hooks=True)) == 0
    _plain(capsys)
    assert os.path.exists(os.path.join(githooks, "pre-commit"))

    allowed = subprocess.run(["git", "commit", "-q", "-m", "allowed in lite"], cwd=root, capture_output=True, text=True)
    assert allowed.returncode == 0, allowed.stderr
    cfg_lite = dict(A.load_config(root), root=root)
    assert cli.cmd_push(cfg_lite, SimpleNamespace(action="check", minutes=30)) == 0


def test_mode_full_accepts_person_review_tier_without_model_reviewer(tmp_path, capsys):
    repo = tmp_path / "person-tier-repo"
    repo.mkdir()
    _git_repo(str(repo), with_marker=False)

    assert cli.cmd_mode({"root": str(repo)}, _mode_args("lite", by="Ada")) == 0
    _plain(capsys)
    assert cli.cmd_init(
        {"root": str(repo)},
        _init_args(profile=None, implementer="claude-code", review_tier="person"),
    ) == 0
    _plain(capsys)
    cfg = dict(A.load_config(str(repo)), root=str(repo))
    assert "reviewer" not in cfg
    assert cli.project_mode(cfg) == "lite"

    # Once `.ao-project` is staged, `ao mode full` accepts person review without a model reviewer.
    subprocess.run(["git", "add", ".ao-project"], cwd=repo, check=True)
    assert cli.cmd_mode(cfg, _mode_args("full")) == 0
    out = _plain(capsys)
    assert "mode = 'full' — enforcement: on" in out
    cfg_after = dict(A.load_config(str(repo)), root=str(repo))
    assert cli.project_mode(cfg_after) == "full"
    tier_text, problems = cli._active_review_tier(cfg_after)
    assert tier_text.startswith("person review:") and problems == []


def test_mode_full_refuses_when_hook_static_install_succeeds_but_execution_probe_fails(
        tmp_path, monkeypatch, capsys):
    repo = tmp_path / "unexecutable-hook-repo"
    repo.mkdir()
    _git_repo(str(repo), with_marker=False)

    bin_dir = tmp_path / "reviewer-bin"
    _make_fake_claude_reviewer(str(bin_dir))

    assert cli.cmd_mode({"root": str(repo)}, _mode_args("lite", by="Ada")) == 0
    _plain(capsys)
    assert cli.cmd_init(
        {"root": str(repo)},
        _init_args(profile="agy-codex"),
    ) == 0
    _plain(capsys)
    subprocess.run(["git", "add", ".ao-project"], cwd=repo, check=True)

    # Make the installed hook carry no machine fallback and remove `ao` from PATH so static install
    # succeeds (`current-local`), while Git's `/bin/sh` fails at execution time with `ao not found`.
    hook_file = repo / ".git" / "hooks" / "pre-commit"
    monkeypatch.setattr(cli, "_hook_fallback", lambda: None)
    clean_dirs = [str(bin_dir)] + [
        d for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not os.path.exists(os.path.join(d, "ao"))
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(clean_dirs))

    cfg = dict(A.load_config(str(repo)), root=str(repo))
    assert cli.cmd_mode(cfg, _mode_args("full")) == 1
    out = _plain(capsys)
    assert "mode full refused" in out
    assert "not installed" in out and "no ao on PATH" in out
    assert cli.project_mode(dict(A.load_config(str(repo)), root=str(repo))) == "lite"
    assert not hook_file.exists()


def test_forged_or_appended_opt_in_row_does_not_activate_lite_and_doctor_names_it(project, capsys):
    from ao import storage
    root = project["root"]
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)

    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)

    config_file = os.path.join(root, ".ao", "config.json")
    ledger_file = os.path.join(root, ".ao", "ledger", "opt-ins.jsonl")
    os.makedirs(os.path.dirname(ledger_file), exist_ok=True)
    updated_cfg = dict(A.project_config_document(root)["config"], mode="lite")
    with open(config_file, "w", encoding="utf-8") as fh:
        json.dump(updated_cfg, fh)

    # (a) A forged genesis `mode` row planted with no checkpoint does not activate lite;
    # the commit hook keeps checking and `ao doctor` names the unattested row.
    forged_genesis = {
        "previous": None,
        "ordinal": 1,
        "at": 1,
        "setting": "mode",
        "value": "lite",
        "by": "Ada",
        "user": "ada",
        "interactive": True,
    }
    with open(ledger_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(forged_genesis) + "\n")

    cfg_a = dict(A.load_config(root), root=root)
    assert cli.project_mode(cfg_a) == "full"
    assert cli.cmd_commit_check(cfg_a, SimpleNamespace(range=None)) == 1
    _plain(capsys)
    commit_a = subprocess.run(["git", "commit", "-q", "-m", "forged genesis"], cwd=root, capture_output=True, text=True)
    assert commit_a.returncode != 0
    problems_a = dict(cli.doctor_problems(cfg_a))
    assert "opt-ins-unattested" in problems_a
    assert "no row of it is ao's own" in problems_a["opt-ins-unattested"]
    assert cli.cmd_doctor(cfg_a, SimpleNamespace(check=True, notify=False)) == 1
    assert "opt-ins-unattested" in _plain(capsys)

    # (b) A `lite` row appended by hand after an attested `full` row does not activate lite;
    # the commit hook keeps checking and `ao doctor` names the unattested row.
    os.remove(ledger_file)
    A.record_opt_in(root, "mode", "full", "Ada")
    with open(ledger_file, encoding="utf-8") as fh:
        attested_rows = [json.loads(line) for line in fh if line.strip()]
    appended_lite = {
        "previous": storage.chained_row_digest(attested_rows[-1], A.OPT_IN_CHAIN),
        "ordinal": len(attested_rows) + 1,
        "at": 2,
        "setting": "mode",
        "value": "lite",
        "by": "Ada",
        "user": "ada",
        "interactive": True,
    }
    with open(ledger_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(appended_lite) + "\n")

    cfg_b = dict(A.load_config(root), root=root)
    assert cli.project_mode(cfg_b) == "full"
    assert cli.cmd_commit_check(cfg_b, SimpleNamespace(range=None)) == 1
    _plain(capsys)
    commit_b = subprocess.run(["git", "commit", "-q", "-m", "appended lite"], cwd=root, capture_output=True, text=True)
    assert commit_b.returncode != 0
    problems_b = dict(cli.doctor_problems(cfg_b))
    assert "opt-ins-unattested" in problems_b
    assert "removes exactly the last 1 line(s)" in problems_b["opt-ins-unattested"]
    assert cli.cmd_doctor(cfg_b, SimpleNamespace(check=True, notify=False)) == 1
    assert "opt-ins-unattested" in _plain(capsys)


def test_init_succeeds_with_shared_hooks_dir_and_refuses_when_hook_path_is_a_file(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    _make_fake_claude_reviewer(str(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)

    # 1. Repository whose `core.hooksPath` is a shared directory outside the repo:
    # full-mode `ao init` succeeds and names `ao hooks install --allow-shared-hooks` as the next step.
    shared_hooks = tmp_path / "shared-hooks"
    shared_hooks.mkdir()
    shared_repo = tmp_path / "shared-repo"
    shared_repo.mkdir()
    _git_repo(str(shared_repo), with_marker=False)
    subprocess.run(["git", "config", "core.hooksPath", str(shared_hooks)], cwd=shared_repo, check=True)

    assert cli.cmd_init({"root": str(shared_repo)}, _init_args(profile="agy-codex")) == 0
    shared_out = _plain(capsys)
    assert "ao hooks install --allow-shared-hooks" in shared_out
    assert (shared_repo / ".ao" / "config.json").exists()
    assert (shared_repo / ".ao-project").exists()

    # 2. Repository whose `core.hooksPath` is a regular file: full-mode `ao init` refuses with nothing written.
    hook_file = tmp_path / "hooks-file"
    hook_file.write_text("not a dir\n", encoding="utf-8")
    file_repo = tmp_path / "file-repo"
    file_repo.mkdir()
    _git_repo(str(file_repo), with_marker=False)
    subprocess.run(["git", "config", "core.hooksPath", str(hook_file)], cwd=file_repo, check=True)

    assert cli.cmd_init({"root": str(file_repo)}, _init_args(profile="agy-codex")) == 1
    file_out = _plain(capsys)
    assert "init refused: commit hooks cannot be installed" in file_out
    assert "not a directory" in file_out
    assert not (file_repo / ".ao").exists()
    assert not (file_repo / ".ao-project").exists()


def test_lite_opt_in_cannot_be_reused_after_config_leaves_lite(project, monkeypatch, capsys):
    root = project["root"]
    config_file = os.path.join(root, ".ao", "config.json")
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)

    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 2\n")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)

    def write_mode(mode_val):
        updated = dict(A.project_config_document(root)["config"], mode=mode_val)
        with open(config_file, "w", encoding="utf-8") as fh:
            json.dump(updated, fh)

    # Path 1: Hand edit back to `full`, followed by `"mode": "lite"` written back -> hook keeps checking.
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("lite", by="Ada")) == 0
    assert cli.cmd_hooks(dict(A.load_config(root), root=root), SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)
    write_mode("full")
    # Even if appending the `full` row fails on a read, the read still answers `full`.
    orig_record = A.record_opt_in
    monkeypatch.setattr(A, "record_opt_in", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    assert cli.project_mode(dict(A.load_config(root), root=root)) == "full"
    monkeypatch.setattr(A, "record_opt_in", orig_record)
    # Normal read while config is not `lite` spends the `lite` opt-in by appending `mode = full` by `ao`.
    assert cli.project_mode(dict(A.load_config(root), root=root)) == "full"
    spent = A.recorded_opt_in(root, "mode")
    assert spent and spent["value"] == "full" and spent["by"] == "ao"
    write_mode("lite")
    assert cli.project_mode(dict(A.load_config(root), root=root)) == "full"
    assert cli.cmd_commit_check(dict(A.load_config(root), root=root), SimpleNamespace(range=None)) == 1
    _plain(capsys)
    assert subprocess.run(["git", "commit", "-q", "-m", "after hand edit"], cwd=root, capture_output=True).returncode != 0

    # Path 2: `ao config set` refuses the `mode` key and names `ao mode lite --by <name>` / `ao mode full`.
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("lite", by="Ada")) == 0
    assert cli.cmd_hooks(dict(A.load_config(root), root=root), SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)
    for action, val in (("set", "full"), ("set", "lite"), ("unset", None)):
        rc = cli.cmd_config(
            dict(A.load_config(root), root=root),
            SimpleNamespace(action=action, key="mode", value=val, machine=False, by="Ada"),
        )
        assert rc == 2
        msg = _plain(capsys)
        assert "ao mode lite --by <name>" in msg and "ao mode full" in msg
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("full")) == 0
    _plain(capsys)
    write_mode("lite")
    assert cli.cmd_commit_check(dict(A.load_config(root), root=root), SimpleNamespace(range=None)) == 1
    _plain(capsys)
    assert subprocess.run(["git", "commit", "-q", "-m", "after config set"], cwd=root, capture_output=True).returncode != 0

    # Path 3: `ao mode full` records its opt-in row first and writes the config after;
    # if the record fails, nothing changes and the command fails; once the config leaves `lite`,
    # writing `"mode": "lite"` back still keeps the hook checking.
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("lite", by="Ada")) == 0
    _plain(capsys)
    hook_path = os.path.join(root, ".git", "hooks", "pre-commit")
    assert not os.path.exists(hook_path)
    monkeypatch.setattr(A, "record_opt_in", lambda *a, **kw: (_ for _ in ()).throw(OSError("ledger unwritable")))
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("full")) == 1
    _plain(capsys)
    monkeypatch.setattr(A, "record_opt_in", orig_record)
    assert A.project_config_document(root)["config"]["mode"] == "lite"
    assert not os.path.exists(hook_path)
    assert A.recorded_opt_in(root, "mode")["value"] == "lite"

    # And if the config write fails after `ao mode full` recorded `full`, or if the config was
    # already `full` when `record_opt_in` failed and `"mode": "lite"` is written back:
    assert cli.cmd_hooks(dict(A.load_config(root), root=root), SimpleNamespace(action="install", allow_shared_hooks=False)) == 0
    _plain(capsys)
    orig_write = A.write_project_config
    monkeypatch.setattr(A, "write_project_config", lambda *a, **kw: (_ for _ in ()).throw(OSError("config unwritable")))
    assert cli.cmd_mode(dict(A.load_config(root), root=root), _mode_args("full")) == 1
    _plain(capsys)
    monkeypatch.setattr(A, "write_project_config", orig_write)
    write_mode("lite")
    assert cli.project_mode(dict(A.load_config(root), root=root)) == "full"
    assert cli.cmd_commit_check(dict(A.load_config(root), root=root), SimpleNamespace(range=None)) == 1
    _plain(capsys)
    assert subprocess.run(["git", "commit", "-q", "-m", "after failed mode full"], cwd=root, capture_output=True).returncode != 0
