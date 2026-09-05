import os

from ao import lib as A


def _touch(dirpath, name="claude"):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, name)
    open(p, "w", encoding="utf-8").write("#!/bin/sh\necho x\n")
    os.chmod(p, 0o755)
    return p


def test_newest_wins_over_path_order(tmp_path, monkeypatch):
    old = _touch(tmp_path / "usr_local_bin")
    new = _touch(tmp_path / "fnm" / "bin")
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(A, "_BIN_DIRS", ())
    monkeypatch.setattr(A, "_BIN_GLOBS", ())
    versions = {os.path.realpath(old): "2.1.185", os.path.realpath(new): "2.1.261"}
    monkeypatch.setattr(A, "binary_version", lambda p: versions.get(os.path.realpath(p), ""))
    path = os.pathsep.join([os.path.dirname(old), os.path.dirname(new)])
    resolved, ver = A.resolve_binary("claude", path=path)
    assert os.path.realpath(resolved) == os.path.realpath(new) and ver == "2.1.261"


def test_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(A, "_BIN_DIRS", ())
    monkeypatch.setattr(A, "_BIN_GLOBS", ())
    assert A.resolve_binary("no-such-agent", path=str(tmp_path)) == (None, "")
