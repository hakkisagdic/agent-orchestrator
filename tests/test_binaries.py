import os
import stat

from ao import lib as A


def _fake(dirpath, version):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "claude")
    open(p, "w").write(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    os.chmod(p, stat.S_IRWXU)
    return p


def test_newest_wins_over_path_order(tmp_path, monkeypatch):
    old = _fake(tmp_path / "usr_local_bin", "2.1.185")
    new = _fake(tmp_path / "fnm" / "bin", "2.1.261")
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    path = os.path.dirname(old) + ":" + os.path.dirname(new)
    resolved, ver = A.resolve_binary("claude", path=path)
    assert resolved == new and ver == "2.1.261"
    # cached: a second call does not re-run the binary
    calls = []
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError()))
    assert A.resolve_binary("claude", path=path)[1] == "2.1.261" and not calls


def test_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(A, "_BIN_DIRS", ())
    monkeypatch.setattr(A, "_BIN_GLOBS", ())
    assert A.resolve_binary("no-such-agent", path=str(tmp_path)) == (None, "")
