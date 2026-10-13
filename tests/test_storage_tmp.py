"""ao writes a file whole through a temporary beside it, and an agent can leave a link where that temporary goes.

`replace_file_durably` without a mode named its temporary `.<name>.<pid>.tmp` and opened it to truncate, following a
link: an agent that put a link at that name for a likely pid in a folder ao writes - `.ao/` among them - had ao
truncate and write the link's target. Every temporary is now made under a name no file had, with O_EXCL and, where
the platform has it, O_NOFOLLOW, as one for a credential already was (STORAGE-TMP).
"""
import os

import pytest

from ao import lib as A, storage


def _link(target, at):
    try:
        os.symlink(target, at)
    except (OSError, NotImplementedError) as exc:          # Windows without the symlink privilege
        pytest.skip(f"no symbolic link can be made here: {exc}")


def test_a_link_at_the_name_the_temporary_had_is_never_written_through(tmp_path):
    target, victim = tmp_path / "config.json", tmp_path / "victim.txt"
    target.write_bytes(b"{}")
    victim.write_bytes(b"the person's")
    _link(victim, tmp_path / f".config.json.{os.getpid()}.tmp")

    storage.replace_file_durably(str(target), b'{"a": 1}')

    assert victim.read_bytes() == b"the person's" and target.read_bytes() == b'{"a": 1}'


def test_every_temporary_is_made_exclusively_under_a_name_no_file_had(tmp_path, monkeypatch):
    made, real_open = [], os.open

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        made.append((path, flags))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", recording_open)
    target = tmp_path / "board.md"
    storage.replace_file_durably(str(target), b"one")
    storage.replace_file_durably(str(target), b"two")
    storage.replace_file_durably(str(target), b"three", mode=0o600)

    temporaries = [(path, flags) for path, flags in made if path.endswith(".tmp")]
    assert len(temporaries) == 3 and len({path for path, _ in temporaries}) == 3
    assert all(flags & os.O_EXCL and flags & os.O_CREAT for _, flags in temporaries)
    assert all(flags & getattr(os, "O_NOFOLLOW", 0) == getattr(os, "O_NOFOLLOW", 0) for _, flags in temporaries)
    assert not any(flags & os.O_TRUNC for _, flags in temporaries)
    assert target.read_bytes() == b"three" and sorted(p.name for p in tmp_path.iterdir()) == ["board.md"]


def test_a_file_keeps_the_mode_it_had(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows keeps no POSIX mode bits")
    target = tmp_path / "script.sh"
    target.write_bytes(b"old")
    os.chmod(target, 0o640)

    storage.replace_file_durably(str(target), b"new")

    assert target.read_bytes() == b"new" and (os.stat(target).st_mode & 0o777) == 0o640


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="a link the test makes needs POSIX symlinks")
def test_the_notice_times_fold_writes_through_no_link_left_at_its_old_temporary_name(project, tmp_path):
    """The fold of notice times wrote `.ao/ledger/.notice-times.json.<pid>.tmp`, opened to truncate: a link an agent left
    there for this pid had its target - a file of the person's - written over (STORAGE-TMP)."""
    root = project["root"]
    victim = tmp_path / "persons-file"
    victim.write_text("the person's own\n", encoding="utf-8")
    link = os.path.join(root, ".ao", "ledger", f".notice-times.json.{os.getpid()}.tmp")
    os.symlink(str(victim), link)

    A.record_notice(root, "a notice", "body", sent=False, key="k")

    assert victim.read_text(encoding="utf-8") == "the person's own\n"
    assert os.path.exists(A.notice_times_path(root))


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="a link the test makes needs POSIX symlinks")
def test_the_checkpoint_store_writes_through_no_link_left_at_its_old_temporary_name(tmp_path, monkeypatch):
    """The ledger checkpoint store was written through `<store>.<pid>.tmp`, opened to truncate: with
    AO_LEDGER_CHECKPOINTS naming a store in a folder an agent writes, a link left at that name had its target - a file
    of the person's - written over (STORAGE-TMP)."""
    store = tmp_path / "checkpoints.json"
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", str(store))
    victim = tmp_path / "persons-file"
    victim.write_text("the person's own\n", encoding="utf-8")
    os.symlink(str(victim), f"{store}.{os.getpid()}.tmp")

    storage._write_committed_lengths({"ledgers": {}})

    assert victim.read_text(encoding="utf-8") == "the person's own\n"
    assert store.exists() and not os.path.islink(str(store))
