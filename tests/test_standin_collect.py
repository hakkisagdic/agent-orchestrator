import os
import subprocess
import sys
from types import SimpleNamespace

from ao import allowlist as AL, cli, language, lib as A, storage
from tests.test_capability_matrix import _strict_config
from tests.test_commit_authority import _allow_commit_prerequisites
from tests.test_review_chain import _args, _fake, _repo_with_change

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "", "## Bulgular", "Bulgu yok.")
REJECTED = ("VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 0", "LOW: 0",
            "- [BLOCKER] src/a.py:1 — the defect the stand-in found")


def _unreachable(project):
    """A running slice whose only reviewer is down: ao writes a stand-in request."""
    root = project["root"]
    _repo_with_change(root)
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    open(board, "w", encoding="utf-8").write(text.replace("## running\n", "## running\n- [S1] slice · acceptance: b\n"))
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("down", exit_code=17)})
    assert cli.cmd_review(cfg, _args(boundary=None)) == 3
    (meta,) = [name for name in os.listdir(A.review_requests_dir(root)) if name.endswith(".json")]
    return cfg, A.review_request(root, meta[:-len(".json")])


def _answer(tmp_path, nonce, lines):
    path = tmp_path / "answer.txt"
    path.write_text(f"NONCE: {nonce}\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _collect(cfg, nonce, response, capsys, **overrides):
    capsys.readouterr()
    args = SimpleNamespace(nonce=nonce, response=response, model="gpt-5.5", by="alice (owner)")
    for key, value in overrides.items():
        setattr(args, key, value)
    code = cli.cmd_collect_review(cfg, args)
    return code, capsys.readouterr().out


def _carried_rows(root):
    return [row for row in storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
            if str(row.get("reviewer") or "").startswith("human-assisted:")]


def test_an_unreachable_reviewer_leaves_a_request_a_person_can_carry(project, capsys):
    cfg, request = _unreachable(project)
    root = cfg["root"]

    text = open(os.path.join(A.review_requests_dir(root), f"{request['nonce']}.md"), encoding="utf-8").read()
    assert request["candidate"] == A.index_candidate(root)["digest"] and request["collected"] is None
    assert f"NONCE: {request['nonce']}" in text
    assert language.text(cfg, "prompt.review-candidate") in text and "VERDICT: APPROVED" in text
    assert f"ao collect-review {request['nonce']}" in capsys.readouterr().out


def test_a_collected_rejection_is_a_round_recorded_with_its_transport_and_limits(project, tmp_path, capsys):
    cfg, request = _unreachable(project)
    root = cfg["root"]

    code, out = _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], REJECTED), capsys)
    assert code == 1
    assert A.rounds(root, "semantic-review") == 1
    (row,) = _carried_rows(root)
    assert row["reviewer"] == "human-assisted:gpt-5.5" and row["fallback"] is True
    evidence = A.review_evidence(open(os.path.join(root, "semantic-review", row["artefact"]), encoding="utf-8").read())
    assert evidence["transport"] == "human-carried" and evidence["collected_by"] == "alice (owner)"
    assert evidence["limits"] == list(A.STANDIN_LIMITS) and evidence["nonce"] == request["nonce"]
    assert A.review_request(root, request["nonce"])["collected"]["artefact"] == row["artefact"]

    code, out = _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], APPROVED), capsys)
    assert code == 2 and "already collected" in out


def test_a_collected_approval_authorises_the_commit(project, tmp_path, monkeypatch, capsys):
    cfg, request = _unreachable(project)
    root = cfg["root"]
    assert _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], APPROVED), capsys)[0] == 0

    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    capsys.readouterr()
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    assert A.latest_authority_decision(root)["reviewer"] == "human-assisted:gpt-5.5"


def test_an_answer_without_the_requests_nonce_is_refused(project, tmp_path, capsys):
    cfg, request = _unreachable(project)

    code, out = _collect(cfg, request["nonce"], _answer(tmp_path, "0" * 32, APPROVED), capsys)
    assert code == 1 and "does not begin with this request's nonce" in out
    assert _carried_rows(cfg["root"]) == []
    assert A.review_request(cfg["root"], request["nonce"])["collected"] is None


def test_an_answer_about_bytes_no_longer_staged_is_refused(project, tmp_path, capsys):
    cfg, request = _unreachable(project)
    root = cfg["root"]
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 3\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)

    code, out = _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], APPROVED), capsys)
    assert code == 1 and "staged candidate changed since the request" in out
    assert _carried_rows(root) == []


def test_a_slow_reviewer_that_answers_leaves_no_request(project):
    root = project["root"]
    _repo_with_change(root)
    slow = [sys.executable, "-c",
            "import time; time.sleep(2); " + "; ".join(f"print({line!r})" for line in APPROVED), "{prompt}"]

    assert cli.cmd_review(dict(project, reviewer={"id": "r1", "family": "x", "argv": slow}), _args()) == 0
    assert not os.path.exists(A.review_requests_dir(root))


def test_collecting_is_a_persons_act_never_an_agents(project, tmp_path, capsys):
    cfg, request = _unreachable(project)
    answer = _answer(tmp_path, request["nonce"], APPROVED)

    assert _collect(cfg, request["nonce"], answer, capsys, by="kiro")[0] == 2
    assert _collect(cfg, "f" * 32, answer, capsys)[0] == 2
    assert _collect(_strict_config(project), request["nonce"], answer, capsys)[0] == 2
    assert _carried_rows(cfg["root"]) == []
    assert not AL.admits("Bash(ao review:*)", "ao collect-review n --response r --model m --by b")
    assert any(command.startswith("ao collect-review") for _, command in AL.FORBIDDEN)
