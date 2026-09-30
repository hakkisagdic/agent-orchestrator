"""What the retrospective review of REVIEWER-IDENTITY found that still held (REVIEWER-IDENTITY-2).

A reviewer runs as the implementer when its command carries the implementer's session id, and that
was read only as a whole argument or after `=`: `sh -c "kiro-cli chat --resume-id <id>"` and `-r<id>`
ran unseen. The `auto` branch, where the session is discovered in the harness's store, had no test,
and a store whose metadata held no JSON object stopped `ao review` with a traceback.
"""
import json

import pytest

from ao import cli, lib as A

LONG = "0b7f2c9e-4d1a-4c3e-9a55-1f2e3d4c5b6a"


@pytest.mark.parametrize("argv, sessions, runs_as_it", [
    (["sh", "-c", "kiro-cli chat --resume-id s1 --no-interactive"], {"s1"}, True),
    (["sh", "-c", "kiro-cli chat --resume-id=s1"], {"s1"}, True),
    (["claude", f"-r{LONG}", "-p"], {LONG}, True),
    (["claude", "--transcript", f"/sessions/{LONG}.jsonl"], {LONG}, True),
    (["kiro-cli", "chat", "--resume-id", "s1"], {"s1"}, True),
    (["kiro-cli", "chat", "--resume-id", "s10"], {"s1"}, False),         # a short id is never a substring
    (["sh", "-c", "kiro-cli chat --resume-id s10"], {"s1"}, False),
    (["sh", "-c", "echo 'unbalanced"], {"s1"}, False),                    # not a shell string: read as words
])
def test_a_reviewer_that_carries_the_implementers_session_is_the_implementer(argv, sessions, runs_as_it):
    assert cli._reviewer_is_implementer({"id": "independent", "argv": argv}, sessions) is runs_as_it


def _auto(project, monkeypatch, discovered):
    monkeypatch.setattr(A, "load_adapter", lambda ident, root=None: {"sessions": {"kind": "workspace-meta"}})
    monkeypatch.setattr(A, "discover_session", discovered)
    return dict(project, implementer={"adapter": "kiro", "session": "auto"})


def test_an_auto_implementer_is_known_by_the_session_its_store_holds_for_the_project(project, monkeypatch):
    cfg = _auto(project, monkeypatch, lambda cwd, exclude=(): {"session": "s-found"})

    assert cli._implementer_sessions(cfg) == {"s-found"}
    assert cli._reviewer_is_implementer({"id": "r", "argv": ["kiro-cli", "--resume-id", "s-found"]},
                                        cli._implementer_sessions(cfg))


def test_a_store_that_cannot_be_read_names_no_session_and_stops_nothing(project, monkeypatch):
    def unreadable(cwd, exclude=()):
        raise OSError("the store went away")

    assert cli._implementer_sessions(_auto(project, monkeypatch, unreadable)) == set()


def test_a_session_whose_metadata_holds_no_json_object_is_not_in_the_store(tmp_path):
    store = {"dir": str(tmp_path / "sessions"), "meta": "meta.json", "transcript": "messages.jsonl",
             "workspaces": "paths"}
    for name, meta in (("kept", {"paths": ["/p"]}), ("list", [1]), ("null", None)):
        session = tmp_path / "sessions" / "ws" / name
        session.mkdir(parents=True)
        (session / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (session / "messages.jsonl").write_text("{}\n", encoding="utf-8")

    assert [row["session"] for row in A._workspace_sessions(store)] == ["kept"]
