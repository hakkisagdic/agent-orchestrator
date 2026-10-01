from ao import lib as A


def test_hard_cap_refuses(project, monkeypatch):
    monkeypatch.setattr(A, "provider_window", lambda name="claude": {"pct": 4, "window": "5h", "resets_in": "4h", "resets_s": 14400})
    v = A.fanout_verdict(project["root"], project, 47)
    assert v["verdict"] == "too-many" and not v["ok"]


def test_ok_then_limit_hit_then_observed_estimate(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "provider_window", lambda name="claude": {"pct": 4, "window": "5h", "resets_in": "4h", "resets_s": 14400})
    assert A.fanout_verdict(root, project, 8)["ok"]
    A.record_fanout(root, 47, done=11, errors=36, tokens=1_962_027, note="session limit")
    v = A.fanout_verdict(root, project, 8)
    assert v["verdict"] == "limit-hit-recently"
    assert v["per_agent_source"] == "observed" and v["per_agent_tokens"] == 1_962_027 // 47


def test_low_window_refuses_and_unreadable_window_is_said(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "provider_window", lambda name="claude": {"pct": 80, "window": "5h", "resets_in": "1h", "resets_s": 3600})
    assert A.fanout_verdict(root, project, 3)["verdict"] == "window-low"
    monkeypatch.setattr(A, "provider_window", lambda name="claude": None)
    v = A.fanout_verdict(root, project, 3)
    assert v["ok"] and any("unreadable" in r for r in v["reasons"])


def test_the_window_is_named_for_the_provider_read_not_the_argument(project, monkeypatch, capsys):
    """HARNESS-ACCOUNTS-2: with no --provider the architect's provider's window was read, and the line named None."""
    from types import SimpleNamespace
    from ao import cli
    asked = []
    window = {"pct": 4, "window": "5h", "resets_in": "4h", "resets_s": 14400}
    monkeypatch.setattr(A, "provider_window", lambda name: asked.append(name) or window)
    args = SimpleNamespace(action="ok", agents=3, roots=None, per_root=None, per_agent_tokens=None, provider=None,
                           json=False, done=None, errors=None, tokens=None, note=None, limit=20)

    assert cli.cmd_fanout(project, args) == 0

    out = capsys.readouterr().out
    assert asked == ["claude"] and "claude window: 4% used" in out and "None window" not in out
    assert A.fanout_verdict(project["root"], project, 3)["provider"] == "claude"


def test_pipeline_bound_is_gated_not_the_root_count(project, monkeypatch, capsys):
    from types import SimpleNamespace
    from ao import cli
    monkeypatch.setattr(A, "provider_window", lambda name="claude": {"pct": 4, "window": "5h", "resets_in": "4h", "resets_s": 14400})
    args = SimpleNamespace(action="ok", agents=None, roots=7, per_root=12, per_agent_tokens=None, provider="claude", json=False,
                           done=None, errors=None, tokens=None, note=None, limit=20)
    assert cli.cmd_fanout(project, args) == 1
    assert "91 agents" in capsys.readouterr().out
