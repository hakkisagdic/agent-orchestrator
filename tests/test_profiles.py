import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from ao import cli


def _bare(root):
    json.dump({"project": "proj", "round_budget": 5}, open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8"))


def test_profile_writes_role_blocks_and_keeps_existing(project):
    root = project["root"]
    _bare(root)
    args = SimpleNamespace(profile="claude-claude", implementer=None, model=None, effort=None, reviewer_model=None)
    assert cli._apply_profile(root, args) == ["implementer", "reviewer", "architect"]
    cfg = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))
    assert cfg["implementer"]["adapter"] == "claude-code" and cfg["implementer"]["model"] == "claude-sonnet-5"
    assert "--model" in cfg["reviewer"]["argv"] and "claude-opus-5" in cfg["reviewer"]["argv"]
    grant = cfg["architect"]["argv"][-1]
    assert cfg["architect"]["session"] == "auto" and "Bash(ao status:*)" in grant and "Bash(ao:*)" not in grant
    # second run: nothing overwritten
    cfg["implementer"]["model"] = "custom"
    json.dump(cfg, open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8"))
    assert cli._apply_profile(root, args) == []
    assert json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))["implementer"]["model"] == "custom"


def test_kiro_profile_with_effort(project):
    root = project["root"]
    _bare(root)
    args = SimpleNamespace(profile="claude-kiro", implementer=None, model=None, effort="high", reviewer_model=None)
    cli._apply_profile(root, args)
    cfg = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))
    assert cfg["implementer"] == {"adapter": "kiro", "session": "auto", "name": "kiro", "effort": "high"}



def _init_args(**overrides):
    values = {
        "name": None,
        "profile": "claude-kiro",
        "implementer": None,
        "model": None,
        "effort": None,
        "reviewer_model": None,
        "agent": "auto",
        "no_mcp": True,
        "rules": False,
        "watchdog": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _tree_state(root):
    """Byte/type/mode snapshot, including empty directories and Git state."""
    root = Path(root)
    state = {}
    entries = sorted(
        root.rglob("*"),
        key=lambda entry: os.fsencode(str(entry.relative_to(root))),
    )
    for entry in entries:
        rel = str(entry.relative_to(root))
        mode = entry.lstat().st_mode
        if entry.is_symlink():
            value = ("symlink", mode, os.readlink(entry))
        elif entry.is_dir():
            value = ("directory", mode, None)
        else:
            value = ("file", mode, entry.read_bytes())
        state[rel] = value
    return state


def test_init_plans_profile_and_failed_reviewer_probe_has_no_side_effects(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "fresh-init"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("owner-rule\n", encoding="utf-8")
    before = _tree_state(root)
    seen = []

    def failed_probe(cfg, timeout=cli.REVIEW_PROBE_TIMEOUT):
        seen.append(cfg)
        return {
            "configured": True,
            "ok": False,
            "route": "claude-reviewer",
            "binary": "/usr/local/bin/claude",
            "version": "1.0",
            "reason": "exited 17",
            "kind": "nonzero-exit",
        }

    monkeypatch.setattr(cli, "_reviewer_probe", failed_probe)

    assert cli.cmd_init(
        {"root": str(root)},
        _init_args(agent="kiro", no_mcp=False),
    ) == 1
    output = capsys.readouterr().out

    assert len(seen) == 1
    assert seen[0]["root"] == str(root)
    assert seen[0]["reviewer"]["argv"][0] == "claude"
    assert "/usr/local/bin/claude" in output
    assert "exited 17" in output
    assert _tree_state(root) == before
    assert (root / ".gitignore").read_text(encoding="utf-8") == "owner-rule\n"
    for relative in (
        ".ao",
        cli.PROJECT_MARKER,
        ".githooks",
        ".kiro",
        ".mcp.json",
        "agent-mail",
        "semantic-review",
    ):
        assert not (root / relative).exists()


def test_manual_doctor_probes_reviewer_but_scheduled_check_does_not(
    project, monkeypatch
):
    cfg = dict(
        project,
        reviewer={"id": "r1", "family": "x", "argv": ["reviewer", "{prompt}"]},
    )
    calls = []

    def failed_probe(probe_cfg, timeout=cli.REVIEW_PROBE_TIMEOUT):
        calls.append(probe_cfg["root"])
        return {
            "configured": True,
            "ok": False,
            "route": "r1",
            "binary": None,
            "version": None,
            "reason": "not installed",
            "kind": "missing-binary",
        }

    monkeypatch.setattr(cli, "_reviewer_probe", failed_probe)
    monkeypatch.setattr(cli, "_doctor_check", lambda probe_cfg: 0)

    assert cli.cmd_doctor(cfg, SimpleNamespace(check=True)) == 0
    assert calls == []
    assert cli.cmd_doctor(cfg, SimpleNamespace(check=False)) == 1
    assert calls == [project["root"]]



def test_init_rejects_oversized_planned_profile_before_probe_or_write(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "oversized-planned-profile"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    before = _tree_state(root)
    real_profile_config = cli._profile_config
    probed = []

    def oversized_profile(profile_root, args, base):
        planned, added = real_profile_config(profile_root, args, base)
        planned["reviewer"]["_why"] = "x" * 1_048_576
        return planned, added

    monkeypatch.setattr(cli, "_profile_config", oversized_profile)
    monkeypatch.setattr(
        cli,
        "_reviewer_probe",
        lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: probed.append(cfg),
    )

    assert cli.cmd_init({"root": str(root)}, _init_args()) == 1
    output = capsys.readouterr().out

    assert "1,048,576-byte limit" in output
    assert probed == []
    assert _tree_state(root) == before
    assert not (root / cli.PROJECT_MARKER).exists()
    assert subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", cli.PROJECT_MARKER],
        cwd=root,
        capture_output=True,
    ).returncode == 1
    assert not (root / ".ao" / "board.md").exists()



def test_init_real_failed_probe_contains_version_discovery_side_effects(
    tmp_path, monkeypatch, capsys,
):
    root = tmp_path / "real-probe-init"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    tracked = root / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Fixture", "-c",
            "user.email=fixture@example.invalid", "commit", "-q", "-m", "base",
        ],
        cwd=root,
        check=True,
    )
    tracked.write_text("unstaged owner change\n", encoding="utf-8")

    binary = tmp_path / "ao59-side-effect-reviewer"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "Path('reviewer-local-write').write_text('x', encoding='utf-8')\n"
        "subprocess.run(['git', 'add', '--', 'tracked.txt'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "if '--version' in sys.argv:\n"
        "    print('fixture reviewer 9.8.7')\n"
        "    raise SystemExit(0)\n"
        "print('fixture probe refused', file=sys.stderr)\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)

    def planned_profile(_root, _args, base):
        planned = dict(base)
        planned["reviewer"] = {
            "id": "fixture-reviewer",
            "family": "fixture",
            "argv": [str(binary), "{prompt}"],
        }
        return planned, ["reviewer"]

    monkeypatch.setattr(cli, "_profile_config", planned_profile)
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    monkeypatch.setenv("GIT_INDEX_FILE", str(root / ".git" / "index"))
    before = _tree_state(root)

    assert cli.cmd_init({"root": str(root)}, _init_args()) == 1
    output = capsys.readouterr().out

    assert str(binary) in output
    assert "exited 17" in output
    assert _tree_state(root) == before
    assert tracked.read_text(encoding="utf-8") == "unstaged owner change\n"
    assert not (root / "reviewer-local-write").exists()
    assert not (root / ".ao").exists()
    assert not (root / cli.PROJECT_MARKER).exists()



def test_init_revalidates_marker_and_config_after_successful_probe(
    tmp_path, monkeypatch, capsys,
):
    for changed in ("marker", "config"):
        root = tmp_path / ("probe-race-" + changed)
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

        def successful_probe(_cfg, timeout=cli.REVIEW_PROBE_TIMEOUT):
            if changed == "marker":
                (root / cli.PROJECT_MARKER).write_bytes(
                    cli.PROJECT_MARKER_BYTES
                )
            else:
                config = root / ".ao" / "config.json"
                config.parent.mkdir()
                config.write_text('{"project":"other"}\n', encoding="utf-8")
            return {
                "configured": True,
                "ok": True,
                "route": "fixture-reviewer",
                "binary": "/fixture/reviewer",
                "version": "1.0.0",
                "reason": "exact nonce echoed",
                "kind": "success",
            }

        monkeypatch.setattr(cli, "_reviewer_probe", successful_probe)

        assert cli.cmd_init({"root": str(root)}, _init_args()) == 1
        output = capsys.readouterr().out

        expected = (
            ".ao-project changed while reviewer probe ran"
            if changed == "marker"
            else ".ao/config.json appeared while reviewer probe ran"
        )
        assert expected in output
        assert not (root / ".ao" / "board.md").exists()
        assert not (root / ".ao" / "backlog.md").exists()
        assert not (root / ".ao" / "authority.md").exists()
        assert not (root / ".githooks").exists()
        assert not (root / ".gitignore").exists()
        if changed == "marker":
            assert (root / cli.PROJECT_MARKER).read_bytes() == (
                cli.PROJECT_MARKER_BYTES
            )
            assert not (root / ".ao").exists()
        else:
            assert not (root / cli.PROJECT_MARKER).exists()
            assert (root / ".ao" / "config.json").read_text(
                encoding="utf-8"
            ) == '{"project":"other"}\n'