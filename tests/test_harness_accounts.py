import json
import shutil
import sqlite3
import time

import pytest

from ao import drivers, lib as A


def test_the_provider_an_actor_spends_is_the_one_its_adapter_declares():
    assert A.provider_of(["claude", "-p", "x"]) == "claude"
    assert A.provider_of(["/usr/local/bin/agent", "-p", "x"]) == "cursor"
    assert A.provider_of(["kiro-cli", "chat"]) is None
    assert A.window_headroom(None)[0] is None


def test_a_harness_declaring_the_usage_protocol_is_read_through_the_driver(project, tmp_path, monkeypatch):
    if not shutil.which("sqlite3"):
        pytest.skip("the sqlite3 CLI reads the token store")
    api = {"driver": "usage-limits", "endpoint": "https://usage.example/", "target": "Svc.GetUsageLimits",
           "content_type": "application/x-amz-json-1.0",
           "token": {"sqlite": "~/store/data.sqlite3", "table": "tokens", "key": "newcomer:token", "field": "access_token"},
           "profile": {"argv": ["newcomer", "whoami"], "prefix": "arn:newcomer"},
           "body": {"profileArn": "{profile}"}, "resource": "TOKENS", "login": ["newcomer", "login"]}
    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": {"id": "newcomer", "billing": {"api": api}}})
    (tmp_path / "home" / "store").mkdir(parents=True)
    db = sqlite3.connect(str(tmp_path / "home" / "store" / "data.sqlite3"))
    db.execute("CREATE TABLE tokens (key TEXT, value TEXT)")
    db.execute("INSERT INTO tokens VALUES (?, ?)", ("newcomer:token", json.dumps({"access_token": "t",
                                                                                  "expires_at": time.time() + 60})))
    db.commit()
    db.close()
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: [])

    assert A.usage_api("newcomer")["login"] == ["newcomer", "login"]
    assert A.account_usage(adapter_id="newcomer") == {
        "error": "newcomer is not on PATH or in the usual install directories"}
    assert set(drivers.USAGE) == {"usage-limits"}


def test_the_shipped_account_lookup_and_install_dirs_come_from_the_adapters():
    api = A.usage_api("kiro")
    assert api["driver"] == "usage-limits" and api["profile"]["argv"][1:] == ["whoami"]
    assert "~/.claude/local" in A._BIN_DIRS and A._BIN_DIRS[:4] == ("~/.local/bin", "~/bin", "/usr/local/bin",
                                                                   "/opt/homebrew/bin")
