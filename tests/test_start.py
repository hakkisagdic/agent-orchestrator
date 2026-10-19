"""Tests for `ao start`: first-run onboarding wizard (ONBOARD, ONBOARD-2).

All tests use stand-in adapters and binaries (`tests/standin.py`), never real agent runs.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A
from tests import standin


REVIEW_STANDIN = """import sys
args = sys.argv[1:]
if "--version" in args or "-v" in args:
    print("standin 1.0.0")
    sys.exit(0)
for arg in args:
    if "Reviewer invocation probe" in arg:
        print(arg.strip().splitlines()[-1])
        sys.exit(0)
print("VERDICT: APPROVED")
print("BLOCKER: 0")
print("HIGH: 0")
print("MEDIUM: 0")
print("LOW: 0")
print()
print("## Findings")
print("None.")
"""


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _adapter(adapter_id, binary, family, verified="full", trust_flag="--no-tools"):
    return {
        "contract": A.ADAPTER_CONTRACT,
        "id": adapter_id,
        "name": f"Stand-in {adapter_id}",
        "actor_name": adapter_id,
        "family": family,
        "verified": verified,
        "detect": {
            "binaries": [binary],
            "dirs": [],
            "processes": [binary],
        },
        "models": {
            "family": family,
            "default": f"{family}-default",
            "review": f"{family}-review",
        },
        "send": {
            "argv": [binary, "run", "--prompt", "{prompt}"],
        },
        "resume": {
            "argv": [binary, "resume", "{session}", "--prompt", "{prompt}"],
        },
        "options": {
            "model": ["--model", "{model}"],
            "trust_none": [trust_flag],
            "allowed_tools": ["--allowed-tools", "{tools}"],
            "architect_tools": "read,shell(ao *)",
        },
        "directives": {
            "rule_files": ["AGENTS.md"],
        },
    }


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(root), check=True)
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "app.py").write_text("def inc(x):\n    return x + 1\n", encoding="utf-8")
    (root / "test_app.py").write_text(
        "import unittest\nfrom app import inc\n\n"
        "class AppTest(unittest.TestCase):\n"
        "    def test_inc(self):\n"
        "        self.assertEqual(inc(1), 2)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n",
        encoding="utf-8",
    )
    (root / ".ao").mkdir()
    cmd = (subprocess.list2cmdline if os.name == "nt" else shlex.join)(
        [sys.executable, "-m", "unittest", "-q"]
    )
    (root / ".ao" / "gates.json").write_text(
        json.dumps(
            {
                "gates": {"unit": {"run": cmd, "expect": "exit_zero", "timeout": 60}},
                "profiles": {"quick": ["unit"], "full": ["unit"]},
                "default_profile": "quick",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore", "app.py", "test_app.py", ".ao/gates.json"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=str(root), check=True)
    return str(root)


def test_start_detects_installed_clis_prefers_measured_verified_and_explains_why(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    for name in ("alpha-cli", "beta-cli", "gamma-cli"):
        standin.program(bin_dir, name, REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="partial", trust_flag="--no-tools"),
        "gamma": _adapter("gamma", "gamma-cli", "fam-gamma", verified="documented", trust_flag="--sandbox"),
        "absent": _adapter("absent", "absent-cli", "fam-delta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda root=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    installed, absent = cli._start_detect_adapters()
    assert [row["id"] for row in installed] == ["alpha", "beta", "gamma"]
    assert [row["id"] for row in absent] == ["absent"]
    assert {row["id"]: row["family"] for row in installed} == {
        "alpha": "fam-alpha",
        "beta": "fam-beta",
        "gamma": "fam-gamma",
    }

    pairs = cli._start_pairs(str(tmp_path), installed)
    pair_names = [(p["implementer"]["id"], p["reviewer"]["id"]) for p in pairs]
    # Same-family pairs are never offered by ao's independence rule.
    assert all(i != r for i, r in pair_names)
    # Measured verified pairs (full + partial: alpha/beta and beta/alpha) rank ahead of documented (gamma).
    assert set(pair_names[:2]) == {("alpha", "beta"), ("beta", "alpha")}
    # Between (alpha=full, beta=partial) and (beta=partial, alpha=full), the full reviewer comes first.
    assert pair_names[0] == ("beta", "alpha")
    assert "different model families" in pairs[0]["why"]
    assert "both adapters have measured verified levels" in pairs[0]["why"]
    assert "reviews read-only (--read-only)" in pairs[0]["why"]


def test_start_refuses_when_no_cross_family_pair_exists_and_installs_nothing(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "solo-cli", REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "solo-a": _adapter("solo-a", "solo-cli", "fam-solo", verified="full"),
        "solo-b": _adapter("solo-b", "solo-cli", "fam-solo", verified="full"),
        "missing": _adapter("missing", "no-such-binary-xyz", "fam-other", verified="full"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda root=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    code = cli.cmd_start({"root": str(tmp_path)}, SimpleNamespace(yes=True))
    assert code == 1
    out = _plain(capsys)
    assert "no cross-family implementer/reviewer pair is available" in out
    assert "ao never installs software" in out
    assert not os.path.exists(A.user_profiles_path())


def test_start_asks_before_each_step_and_writes_chosen_profile(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "alpha-cli", REVIEW_STANDIN)
    standin.program(bin_dir, "beta-cli", REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda root=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    ran = []
    monkeypatch.setattr(cli, "cmd_demo", lambda cfg, a: ran.append("demo") or 0)
    monkeypatch.setattr(cli, "cmd_init", lambda cfg, a: ran.append(("init", a.profile)) or 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: ran.append("hooks") or 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: ran.append("prove") or 0)

    # Choose pair #2 (beta-alpha), say yes to demo and init, no to hooks install and prove.
    answers = iter(["2", "y", "y", "n", "n"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=False)) == 0
    out = _plain(capsys)

    assert ran == ["demo", ("init", "beta-alpha")]
    assert "skipped `ao hooks install`" in out
    assert "skipped `ao prove`" in out
    assert "Steps only a person can take:" in out
    assert "ao harness seen --ask" in out

    # Profile is written to profiles.json and known to A.profiles() and `ao init --profile`.
    assert os.path.isfile(A.user_profiles_path())
    profiles = A.profiles(root)
    assert profiles["beta-alpha"] == {"implementer": "beta", "reviewer": "alpha", "architect": "beta"}
    assert A.default_profile(root) == "beta-alpha"
    parser = cli.build_parser(root)
    parsed = parser.parse_args(["init", "--profile", "beta-alpha"])
    assert parsed.profile == "beta-alpha"


def test_start_yes_runs_demo_init_hooks_and_prove_without_recording_person_approval(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "alpha-cli", REVIEW_STANDIN)
    standin.program(bin_dir, "beta-cli", REVIEW_STANDIN)
    standin.program(
        bin_dir,
        "ao",
        "import sys\nfrom ao.cli import main\nsys.exit(main())\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    demo_calls = []

    def fast_demo(cfg, a):
        demo_calls.append(bool(getattr(a, "keep", False)))
        return 0

    monkeypatch.setattr(cli, "cmd_demo", fast_demo)

    # An agent-modified rule file created after init baseline would be unseen; ensure start never calls record_harness_seen.
    seen_calls = []
    monkeypatch.setattr(A, "record_harness_seen", lambda *a, **kw: seen_calls.append((a, kw)))

    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 0
    out = _plain(capsys)

    assert demo_calls == [False]
    assert seen_calls == []
    assert os.path.isfile(os.path.join(root, ".ao", "config.json"))
    assert os.path.isfile(os.path.join(root, ".ao-project"))
    for claim in (
        "hook refuses an unauthorised commit",
        "reviewer answers and is another actor",
        "a throwaway slice lands end to end",
    ):
        assert f"proven  {claim}" in out
    assert "Steps only a person can take:" in out
    assert "ao harness seen --ask" in out


def test_start_assigns_only_restricted_architect_command(tmp_path, monkeypatch, capsys):
    from ao import allowlist as AL

    root = _repo(tmp_path)
    no_arch = tmp_path / "no_arch"
    no_arch.mkdir()
    root2 = _repo(no_arch)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "amp", REVIEW_STANDIN)
    standin.program(bin_dir, "claude", REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    real_pkg = A.package_adapters()
    shipped_subset = {k: real_pkg[k] for k in ("amp", "claude-code")}
    monkeypatch.setattr(A, "package_adapters", lambda: shipped_subset)
    monkeypatch.setattr(cli, "cmd_demo", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: 0)

    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 0

    profiles = A.profiles(root)
    assert profiles["amp-claude-code"] == {
        "implementer": "amp",
        "reviewer": "claude-code",
        "architect": "claude-code",
    }
    saved = A.load_config(root)
    assert saved["implementer"]["adapter"] == "amp"
    assert A.block_adapter(saved["reviewer"]) == "claude-code"
    assert saved["architect"]["adapter"] == "claude-code"
    arch_argv = saved["architect"]["argv"]
    claude_opts = real_pkg["claude-code"]["options"]
    assert AL.problems(arch_argv, claude_opts, role="architect") == []
    assert A.turn_refusal("architect", "claude-code", arch_argv) is None

    # When no installed adapter has a restricted architect command, ao start omits architect
    # rather than writing an unrestricted wake command.
    unrestricted_impl = _adapter("unrestricted", "amp", "fam-impl", verified="partial", trust_flag=None)
    unrestricted_impl["options"] = {"trust_none": None, "trust_all": ["--dangerously-allow-all"]}
    review_only = _adapter("revonly", "claude", "fam-rev", verified="full", trust_flag="--read-only")
    review_only["options"] = {"model": ["--model", "{model}"], "trust_none": ["--read-only"]}
    custom = {"unrestricted": unrestricted_impl, "revonly": review_only}
    monkeypatch.setattr(A, "package_adapters", lambda: custom)
    monkeypatch.setattr(A, "turn_adapter", lambda aid: custom.get(aid))
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in custom.items()
    })

    cfg2 = A.load_config(root2)
    assert cli.cmd_start(cfg2, SimpleNamespace(yes=True)) == 0
    profiles2 = A.profiles(root2)
    assert profiles2["unrestricted-revonly"] == {
        "implementer": "unrestricted",
        "reviewer": "revonly",
    }
    saved2 = A.load_config(root2)
    assert "architect" not in saved2


def test_start_refuses_profiles_json_symlink_without_truncating_target(tmp_path, monkeypatch, capsys):
    import pytest
    from tests.test_leads_adapters import _can_link

    _can_link(tmp_path)
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "alpha-cli", REVIEW_STANDIN)
    standin.program(bin_dir, "beta-cli", REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    ran = []
    monkeypatch.setattr(cli, "cmd_demo", lambda cfg, a: ran.append("demo") or 0)
    monkeypatch.setattr(cli, "cmd_init", lambda cfg, a: ran.append("init") or 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: ran.append("hooks") or 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: ran.append("prove") or 0)

    authorized_keys = tmp_path / "authorized_keys"
    key_material = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterial person@example\n"
    authorized_keys.write_text(key_material, encoding="utf-8")

    in_tree_adapters = os.path.join(root, ".ao", "adapters")
    os.makedirs(in_tree_adapters, exist_ok=True)
    link_path = os.path.join(in_tree_adapters, "profiles.json")
    os.symlink(str(authorized_keys), link_path)
    monkeypatch.setenv("AO_USER_ADAPTERS", in_tree_adapters)

    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 1
    out = _plain(capsys)
    assert "start refused" in out
    assert authorized_keys.read_text(encoding="utf-8") == key_material
    assert os.path.islink(link_path)
    assert ran == ["demo"]

    with pytest.raises(OSError):
        A.write_profile("beta-alpha", {"implementer": "beta", "reviewer": "alpha"})
    assert authorized_keys.read_text(encoding="utf-8") == key_material
    assert os.path.islink(link_path)

    # A dangling profiles.json symlink and a symlinked adapter folder are refused too.
    os.unlink(link_path)
    dangling_target = tmp_path / "dangling_keys"
    os.symlink(str(dangling_target), link_path)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 1
    assert not dangling_target.exists()

    os.unlink(link_path)
    os.rmdir(in_tree_adapters)
    outside_dir = tmp_path / "outside_adapters"
    outside_dir.mkdir()
    outside_profiles = outside_dir / "profiles.json"
    outside_profiles.write_text('{"init": "keep"}\n', encoding="utf-8")
    os.symlink(str(outside_dir), in_tree_adapters, target_is_directory=True)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 1
    assert outside_profiles.read_text(encoding="utf-8") == '{"init": "keep"}\n'


def test_start_ignores_project_local_profiles_json_and_uses_selected_pair(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    for name in ("alpha-cli", "beta-cli", "gamma-cli"):
        standin.program(bin_dir, name, REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
        "gamma": _adapter("gamma", "gamma-cli", "fam-gamma", verified="documented", trust_flag="--sandbox"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "turn_adapter", lambda aid: adapters.get(aid))
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })
    monkeypatch.setattr(cli, "cmd_demo", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: 0)

    # A project-local .ao/adapters/profiles.json maps alpha-beta to gamma/beta instead of alpha/beta.
    project_adapters = os.path.join(root, ".ao", "adapters")
    os.makedirs(project_adapters, exist_ok=True)
    with open(os.path.join(project_adapters, "profiles.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "init": "gamma-beta",
                "profiles": {
                    "alpha-beta": {"implementer": "gamma", "reviewer": "beta", "architect": "gamma"},
                    "gamma-beta": {"implementer": "gamma", "reviewer": "beta", "architect": "gamma"},
                },
            },
            fh,
        )

    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 0

    saved = A.load_config(root)
    assert saved["implementer"]["adapter"] == "alpha"
    assert A.block_adapter(saved["reviewer"]) == "beta"
    assert saved["architect"]["adapter"] == "alpha"
    assert A.profiles()["alpha-beta"] == {"implementer": "alpha", "reviewer": "beta", "architect": "alpha"}
    assert A.default_profile() == "alpha-beta"
    assert A.profiles(root)["gamma-beta"] == {"implementer": "gamma", "reviewer": "beta", "architect": "gamma"}
    assert A.default_profile(root) == "gamma-beta"


def test_project_local_profiles_json_drives_init_and_review_fallback(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    for name in ("alpha-cli", "beta-cli", "gamma-cli"):
        standin.program(bin_dir, name, REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
        "gamma": _adapter("gamma", "gamma-cli", "fam-gamma", verified="documented", trust_flag="--sandbox"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "turn_adapter", lambda aid: adapters.get(aid))
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    project_adapters = os.path.join(root, ".ao", "adapters")
    os.makedirs(project_adapters, exist_ok=True)
    with open(os.path.join(project_adapters, "profiles.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "init": "gamma-beta",
                "profiles": {
                    "gamma-beta": {"implementer": "gamma", "reviewer": "beta", "architect": "gamma"},
                },
            },
            fh,
        )

    assert "gamma-beta" in A.profiles(root)
    assert A.default_profile(root) == "gamma-beta"

    parser = cli.build_parser(root)
    parsed = parser.parse_args(["init", "--profile", "gamma-beta", "--no-mcp"])
    assert parsed.profile == "gamma-beta"

    # Unconfigured-reviewer fallback in cmd_review reads the project's default_profile(root).
    assert cli.cmd_review(
        {"root": root, "project": "demo"},
        SimpleNamespace(
            range_revs=None, range_slice=None, boundary=None, pinned=False,
            carried=None, person=None, author=None,
        ),
    ) == 1
    review_out = _plain(capsys)
    assert "beta-cli" in review_out

    assert cli.cmd_init({"root": root}, parsed) == 0
    _plain(capsys)
    saved = A.load_config(root)
    assert saved["implementer"]["adapter"] == "gamma"
    assert A.block_adapter(saved["reviewer"]) == "beta"
    assert saved["architect"]["adapter"] == "gamma"


def test_start_does_not_write_user_profile_when_cmd_init_refuses(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "alpha-cli", REVIEW_STANDIN)
    standin.program(
        bin_dir,
        "beta-cli",
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('standin-agent 1.0.0')\n"
        "    raise SystemExit(0)\n"
        "print('probe refused', file=sys.stderr)\n"
        "raise SystemExit(17)\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "turn_adapter", lambda aid: adapters.get(aid))
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })
    monkeypatch.setattr(cli, "cmd_demo", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: 0)

    # 1. No prior user profiles.json: failed cmd_init leaves no profiles.json behind.
    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 1
    out = _plain(capsys)
    assert "init refused: reviewer probe" in out
    assert "wrote profile" not in out
    assert not os.path.exists(A.user_profiles_path())

    # 2. Existing user profiles.json: failed cmd_init leaves machine-wide init default untouched.
    A.write_profile("beta-alpha", {"implementer": "beta", "reviewer": "alpha", "architect": "beta"}, default=True)
    with open(A.user_profiles_path(), "rb") as fh:
        before_bytes = fh.read()
    assert A.default_profile() == "beta-alpha"

    assert cli.cmd_start(cfg, SimpleNamespace(yes=True)) == 1
    out = _plain(capsys)
    assert "init refused: reviewer probe" in out
    assert "wrote profile" not in out
    with open(A.user_profiles_path(), "rb") as fh:
        assert fh.read() == before_bytes
    assert A.default_profile() == "beta-alpha"


def test_start_writes_nothing_before_init_step_is_confirmed(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    bin_dir = tmp_path / "bin"
    standin.program(bin_dir, "alpha-cli", REVIEW_STANDIN)
    standin.program(bin_dir, "beta-cli", REVIEW_STANDIN)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    adapters = {
        "alpha": _adapter("alpha", "alpha-cli", "fam-alpha", verified="full", trust_flag="--read-only"),
        "beta": _adapter("beta", "beta-cli", "fam-beta", verified="full", trust_flag="--no-tools"),
    }
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setattr(A, "adapter_catalog", lambda r=None: {
        k: {"id": k, "source": "package", "path": k, "adapter": v, "problem": None}
        for k, v in adapters.items()
    })

    ran = []

    def check_demo(cfg, a):
        assert not os.path.exists(A.user_profiles_path())
        ran.append("demo")
        return 0

    monkeypatch.setattr(cli, "cmd_demo", check_demo)
    monkeypatch.setattr(cli, "cmd_init", lambda cfg, a: ran.append("init") or 0)
    monkeypatch.setattr(cli, "cmd_hooks", lambda cfg, a: ran.append("hooks") or 0)
    monkeypatch.setattr(cli, "cmd_prove", lambda cfg, a: ran.append("prove") or 0)

    # With no terminal and no --yes, no person can confirm any step: profiles.json is untouched.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    cfg = A.load_config(root)
    assert cli.cmd_start(cfg, SimpleNamespace(yes=False)) == 0
    out = _plain(capsys)
    assert "skipped `ao demo`" in out
    assert "skipped `ao init --profile alpha-beta`" in out
    assert ran == []
    assert not os.path.exists(A.user_profiles_path())
    assert not os.path.exists(os.path.join(root, ".ao", "config.json"))

    # At a terminal, saying yes to demo and no to init still writes no profile.
    answers = iter(["1", "y", "n", "n", "n"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.cmd_start(cfg, SimpleNamespace(yes=False)) == 0
    assert ran == ["demo"]
    assert not os.path.exists(A.user_profiles_path())
    assert not os.path.exists(os.path.join(root, ".ao", "config.json"))
