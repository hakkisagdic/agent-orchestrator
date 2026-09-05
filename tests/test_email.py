import json

from ao import email


class _Resp:
    def __init__(self, body):
        self._b = body.encode()

    def read(self):
        return self._b


def test_send_posts_json_to_formsubmit(tmp_path, monkeypatch):
    monkeypatch.setattr(email, "CONF", str(tmp_path / "email.json"))
    email.save("tok123", to="me@example.com")
    seen = {}

    def opener(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data.decode())
        return _Resp('{"success": "true", "message": "ok"}')
    assert email.send("test", "hello", root="/tmp/proj", opener=opener) is True
    assert seen["url"] == "https://formsubmit.co/ajax/tok123"
    assert seen["body"]["_subject"] == "[ao/proj] test" and seen["body"]["message"] == "hello"
    assert seen["headers"]["Content-type"] == "application/json"


def test_send_without_config_is_false_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(email, "CONF", str(tmp_path / "missing.json"))
    assert email.send("x", "y") is False


def test_relay_failure_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(email, "CONF", str(tmp_path / "email.json"))
    email.save("tok")
    assert email.send("x", "y", opener=lambda req, timeout=0: (_ for _ in ()).throw(OSError("down"))) is False
