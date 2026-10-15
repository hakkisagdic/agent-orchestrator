"""Pre-review found tool reviewers and urgent or normalized mail misclassified.

Doctor must distinguish file tools from tree readers, urgency must follow inbox
routing, reviewer addresses must survive note slugging, and a machine config
replacement must not be reported unchanged (TRIAGE-3-LOW).
"""
import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, mcp, settings as S, storage


@pytest.mark.parametrize("adapter, model, family", [
    ("openai-api", "local/glm-5.3", "zhipu"),
    ("pr-agent", "gpt-5", "openai"),
])
@pytest.mark.parametrize("fallback", [False, True], ids=["primary", "fallback"])
def test_doctor_accepts_composed_file_tool_reviewers(project, adapter, model, family, fallback):
    route = A.compose_reviewer(adapter, model=model, family=family)
    primary = dict(A.compose_reviewer("claude-code", model="m"), fallbacks=[route]) if fallback else route
    cfg = dict(project, reviewer=primary)
    assert cli._tool_invocation(route, "review this", b"candidate")[1] is None
    assert not [problem for problem in cli._actor_grant_problems(cfg) if problem[0].startswith("reviewer-tools:")]


@pytest.mark.parametrize("adapter", ["openai-api", "pr-agent"])
def test_a_file_tool_command_without_the_tool_route_kind_is_still_named(project, adapter):
    route = A.compose_reviewer(adapter, model="m", family="other")
    route.pop("kind")
    assert any(key.startswith("reviewer-tools:") and "tool reviewer" in text
               for key, text in cli._actor_grant_problems(dict(project, reviewer=route)))


@pytest.mark.parametrize("urgent", [True, False], ids=["urgent-flag", "urgent-heading"])
def test_reviewer_urgency_does_not_block_an_implementer_that_cannot_ack_it(project, urgent):
    cfg = dict(project, reviewer={"id": "judge"})
    body = "review context" if urgent else "## URGENT\nreview context"
    assert cli.cmd_note(cfg, SimpleNamespace(title="read this", body=body, stdin=False,
                                            to="reviewer", urgent=urgent)) == 0
    root = cfg["root"]
    [name] = A.mailbox(root, cfg["mailbox"])
    assert mcp.call("ao_inbox", {}, cfg, False, "implementer")["messages"] == []
    assert "not addressed to the implementer" in mcp.call("ao_ack", {"id": name}, cfg, False, "implementer")["error"]
    assert A.urgent_messages(root, cfg) == []
    assert [(message["id"], message["to"]) for message in A.urgent_messages(root, cfg, None)] == [(name, "reviewer")]
    mine = A.note(root, cfg, "implementer", "implementer action", "act on this", urgent=True)
    assert [message["id"] for message in A.urgent_messages(root, cfg)] == [mine]
    assert mcp.call("ao_ack", {"id": mine}, cfg, False, "implementer")["acknowledged"] == mine
    assert A.urgent_messages(root, cfg) == []


@pytest.mark.parametrize("model", ["local/glm-5.3", "local/" + "long-model-name-" * 5], ids=["slash", "long-id"])
def test_composed_reviewer_addresses_survive_note_slugging(project, model):
    route = A.compose_reviewer("openai-api", model=model, family="zhipu")
    cfg = dict(project, reviewer=dict(route, actor=route["id"]))
    name = A.note(cfg["root"], cfg, route["id"], "read this", "context")
    meta = A.mail_meta(os.path.join(cfg["root"], cfg["mailbox"], name))
    assert meta["to"] == A.safe_slug(route["id"])
    assert meta.get("to_role") == "reviewer"
    assert A.role_of(route["id"], cfg) == "reviewer"
    assert A.to_reviewer(name, cfg)
    assert A.implementer_inbox(cfg["root"], cfg) == []


def test_machine_config_reports_replacement_when_directory_sync_fails(project, monkeypatch, capsys):
    real = storage.replace_file_durably

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "directory sync failed")
        os.fsync(fd)

    def write(path, data, **kwargs):
        return real(path, data, _fsync=fsync, **kwargs)

    # Windows cannot open a directory for fsync; inject at that boundary (TRIAGE-3-LOW).
    if os.name == "nt":
        def directory_sync(directory, sync):
            raise OSError(errno.EIO, "directory sync failed")
        monkeypatch.setattr(storage, "_sync_directory", directory_sync)
    monkeypatch.setattr(storage, "replace_file_durably", write)
    args = SimpleNamespace(action="set", key="keyflip.rotation", value="on", machine=True)
    assert cli.cmd_config(project, args) == 1
    assert Path(S.machine_path()).is_file() and S.machine_settings()["keyflip"]["rotation"] == "on"
    out = capsys.readouterr().out
    assert "not changed" not in out
    assert "changed" in out and "durability could not be confirmed" in out
