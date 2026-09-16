from types import SimpleNamespace

from ao import watchdog as W


def _windows(monkeypatch, calls, code=0):
    monkeypatch.setattr(W.sys, "platform", "win32")
    monkeypatch.setattr(W.shutil, "which", lambda name: "C:\\Windows\\powershell.exe" if name == "powershell" else None)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(W.subprocess, "run", run)


def test_a_toast_is_off_until_the_project_turns_it_on(project, monkeypatch):
    calls = []
    _windows(monkeypatch, calls)

    assert W.desktop_notify("proj: needs you", "a message", project) is False
    assert W.desktop_notify("proj: needs you", "a message", None) is False
    assert calls == []


def test_a_toast_carries_its_text_in_the_environment_never_in_the_script(project, monkeypatch):
    calls = []
    _windows(monkeypatch, calls)
    cfg = dict(project, features={"toast": True})
    title, body = 'proj: "quoted"; $(boom)', "line one\nline two"

    assert W.desktop_notify(title, body, cfg) is True

    [(argv, kwargs)] = calls
    assert argv[0].endswith("powershell.exe") and argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert title not in argv[-1] and "boom" not in argv[-1]
    assert kwargs["env"]["AO_TOAST_TITLE"] == title and kwargs["env"]["AO_TOAST_BODY"] == body


def test_a_failed_toast_is_reported_and_never_raises(project, monkeypatch):
    calls = []
    _windows(monkeypatch, calls, code=1)
    cfg = dict(project, features={"toast": True})

    assert W.desktop_notify("t", "m", cfg) is False
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no shell")))
    assert W.desktop_notify("t", "m", cfg) is False
