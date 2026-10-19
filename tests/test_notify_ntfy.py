import http.server
import json
import os
import secrets
import stat
import threading

import pytest

from ao import cli, language, lib as A, ntfy, telegram, watchdog as W


class _NtfyRecorder:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = b'{"id":"msg1"}'
        self.headers = {"Content-Type": "application/json"}
        recorder = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                recorder.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "raw_body": raw,
                    "body": raw.decode("utf-8", errors="replace"),
                })
                self.send_response(recorder.status)
                for k, v in recorder.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(recorder.body)

            def log_message(self, fmt, *args):
                return

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, topic="secret-topic-xyz"):
        return f"http://127.0.0.1:{self.port}/{topic}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def ntfy_server():
    srv = _NtfyRecorder()
    try:
        yield srv
    finally:
        srv.close()


@pytest.fixture(autouse=True)
def _isolate_ntfy_conf(tmp_path, monkeypatch):
    conf = tmp_path / "ntfy-home" / ".ao" / "ntfy.json"
    monkeypatch.setattr(ntfy, "CONF", str(conf))
    return conf


def test_setup_writes_owner_only_config_and_never_prints_token(project, ntfy_server, monkeypatch, capsys):
    secret = "tk_" + secrets.token_hex(12)
    topic = "my-private-topic-42"
    monkeypatch.setenv("TEST_NTFY_TOKEN", secret)

    rc = cli.main(["-C", project["root"], "ntfy", "setup",
                   "--topic-url", ntfy_server.url(topic), "--token-env", "TEST_NTFY_TOKEN"])
    out, err = capsys.readouterr()

    assert rc == 0
    assert secret not in out and secret not in err
    assert topic not in out and topic not in err
    assert os.path.isfile(ntfy.CONF)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(ntfy.CONF).st_mode) == 0o600
    saved = json.loads(open(ntfy.CONF, encoding="utf-8").read())
    assert saved == {"topic_url": ntfy_server.url(topic), "token": secret}


def test_setup_rejects_token_passed_on_command_line_without_echoing_it(project, ntfy_server, capsys):
    secret = "tk_" + secrets.token_hex(12)
    rc = cli.main(["-C", project["root"], "ntfy", "setup",
                   "--topic-url", ntfy_server.url(), "--token", secret])
    out, err = capsys.readouterr()

    assert rc != 0
    assert secret not in out and secret not in err
    assert "--token-env" in (out + err)
    assert not os.path.exists(ntfy.CONF)


def test_setup_validates_url_and_token_env(project, monkeypatch, capsys):
    root = project["root"]
    assert cli.main(["-C", root, "ntfy", "setup", "--topic-url", "http://ntfy.sh/mytopic"]) != 0
    assert cli.main(["-C", root, "ntfy", "setup", "--topic-url", "https://ntfy.sh/"]) != 0
    assert cli.main(["-C", root, "ntfy", "setup", "--topic-url", "https://user:pass@ntfy.sh/mytopic"]) != 0
    monkeypatch.delenv("MISSING_NTFY_TOKEN", raising=False)
    assert cli.main(["-C", root, "ntfy", "setup", "--topic-url", "https://ntfy.sh/mytopic",
                     "--token-env", "MISSING_NTFY_TOKEN"]) != 0
    capsys.readouterr()
    assert not os.path.exists(ntfy.CONF)


def test_status_and_test_command_never_print_token_or_topic(project, ntfy_server, monkeypatch, capsys):
    root = project["root"]
    assert cli.main(["-C", root, "ntfy", "status"]) == 1
    out, _ = capsys.readouterr()
    assert "not configured" in out

    secret = "tk_" + secrets.token_hex(12)
    topic = "secret-topic-status"
    monkeypatch.setenv("TEST_NTFY_TOKEN", secret)
    assert cli.main(["-C", root, "ntfy", "setup",
                     "--topic-url", ntfy_server.url(topic), "--token-env", "TEST_NTFY_TOKEN"]) == 0
    capsys.readouterr()

    assert cli.main(["-C", root, "ntfy", "status"]) == 0
    out, err = capsys.readouterr()
    assert "configured" in out and "host=127.0.0.1" in out
    assert secret not in out and secret not in err
    assert topic not in out and topic not in err

    assert cli.main(["-C", root, "ntfy", "test"]) == 0
    out, err = capsys.readouterr()
    assert "sent" in out
    assert secret not in out and secret not in err
    assert len(ntfy_server.requests) == 1
    req = ntfy_server.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == f"/{topic}"
    assert req["body"] == language.text(project, "ntfy.test", name=project["project"])
    assert req["headers"]["priority"] == "default"
    assert req["headers"]["authorization"] == f"Bearer {secret}"


def test_watchdog_sends_same_text_as_telegram_with_mapped_priority(project, ntfy_server, monkeypatch):
    secret = "tk_" + secrets.token_hex(12)
    monkeypatch.setenv("TEST_NTFY_TOKEN", secret)
    ntfy.save(ntfy_server.url("wd-topic"), secret)

    tg_messages = []
    monkeypatch.setattr(telegram, "send", lambda text, root=None: tg_messages.append(text) or True)

    W.notify("cap hit", "daily cap reached", root=project["root"], key="cap", audience="human", level="red")
    W.notify("review warning", "two rounds stuck", root=project["root"], key="rev", audience="human", level="orange")
    W.notify("heads up", "minor drift", root=project["root"], key="drift", audience="human", level="yellow")
    W._tell_phone(project, "implementer finished slice")

    assert len(ntfy_server.requests) == 4
    assert [r["body"] for r in ntfy_server.requests] == tg_messages
    assert [r["headers"]["priority"] for r in ntfy_server.requests] == [
        "urgent",
        "high",
        "default",
        "default",
    ]
    for r in ntfy_server.requests:
        assert r["headers"]["authorization"] == f"Bearer {secret}"


def test_failed_send_records_notice_without_leaking_secrets_and_never_raises(project, ntfy_server, monkeypatch):
    secret = "tk_" + secrets.token_hex(12)
    topic = "secret-topic-fail"
    ntfy.save(ntfy_server.url(topic), secret)
    monkeypatch.setattr(telegram, "send", lambda text, root=None: False)

    ntfy_server.status = 500
    ntfy_server.body = f"error for {secret} on {topic}".encode("utf-8")

    # Must not raise in watchdog.notify or direct ntfy.send
    W.notify("cap hit", "daily cap reached", root=project["root"], key="cap", audience="human", level="red")

    notices = A.notices(project["root"], 10**9, include_suppressed=True)
    failed = [n for n in notices if n.get("title") == "ntfy send failed"]
    assert len(failed) == 1
    assert failed[0]["sent"] is False
    assert secret not in json.dumps(failed[0])
    assert topic not in json.dumps(failed[0])

    # Redirects must also be refused so Authorization is never forwarded
    ntfy_server.status = 302
    ntfy_server.headers = {"Location": ntfy_server.url("other")}
    assert ntfy.send("redirect test", root=project["root"]) is False


def test_send_truncates_at_utf8_character_boundary_within_4096_bytes(project, ntfy_server):
    """A multibyte alert under 4,000 characters can still exceed ntfy's 4,096-byte message limit (NOTIFY-NTFY-2)."""
    ntfy.save(ntfy_server.url("utf8-topic"))

    cases = [
        # 2,500 Turkish characters (2 bytes each = 5,000 bytes) with a 1-byte prefix so byte 4,096 splits a character.
        ("a" + ("ş" * 2500), "a" + ("ş" * 2047), 4095),
        # 3-byte characters ('—' is 3 bytes) where byte 4,096 splits after 2 continuation bytes.
        ("ab" + ("—" * 1500), "ab" + ("—" * 1364), 4094),
        # 4-byte emoji ('🚨' is 4 bytes) split at 1, 2 and 3 bytes into the character at the 4,096-byte boundary.
        ("a" + ("🚨" * 1100), "a" + ("🚨" * 1023), 4093),
        ("ab" + ("🚨" * 1100), "ab" + ("🚨" * 1023), 4094),
        ("abc" + ("🚨" * 1100), "abc" + ("🚨" * 1023), 4095),
        # Exact 4,096-byte multibyte payload is sent whole without dropping the last character.
        ("ş" * 2048, "ş" * 2048, 4096),
    ]
    for message, expected_text, expected_bytes in cases:
        ntfy_server.requests.clear()
        assert ntfy.send(message, root=project["root"]) is True
        assert len(ntfy_server.requests) == 1
        raw = ntfy_server.requests[0]["raw_body"]
        assert len(raw) == expected_bytes <= 4096
        assert raw.decode("utf-8") == expected_text
