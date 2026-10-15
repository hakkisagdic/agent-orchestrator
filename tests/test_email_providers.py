"""Mail providers send through saved settings; malformed provider names disable sending (TRIAGE-2-LOW)."""
import json
import os
import stat
from types import SimpleNamespace

import pytest

from ao import cli, email


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "email.json"
    monkeypatch.setattr(email, "CONF", str(path))
    return path


class _Server:
    sent = []

    def __init__(self, host, port, timeout=0):
        self.calls = [("connect", host, port)]
        _Server.instance = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        _Server.sent.append(message)


def test_smtp_sends_through_the_users_own_server(conf):
    email.save_provider("smtp", host="smtp.example.test", user="me@example.test", password="pw",
                        to="me@example.test")
    _Server.sent = []

    assert email.send("red", "the queue is empty", root=None, opener=_Server) is True

    (message,) = _Server.sent
    assert message["Subject"] == "[ao/ao] red" and message["To"] == "me@example.test"
    assert _Server.instance.calls == [("connect", "smtp.example.test", 587), ("starttls",),
                                      ("login", "me@example.test", "pw")]
    if os.name == "nt":
        pytest.skip("os.chmod sets only the read-only flag on Windows, so an owner-only mode cannot be "
                    "checked there (#71)")
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600


def test_a_provider_that_fails_or_is_unknown_is_false_not_an_error(conf):
    def refuses(host, port, timeout=0):
        raise OSError("connection refused")

    email.save_provider("smtp", host="smtp.example.test", to="me@example.test")
    assert email.send("x", "y", opener=refuses) is False

    conf.write_text(json.dumps({"provider": "pigeon", "to": "me@example.test"}))
    assert email.config() is None and email.send("x", "y") is False

    with pytest.raises(ValueError):
        email.save_provider("smtp", to="me@example.test")


def test_ao_email_setup_saves_smtp_with_the_password_from_the_environment(project, conf, monkeypatch, capsys):
    args = SimpleNamespace(action="setup", provider="smtp", token=None, to="me@example.test",
                           host="smtp.example.test", port=None, user="me@example.test",
                           password_env="AO_TEST_SMTP_PASSWORD", sender=None, tls=None)
    assert cli.cmd_email(project, args) == 2
    assert "AO_TEST_SMTP_PASSWORD is not set" in capsys.readouterr().out

    monkeypatch.setenv("AO_TEST_SMTP_PASSWORD", "from-the-environment")
    assert cli.cmd_email(project, args) == 0

    saved = json.loads(conf.read_text())
    assert saved["provider"] == "smtp" and saved["password"] == "from-the-environment"
    assert email.config()["host"] == "smtp.example.test"


def test_ao_email_setup_asks_for_an_account_s_password_at_the_terminal_and_never_without_one(project, conf,
                                                                                           monkeypatch, capsys):
    """EMAIL-SETUP-PROMPT: the setup text had a person read the password into a variable first, a line that swallowed
    the command pasted after it as the password and left the variable set when it was cut short."""
    import getpass
    import sys
    args = SimpleNamespace(action="setup", provider="smtp", token=None, to="me@example.test",
                           host="smtp.example.test", port=None, user="me@example.test",
                           password_env=None, sender=None, tls=None)
    asked = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: asked.append(prompt) or "typed-unseen")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.cmd_email(project, args) == 2
    assert "asked at a terminal, and none is attached; --password-env names a variable" in capsys.readouterr().out
    assert asked == [] and not conf.exists()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.cmd_email(project, args) == 0

    assert asked == ["password for me@example.test on smtp.example.test (not shown): "]
    assert json.loads(conf.read_text())["password"] == "typed-unseen"


def test_an_smtp_server_with_no_account_is_saved_without_asking_for_a_password(project, conf, monkeypatch):
    import getpass
    monkeypatch.setattr(getpass, "getpass", lambda prompt: pytest.fail("asked for a password"))
    args = SimpleNamespace(action="setup", provider="smtp", token=None, to="me@example.test",
                           host="relay.example.test", port=25, user=None, password_env=None, sender=None, tls="none")

    assert cli.cmd_email(project, args) == 0
    assert "password" not in json.loads(conf.read_text())


def test_the_formsubmit_file_from_before_providers_still_sends(conf):
    conf.write_text(json.dumps({"token": "tok", "to": "me@example.test"}))

    assert email.config()["provider"] == "formsubmit"


@pytest.mark.parametrize("provider", [[], {}, ["smtp"], {"name": "smtp"}, None, 1, True])
def test_a_non_string_provider_disables_email(conf, provider):
    conf.write_text(json.dumps({"provider": provider, "token": "tok"}), encoding="utf-8")

    assert email.config() is None
    assert email.send("red", "the queue is empty") is False
