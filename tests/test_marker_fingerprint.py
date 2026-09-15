import os
import subprocess
from types import SimpleNamespace

from ao import cli

REPLACEMENT = cli.PROJECT_MARKER_BYTES.replace(b"v1", b"v9")


def _identity_of(path):
    real = os.lstat(path)
    return SimpleNamespace(
        st_dev=real.st_dev, st_ino=real.st_ino, st_mode=real.st_mode, st_size=real.st_size,
        st_mtime_ns=real.st_mtime_ns, st_ctime_ns=real.st_ctime_ns,
        st_mtime=real.st_mtime, st_ctime=real.st_ctime,
    )


def _forge_identity(monkeypatch, marker, identity):
    """Make every stat of the marker report `identity`, whatever the file now holds.

    The fields are written rather than waited for: no filesystem has to reuse an
    inode or round a timestamp for the replacement to look identical.
    """
    real_lstat, real_fstat, real_open = os.lstat, os.fstat, open
    handles, hits = set(), []

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == marker:
            hits.append("lstat")
            return identity
        return real_lstat(path, *args, **kwargs)

    def fstat(fd):
        if fd in handles:
            hits.append("fstat")
            return identity
        return real_fstat(fd)

    def tracked_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if os.fspath(path) == marker:
            handles.add(handle.fileno())
        return handle

    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(cli, "open", tracked_open, raising=False)
    return hits


def test_a_replacement_that_forges_every_stat_field_is_caught_by_its_bytes(tmp_path, monkeypatch):
    root = str(tmp_path)
    marker = os.path.join(root, cli.PROJECT_MARKER)
    with open(marker, "wb") as fh:
        fh.write(cli.PROJECT_MARKER_BYTES)
    before = cli._worktree_project_marker_document(root)
    identity = _identity_of(marker)
    with open(marker, "wb") as fh:
        fh.write(REPLACEMENT)
    hits = _forge_identity(monkeypatch, marker, identity)

    after = cli._worktree_project_marker_document(root)

    assert {"lstat", "fstat"} <= set(hits)
    assert after["fingerprint"][1] == before["fingerprint"][1]
    assert after["fingerprint"] != before["fingerprint"]
    assert after["problem"] == f"{cli.PROJECT_MARKER} bytes are not ao-project-v1"


def test_the_fingerprint_of_a_valid_marker_holds_every_byte_of_it(tmp_path):
    marker = tmp_path / cli.PROJECT_MARKER
    marker.write_bytes(cli.PROJECT_MARKER_BYTES)

    kind, identity, data = cli._worktree_project_marker_document(str(tmp_path))["fingerprint"]

    assert kind == "regular" and data == cli.PROJECT_MARKER_BYTES and identity[3] == len(data)
    marker.write_bytes(cli.PROJECT_MARKER_BYTES + b"x")
    longer = cli._worktree_project_marker_document(str(tmp_path))
    assert longer["problem"] and len(longer["fingerprint"][2]) == len(cli.PROJECT_MARKER_BYTES) + 1


def test_init_refuses_a_marker_replaced_during_the_probe_behind_forged_stat_fields(
    tmp_path, monkeypatch, capsys,
):
    from tests.test_profiles import _init_args

    root = tmp_path / "forged-marker"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    marker = os.path.join(str(root), cli.PROJECT_MARKER)
    with open(marker, "wb") as fh:
        fh.write(cli.PROJECT_MARKER_BYTES)
    identity = _identity_of(marker)
    hits = []

    def probe(_cfg, timeout=cli.REVIEW_PROBE_TIMEOUT):
        with open(marker, "wb") as fh:
            fh.write(REPLACEMENT)
        hits.extend([_forge_identity(monkeypatch, marker, identity)])
        return {
            "configured": True, "ok": True, "route": "fixture-reviewer",
            "binary": "/fixture/reviewer", "version": "1.0.0",
            "reason": "exact nonce echoed", "kind": "success",
        }

    monkeypatch.setattr(cli, "_reviewer_probe", probe)

    assert cli.cmd_init({"root": str(root)}, _init_args()) == 1

    assert hits and {"lstat", "fstat"} <= set(hits[0])
    assert "init refused" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(str(root), ".ao", "config.json"))
