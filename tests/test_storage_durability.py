import errno
import json
import os
import subprocess
import sys
import time

import pytest

from ao import storage


SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _wait_for(path, process, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return
        if process.poll() is not None:
            raise AssertionError(f"child exited before checkpoint: {process.returncode}")
        time.sleep(0.02)
    process.kill()
    raise AssertionError("timed out waiting for child checkpoint")


def _child(script, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", script, *map(str, args)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_short_write_then_disk_full_is_recovered_without_losing_prior_rows(tmp_path):
    path = str(tmp_path / "authority.jsonl")
    storage.append_jsonl(path, {"id": "before"})
    calls = 0

    def short_then_full(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return os.write(fd, bytes(data[: max(1, len(data) // 2)]))
        raise OSError(errno.ENOSPC, "disk full")

    with pytest.raises(OSError, match="disk full"):
        storage.append_jsonl(path, {"id": "partial"}, _write=short_then_full)

    assert storage.read_jsonl(path) == [{"id": "before"}]
    storage.append_jsonl(path, {"id": "after"})
    assert storage.read_jsonl(path) == [{"id": "before"}, {"id": "after"}]
    assert open(path, "rb").read().endswith(b"\n")


def test_complete_corrupt_record_fails_closed(tmp_path):
    path = tmp_path / "authority.jsonl"
    path.write_bytes(b'{"id":"ok"}\nnot-json\n')
    with pytest.raises(storage.LedgerCorruption):
        storage.read_jsonl(str(path))
    with pytest.raises(storage.LedgerCorruption):
        storage.append_jsonl(str(path), {"id": "must-not-land"})


def test_partial_tail_does_not_hide_earlier_corruption(tmp_path):
    path = tmp_path / "authority.jsonl"
    original = b'{"id":"ok"}\nnot-json\n{"id":"partial"'
    path.write_bytes(original)

    with pytest.raises(storage.LedgerCorruption):
        storage.append_jsonl(str(path), {"id": "must-not-land"})

    assert path.read_bytes() == original


def test_fsync_failure_is_reported_to_the_caller(tmp_path):
    path = str(tmp_path / "authority.jsonl")

    def fail_fsync(_fd):
        raise OSError(errno.EIO, "fsync failed")

    with pytest.raises(OSError, match="fsync failed"):
        storage.append_jsonl(path, {"id": "uncertain"}, _fsync=fail_fsync)


def test_append_uses_portable_utf8_lf_format(tmp_path):
    path = tmp_path / "events.jsonl"
    storage.append_jsonl(str(path), {"text": "café"})
    assert path.read_bytes() == b'{"text":"caf\xc3\xa9"}\n'


def test_new_ledger_syncs_file_and_directory_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows has no directory fsync API")
    steps = []
    storage.append_jsonl(
        str(tmp_path / "authority.jsonl"),
        {"id": "grant"},
        _checkpoint=steps.append,
    )
    assert steps.index("written") < steps.index("fsynced") < steps.index("directory-fsynced")


def test_process_killed_during_partial_write_leaves_recoverable_tail(tmp_path):
    path = tmp_path / "authority.jsonl"
    marker = tmp_path / "partial.marker"
    storage.append_jsonl(str(path), {"id": "before"})
    script = r'''
import os, sys, time
from ao.storage import append_jsonl
path, marker = sys.argv[1:]
def partial(fd, data):
    count = os.write(fd, bytes(data[:max(1, len(data)//2)]))
    open(marker, "w", encoding="utf-8").write("partial")
    while True: time.sleep(1)
append_jsonl(path, {"id":"killed-partial"}, _write=partial)
'''
    proc = _child(script, path, marker)
    _wait_for(marker, proc)
    proc.kill()
    proc.wait(timeout=5)

    assert storage.read_jsonl(str(path)) == [{"id": "before"}]
    storage.append_jsonl(str(path), {"id": "after"})
    assert storage.read_jsonl(str(path)) == [{"id": "before"}, {"id": "after"}]


def test_process_killed_after_fsync_leaves_complete_record(tmp_path):
    path = tmp_path / "authority.jsonl"
    marker = tmp_path / "fsynced.marker"
    storage.append_jsonl(str(path), {"id": "before"})
    script = r'''
import sys, time
from ao.storage import append_jsonl
path, marker = sys.argv[1:]
def checkpoint(step):
    if step == "fsynced":
        open(marker, "w", encoding="utf-8").write("fsynced")
        while True: time.sleep(1)
append_jsonl(path, {"id":"durable"}, _checkpoint=checkpoint)
'''
    proc = _child(script, path, marker)
    _wait_for(marker, proc)
    proc.kill()
    proc.wait(timeout=5)

    assert storage.read_jsonl(str(path)) == [{"id": "before"}, {"id": "durable"}]


def test_reader_waits_until_writer_fsyncs_and_releases_lock(tmp_path):
    path = tmp_path / "authority.jsonl"
    marker = tmp_path / "written.marker"
    release = tmp_path / "release.marker"
    writer_script = r'''
import os, sys, time
from ao.storage import append_jsonl
path, marker, release = sys.argv[1:]
def checkpoint(step):
    if step == "written":
        open(marker, "w", encoding="utf-8").write("written")
        while not os.path.exists(release): time.sleep(0.01)
append_jsonl(path, {"id":"committed"}, _checkpoint=checkpoint)
'''
    reader_script = r'''
import json, sys
from ao.storage import read_jsonl
print(json.dumps(read_jsonl(sys.argv[1])), flush=True)
'''
    writer = _child(writer_script, path, marker, release)
    reader = None
    try:
        _wait_for(marker, writer)
        reader = _child(reader_script, path)
        time.sleep(0.2)
        assert reader.poll() is None, "reader observed a row before writer fsync"

        release.write_text("release", encoding="utf-8")
        writer_stdout, writer_stderr = writer.communicate(timeout=10)
        reader_stdout, reader_stderr = reader.communicate(timeout=10)
        assert writer.returncode == 0, writer_stdout + writer_stderr
        assert reader.returncode == 0, reader_stdout + reader_stderr
        assert json.loads(reader_stdout) == [{"id": "committed"}]
    finally:
        for process in (writer, reader):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_visible_grant_always_has_a_restart_readable_record(tmp_path):
    path = tmp_path / "authority.jsonl"
    script = r'''
import sys, time
from ao.storage import append_jsonl
append_jsonl(sys.argv[1], {"token":"C-visible","granted":True})
print("GRANTED C-visible", flush=True)
while True: time.sleep(1)
'''
    proc = _child(script, path)
    assert proc.stdout.readline().strip() == "GRANTED C-visible"
    proc.kill()
    proc.wait(timeout=5)
    assert storage.read_jsonl(str(path)) == [{"token": "C-visible", "granted": True}]


def test_concurrent_processes_do_not_interleave_json_records(tmp_path):
    path = tmp_path / "events.jsonl"
    script = r'''
import sys
from ao.storage import append_jsonl
path, worker = sys.argv[1], int(sys.argv[2])
for i in range(20): append_jsonl(path, {"worker":worker,"i":i})
'''
    children = [_child(script, path, worker) for worker in range(4)]
    for proc in children:
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, stdout + stderr

    rows = storage.read_jsonl(str(path), allow_partial_tail=False)
    assert len(rows) == 80
    assert {(row["worker"], row["i"]) for row in rows} == {
        (worker, i) for worker in range(4) for i in range(20)
    }
