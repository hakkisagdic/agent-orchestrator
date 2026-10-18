"""`ao gain` reads every store it shows, and names the ones it cannot (GAIN)."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from ao import cli, lib as A


def _review(root, name, verdict, slice_id, digest="sha256:c1"):
    evidence = {"kind": "index-candidate", "candidate": {"digest": digest}, "slice": slice_id,
                "authorizable": True}
    A.record_review(root, name, f"VERDICT: {verdict}\n".encode(), evidence, verdict, reviewer="reviewer-1")


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _turn(records, start, usage, *calls):
    records.append({"timestamp": _stamp(start), "payload": {"type": "turn_start"}})
    for name, args in calls:
        records.append({"timestamp": _stamp(start + 1),
                        "payload": {"type": "tool_call", "toolName": name, "args": args}})
    records.append({"payload": {"type": "usage_summary",
                                "promptTurnSummaries": [{"usage": usage, "unit": "credit"}]}})
    records.append({"payload": {"type": "turn_end"}})


def _landed(project):
    root = project["root"]
    _review(root, "r1.md", "APPROVED", "S1")
    size = {"paths": 1, "kinds": {kind: {"paths": 0, "added": 0, "deleted": 0}
                                  for kind in ("product", "tests", "fixtures", "generated", "deletion")}}
    size["kinds"]["product"] = {"paths": 1, "added": 120, "deleted": 30}
    A.record_verification(root, {"id": "V-1", "at": 1, "passed": True, "candidate": {"digest": "sha256:c1"},
                                 "candidate_size": size})
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review="r1.md",
                       candidate={"digest": "sha256:c1"}, scope={})
    subprocess.run([A.git_binary(), "-c", "core.hooksPath=", "commit", "--allow-empty", "-m",
                    "land\n\nAo-Grant: C-1"], cwd=root, check=True, capture_output=True)
    path = os.path.join(root, ".ao", "board.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("## queued\n", "## queued\n\n- [S2] waiting · needs: S1\n", 1)
    text = text.rstrip("\n") + "\n- [F1] fix what S1 landed · fixes: S1\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _evidence(root, commit):
    os.makedirs(os.path.join(root, ".ao", "ledger"), exist_ok=True)
    with open(os.path.join(root, ".ao", "ledger", "platform-runs.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": int(time.time()) - 60, "platform": "windows", "commit": commit,
                             "run": "39026620122", "passed": 5295, "failed": 0, "skipped": 215,
                             "suite": "full"}) + "\n")


def _transcript(tmp_path, monkeypatch, now):
    records = []
    _turn(records, now - 3600, 4.0, ("fs_read", {"path": "src/a.py"}))
    _turn(records, now - 60, 2.0, ("fs_write", {"path": "src/a.py", "text": "x = 1"}))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))


def _args(**overrides):
    values = dict(all=False, since=None, days=14, slices=False, json=False, sources=False, quota=False)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_gain_reads_slices_spend_reviews_and_evidence_from_their_stores(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    first = subprocess.run([A.git_binary(), "rev-parse", "HEAD"], cwd=root, check=True,
                           capture_output=True, text=True).stdout.strip()
    _evidence(root, first)
    _landed(project)
    _transcript(tmp_path, monkeypatch, time.time())

    assert cli.cmd_gain(project, _args(slices=True, sources=True)) == 0

    out = capsys.readouterr().out
    assert "ao gain" in out and "1 landed" in out
    assert "APPROVED" in out and "150 product lines" in out
    assert "6 credit in 2 turns" in out and "2 tool calls" in out
    assert "windows green" in out and "1 commit(s) behind HEAD" in out
    assert "queued 1" in out and "done 1" in out
    # The file is named as the platform writes its paths: os.path.join, as cli_gain builds it (WINDOWS-TESTS-10).
    assert os.path.join(".ao", "ledger", "platform-runs.jsonl") in out and "transcript:" in out
    assert datetime.now().strftime("%m-%d") in out


def test_gain_json_is_one_document_of_the_same_numbers(project, tmp_path, monkeypatch, capsys):
    _landed(project)
    _evidence(project["root"], subprocess.run([A.git_binary(), "rev-parse", "HEAD"], cwd=project["root"],
                                             check=True, capture_output=True, text=True).stdout.strip())
    _transcript(tmp_path, monkeypatch, time.time())

    assert cli.cmd_gain(project, _args(json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["project"] == "proj"
    assert payload["slices"]["slices"] == 1 and payload["slices"]["defects_pct"] == 100
    assert payload["reviews"]["approved"] == 1 and payload["reviews"]["reviewers"] == 1
    assert payload["spend"]["total"] == 6 and payload["spend"]["turns"] == 2
    assert payload["board"]["queued"] == 1
    assert payload["evidence"][0]["platform"] == "windows"
    assert not any(isinstance(value, str) and "\x1b[" in value for value in json.dumps(payload).split('"'))


def test_gain_names_an_adapter_that_declares_no_account_rather_than_guessing(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "implementer_adapter_id", lambda cfg: "kiro")
    monkeypatch.setattr(A, "usage_api", lambda adapter_id: {})
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))

    assert cli.cmd_gain(project, _args(quota=True)) == 0

    out = capsys.readouterr().out
    assert "kiro: its adapter declares no account lookup ao can run" in out
    assert "% used" not in out


def test_gain_says_what_is_absent_instead_of_showing_zero(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    assert cli.cmd_gain(project, _args()) == 0

    out = capsys.readouterr().out
    assert "no landed slice in this window" in out
    assert "no review recorded in this window" in out
    assert "no implementer turn in this window" in out
    assert "no platform run recorded" in out
