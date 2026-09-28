"""What the Windows lane found in the run dispatched after WINDOWS-PID-ALIVE (WINDOWS-LANE-4).

Twelve tests failed there. Nine read processes through a backend that was ps and lsof, which Windows does
not have: the backend is chosen once, by a PowerShell reading of this process, and the first reading of
the suite came in a test that stood in for subprocess, so it failed and the fallback was kept. An empty
snapshot was kept for two seconds as well. The status panel named a file in the home with backslashes
where its every other reading has slashes, and a lane another checkout started was not known for one,
since its path was compared case-folded against a branch that keeps its item's case.
"""
import json
import os

from ao import cli, lib as A, procs

LANE_OF_ANOTHER_CHECKOUT = "a lane another checkout started: `ao lane remove` there retires it"


def test_windows_keeps_its_own_backend_whatever_its_first_reading_gave(monkeypatch):
    monkeypatch.setattr(procs, "_NATIVE", None)
    monkeypatch.setattr(procs, "_run", lambda argv: "")               # PowerShell answered nothing
    with monkeypatch.context() as patch:
        patch.setattr(procs.sys, "platform", "win32")
        backend = procs._backend()

    assert isinstance(backend, procs._Windows)


def test_a_snapshot_that_lists_no_process_is_not_kept(monkeypatch):
    row = {"ProcessId": 42, "ParentProcessId": 1, "CommandLine": "a.exe", "Name": "a.exe", "SessionId": 1,
           "CreationDate": "/Date(1757178612000)/"}
    answers = ["", json.dumps([row])]
    monkeypatch.setattr(procs, "_run", lambda argv: answers.pop(0))
    backend = procs._Windows()
    backend.invalidate()

    assert backend.all_pids() == [] and backend.all_pids() == [42]


def test_a_file_in_the_home_is_named_with_slashes_on_windows_too(monkeypatch):
    monkeypatch.setattr(A, "HOME", "D:\\ao-home")
    with monkeypatch.context() as patch:
        patch.setattr(os, "sep", "\\")
        shown = cli._home_relative("D:\\ao-home\\.ao\\nudge-acme-api.log")

    assert shown == "~/.ao/nudge-acme-api.log"


def test_a_lane_another_checkout_started_is_known_where_paths_are_compared_case_folded(tmp_path, monkeypatch):
    root = str(tmp_path / "proj")
    trees = [{"path": root, "branch": "main"},
             {"path": str(tmp_path / "proj-lanes" / "B"), "branch": "lane/B"}]
    monkeypatch.setattr(A.os.path, "normcase", str.lower)            # as Windows compares a path

    assert list(A.lane_keeps(root, trees).values()) == [LANE_OF_ANOTHER_CHECKOUT]
