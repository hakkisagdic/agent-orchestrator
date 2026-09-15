from ao import cli


def _fake_clock(monkeypatch, start=1000.0):
    clock = [start]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli, "_review_retry_wait", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


def _routes_that_never_finish(monkeypatch, clock, handed):
    def never_finishes(root, cand, prompt, timeout, strict, primary):
        handed.append(timeout)
        clock[0] += timeout + cli.REVIEW_KILL_DRAIN_SECONDS
        return cand["id"], "/agents/reviewer", "1.0", {
            "ok": False, "out": "", "returncode": None,
            "kind": "timeout", "retryable": True, "reason": "timed out",
        }

    monkeypatch.setattr(cli, "_reviewer_route_invocation", never_finishes)


def test_a_chain_of_routes_that_never_finish_stops_inside_its_budget(project, monkeypatch):
    clock = _fake_clock(monkeypatch)
    handed = []
    _routes_that_never_finish(monkeypatch, clock, handed)
    chain = [{"id": f"r{i}", "argv": ["reviewer"]} for i in range(4)]

    result = cli._invoke_reviewer_chain(project["root"], chain, "prompt", 100, False)

    assert result["used"] is None
    assert clock[0] - 1000.0 <= cli._review_chain_budget(100) == 300
    assert handed[0] == 100 and len(handed) == 3
    assert sorted(result["failures"]) == [0, 1, 2, 3]
    assert result["failures"][3]["reason"] == "not tried: the review chain budget of 300s was spent"
    assert result["failures"][0]["reason"].endswith("not retried: the review chain budget of 300s was spent")


def test_a_single_reviewer_keeps_its_full_timeout_and_its_one_retry(project, monkeypatch):
    clock = _fake_clock(monkeypatch)
    handed = []
    _routes_that_never_finish(monkeypatch, clock, handed)

    result = cli._invoke_reviewer_chain(project["root"], [{"id": "r0", "argv": ["reviewer"]}],
                                        "prompt", 900, False)

    assert result["used"] is None and handed == [900, 900]
    assert clock[0] - 1000.0 <= cli._review_chain_budget(900)


def test_doctor_states_the_worst_case_for_a_review_and_a_probe():
    assert cli._review_chain_budget(cli.REVIEW_TIMEOUT_DEFAULT) == 1900
    assert cli._review_budget_text() == (
        "a review takes at most 31m 40s and the reviewer probe at most 4m 40s, "
        "whatever the length of the chain")
