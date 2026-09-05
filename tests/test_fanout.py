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
