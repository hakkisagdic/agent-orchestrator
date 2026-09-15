import json
import os
import subprocess
import time

from ao import cli, lib as A


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


def _stage(root, name, text):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", name)
    return A.index_candidate(root)


def _verified(root, candidate, minutes_ago=0):
    at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - minutes_ago * 60))
    A.record_verification(root, {"id": f"V-{int(time.time())}", "at": at, "schema": 2, "passed": True,
                                 "candidate": candidate, "candidate_ready": True})


def _transcript(tmp_path, monkeypatch, minutes_ago):
    path = tmp_path / "transcript.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    written = time.time() - minutes_ago * 60
    os.utime(path, (written, written))
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(path), None))


def test_a_candidate_that_reaches_a_commit_is_staged_and_landed(project, tmp_path, monkeypatch):
    root = project["root"]
    _transcript(tmp_path, monkeypatch, minutes_ago=5)
    candidate = _stage(root, "a.py", "x = 1\n")
    _verified(root, candidate)
    _git(root, "commit", "-q", "-m", "land it")

    tp = A.throughput(root, project)

    assert (tp["staged"], tp["landed"], tp["state"], tp["stall"]) == (1, 1, "landing", None)


def test_a_staged_candidate_unlanded_past_the_threshold_is_a_stall_with_its_reason(project, tmp_path, monkeypatch):
    root = project["root"]
    _transcript(tmp_path, monkeypatch, minutes_ago=1)
    candidate = _stage(root, "a.py", "x = 1\n")
    _verified(root, candidate, minutes_ago=95)
    A.record_authority(root, False, ["review verdict is NEEDS_CHANGES"], "sha256:tree", "V-1",
                       candidate=candidate, scope={"kind": "full", "paths": [], "digest": "sha256:x"})

    tp = A.throughput(root, project)

    assert tp["state"] == "stalled" and tp["stall"]["minutes"] >= 95
    assert tp["stall"]["reason"] == "commit refused: review verdict is NEEDS_CHANGES"
    assert tp["stall"]["paths"] == ["a.py"]
    counts, state = cli._throughput_lines(tp)
    assert counts.startswith("staged 1 · landed 0")
    assert state.startswith("stalled 1h 3") and "(a.py)" in state


def test_a_candidate_whose_tree_is_already_committed_is_not_a_stall(project, tmp_path, monkeypatch):
    root = project["root"]
    _transcript(tmp_path, monkeypatch, minutes_ago=1)
    candidate = _stage(root, "a.py", "x = 1\n")
    _git(root, "commit", "-q", "-m", "landed before a later verification of the same tree")
    _verified(root, candidate)

    tp = A.throughput(root, project, now=time.time() + 2 * 3600)

    assert tp["stall"] is None and tp["state"] == "landing"


def test_without_candidates_turns_read_as_busy_and_silence_as_idle(project, tmp_path, monkeypatch):
    root = project["root"]
    _transcript(tmp_path, monkeypatch, minutes_ago=30)
    assert A.throughput(root, project)["state"] == "busy"

    _transcript(tmp_path, monkeypatch, minutes_ago=26 * 60)
    tp = A.throughput(root, project)

    assert (tp["state"], tp["staged"], tp["landed"]) == ("idle", 0, 0)
    assert cli._throughput_lines(tp)[1] == "idle: no turns in this window"


def test_decisions_asked_and_still_waiting_are_counted_with_the_oldest_age(project, tmp_path, monkeypatch):
    root = project["root"]
    _transcript(tmp_path, monkeypatch, minutes_ago=1)
    folder = os.path.join(root, ".ao", "decisions")
    os.makedirs(folder, exist_ok=True)
    now = time.time()
    for name, asked, state in (("D-1", now - 3 * 3600, "open"), ("D-2", now - 600, "answered"),
                               ("D-3", now - 30 * 3600, "open")):
        with open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump({"asked_at": asked, "question": name, "state": state}, fh)

    tp = A.throughput(root, project, hours=24, now=now)

    assert (tp["decisions_asked"], tp["decisions_open"], tp["oldest_open_minutes"]) == (2, 2, 30 * 60)
    assert cli._throughput_lines(tp)[0].endswith("2 waiting (oldest 30h 0m)")
