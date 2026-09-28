"""An agent proposes a change to the rules it works under, and a person decides it (RULE-PROPOSALS).

An agent that found a rule of its playbook, steering or authority wrong had two ways forward: live
with it, or edit the file that holds it and so rewrite the rules it is held to, which nobody
decided. `ao propose`, and `ao_propose` over MCP, record the change instead: a chained row of the
decision ledger naming who proposed it, what and why, the rule file with its digest, and the
outcomes of the project's last landed slices, with a question a person answers through the decision
flow - `ao answer` or a tap on the phone - to accept, reject, or say in their own words. `ao
proposals` lists the open ones. The question parks no slice, rings no alarm and wakes no architect;
no agent can answer it; and neither the proposal nor its answer writes a rule file.
"""
import hashlib
import os
import time
from pathlib import Path

import pytest

from ao import cli, lib as A, mcp, skillkit, storage, telegram, watchdog as W

RULE = ".ao/authority.md"
CHANGE = "Let the implementer run the benchmark suite under ao lock"
WHY = "three slices waited a day for a person to run it"


@pytest.fixture
def proposing(project, monkeypatch, tmp_path):
    """The project as a turn ao started for the implementer sees it: a rule file, and no phone."""
    monkeypatch.setenv("AO_ROLE", "implementer")
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))
    Path(project["root"], RULE).write_text("# Authority\n\n- ao lock runs the declared gates only\n",
                                           encoding="utf-8")
    return project


def _propose(root, *extra):
    return cli.main(["-C", root, "propose", CHANGE, "--why", WHY, *extra])


def _person(monkeypatch):
    """A person at a terminal, or the phone's poller: no turn ao started."""
    monkeypatch.delenv("AO_ROLE", raising=False)


def _files(root):
    """{path in the project: bytes} for every file outside .git."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


# ---- recorded and listed -------------------------------------------------------------------------

def test_a_proposal_is_recorded_in_the_decision_ledger_with_who_what_why_and_the_rule_file_and_listed_open(
        proposing, capsys):
    root = proposing["root"]

    assert _propose(root, "--rule-file", RULE) == 0

    (row,) = A.decision_rows(root)
    assert (row["kind"], row["text"], row["why"], row["via"]) == ("proposal", CHANGE, WHY, "cli")
    assert row["rule_file"] == RULE
    assert row["rule_digest"] == "sha256:" + hashlib.sha256(Path(root, RULE).read_bytes()).hexdigest()
    assert {key: row["by"][key] for key in ("role", "actor", "adapter")} == \
        {"role": "implementer", "actor": "kiro", "adapter": "kiro"}
    assert row["evidence"] == {"slices": [], "stats": A.outcome_stats([])}
    assert (row["previous"], row["ordinal"]) == (None, 1)         # chained, as ao decide chains its rows
    (question,) = A.decisions(root)
    assert (question["id"], question["proposal"], question["state"]) == (row["question"], row["id"], A.PROPOSED)
    assert [option["key"] for option in question["options"]] == ["a", "b", "x"]
    assert CHANGE in question["question"] and WHY in question["context"] and RULE in question["context"]

    capsys.readouterr()
    assert cli.main(["-C", root, "proposals"]) == 0
    listed = capsys.readouterr().out
    assert [part for part in (row["id"], CHANGE, WHY, f"ao answer {question['id']} a", "kiro (implementer)",
                              f"{RULE}, unchanged since it was proposed") if part not in listed] == []
    assert cli.main(["-C", root, "decisions"]) == 0
    assert f"PROPOSED {question['id']}" in capsys.readouterr().out


def test_the_evidence_is_the_last_ten_landed_slices_as_ao_stats_measures_them(proposing, monkeypatch):
    root = proposing["root"]
    landed = [{"project": "proj", "slice": f"S{n}", "verdicts": ["APPROVED"], "rounds": 1, "first_pass": True,
               "waived": False, "started_at": 1000.0 * n, "landed_at": 1000.0 * n + 500, "hours": 0.14,
               "size": None, "defect_found": n == 12, "tier": None} for n in range(12, 0, -1)]
    monkeypatch.setattr(A, "slice_outcomes", lambda root, project=None: landed)

    proposal, question, created = A.propose(root, A.load_config(root), CHANGE, WHY)

    assert created and proposal["evidence"]["slices"] == [f"S{n}" for n in range(3, 13)]
    assert proposal["evidence"]["stats"] == A.outcome_stats([o for o in landed if int(o["slice"][1:]) >= 3])
    assert ("The last 10 landed slice(s), 10 of them reviewed: 100% of those approved first time, "
            "median 1 review round(s); 10% of all 10 with a defect found later.") in question["context"]


def test_a_proposal_reaches_the_phone_with_a_button_to_accept_and_one_to_reject(proposing, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append((text, keyboard)) or 1)

    assert _propose(proposing["root"], "--rule-file", RULE) == 0

    ((text, keyboard),) = sent
    (question,) = A.decisions(proposing["root"])
    assert text.startswith(f"❓ *Rule proposal: {CHANGE}*") and WHY in text
    assert [row[0]["callback_data"] for row in keyboard] == [f"{question['id']}:a", f"{question['id']}:b"]


def test_the_mcp_tool_proposes_as_the_command_does_and_says_where_the_answer_comes_back(proposing):
    root = proposing["root"]
    cfg = A.load_config(root)

    result = mcp.call("ao_propose", {"text": CHANGE, "why": WHY, "rule_file": RULE}, cfg, False)

    (row,) = A.decision_rows(root)
    assert (result["id"], result["question"], row["via"], row["rule_file"]) == (row["id"], row["question"], "mcp", RULE)
    assert "ao_decisions" in result["note"]
    assert [q["id"] for q in mcp.call("ao_decisions", {"state": "proposed"}, cfg, False)["decisions"]] == \
        [row["question"]]
    assert mcp.call("ao_decisions", {"state": "open"}, cfg, False)["decisions"] == []


def test_the_same_change_proposed_twice_is_one_proposal(proposing, capsys):
    root = proposing["root"]
    assert _propose(root) == 0
    capsys.readouterr()

    assert cli.main(["-C", root, "propose", f"  {CHANGE.upper()} ", "--why", "again"]) == 0
    standing = mcp.call("ao_propose", {"text": CHANGE, "why": "and again"}, A.load_config(root), False)

    assert "proposed already" in capsys.readouterr().out
    (proposal,) = A.proposals(root)
    assert (standing["standing"], standing["id"]) == (True, proposal["id"])
    assert len(A.decisions(root)) == 1


def test_two_proposals_in_one_second_keep_a_question_each(proposing, monkeypatch):
    root = proposing["root"]
    monkeypatch.setattr(time, "time", lambda: 1789000000.25)

    first = A.propose(root, A.load_config(root), CHANGE, WHY)[0]
    second = A.propose(root, A.load_config(root), "Let the architect edit the backlog only", WHY)[0]

    assert (first["id"], first["question"]) == ("P-1789000000", "D-1789000000")
    assert (second["id"], second["question"]) == ("P-1789000001", "D-1789000001")
    assert sorted(question["proposal"] for question in A.decisions(root)) == ["P-1789000000", "P-1789000001"]


def test_two_questions_asked_in_one_second_are_two_questions(project, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1789000000.5)

    first = A.ask(project["root"], "which store keeps the ledger?", ["files", "sqlite"])
    second = A.ask(project["root"], "which queue feeds the board?", ["redis", "files"])

    assert (first["id"], second["id"]) == ("D-1789000000", "D-1789000001")
    assert {question["id"]: question["question"] for question in A.decisions(project["root"])} == \
        {"D-1789000000": "which store keeps the ledger?", "D-1789000001": "which queue feeds the board?"}


# ---- refused -------------------------------------------------------------------------------------

def test_a_proposal_without_a_change_or_a_reason_or_with_no_rule_file_of_the_project_is_refused_and_writes_nothing(
        proposing, tmp_path, capsys):
    root = proposing["root"]
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# a rule of another project\n", encoding="utf-8")

    assert cli.main(["-C", root, "propose", CHANGE]) == 2
    assert "--why is required" in capsys.readouterr().out
    for path in (str(outside), "../elsewhere.md", "no-such-rule.md", ".ao"):
        assert _propose(root, "--rule-file", path) == 2, path
        assert "not proposed" in capsys.readouterr().out
    cfg = A.load_config(root)
    assert "reason" in mcp.call("ao_propose", {"text": CHANGE, "why": " "}, cfg, False)["error"]
    assert "names the change" in mcp.call("ao_propose", {"text": " ", "why": WHY}, cfg, False)["error"]

    assert A.decision_rows(root) == [] and A.decisions(root) == []


def test_a_proposal_the_ledger_cannot_record_takes_its_question_back(proposing, monkeypatch, capsys):
    root = proposing["root"]

    def held(*args, **kwargs):
        raise storage.LedgerLockTimeout("held", path=A.decisions_path(root), timeout=10)
    monkeypatch.setattr(storage, "append_chained_jsonl", held)

    assert _propose(root) == 1

    assert "another process held the ledger lock" in capsys.readouterr().err
    assert A.decisions(root) == [] and A.proposals(root) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_a_rule_file_that_links_out_of_the_project_is_refused(proposing, tmp_path, capsys):
    root = proposing["root"]
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# a rule of another project\n", encoding="utf-8")
    os.symlink(str(outside), os.path.join(root, "linked.md"))

    assert _propose(root, "--rule-file", "linked.md") == 2

    assert "outside the project" in capsys.readouterr().out and A.decision_rows(root) == []


# ---- decided by a person, through the decision flow ----------------------------------------------

@pytest.mark.parametrize("answer,state", [("a", "accepted"), ("b", "rejected"), ("x only for the full profile",
                                                                                   "answered")])
def test_a_person_accepts_rejects_or_answers_a_proposal_in_words_with_ao_answer_as_any_question(
        proposing, monkeypatch, capsys, answer, state):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    _person(monkeypatch)
    capsys.readouterr()

    assert cli.main(["-C", root, "answer", proposal["question"], *answer.split()]) == 0

    assert "ao writes no rule file" in capsys.readouterr().out
    (decided,) = A.proposals(root)
    (question,) = A.decisions(root)
    assert (decided["state"], decided["answered_by"], decided["answer"]) == (state, "terminal", question["answer"])
    assert question["answer_key"] == answer[0]
    assert cli.main(["-C", root, "proposals"]) == 0
    assert "No open rule proposals." in capsys.readouterr().out
    assert cli.main(["-C", root, "proposals", "--all"]) == 0
    listed = capsys.readouterr().out
    assert state.upper() in listed and question["answer"] in listed


def test_a_tap_on_the_phone_decides_a_proposal_as_it_answers_any_question(proposing, monkeypatch, tmp_path):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    tap = {"update_id": 1, "callback_query": {"id": "c1", "data": f"{proposal['question']}:b",
                                              "message": {"chat": {"id": 42}}, "from": {"username": "alice"}}}
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params:
                        {"ok": True, "result": [tap]} if method == "getUpdates" else {"ok": True})
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: 1)
    monkeypatch.setattr(telegram, "_offset_path", lambda: str(tmp_path / "telegram-offset"))
    _person(monkeypatch)                                   # the poller runs as a scheduled job, in no turn

    telegram.poll(root, A.load_config(root))

    (decided,) = A.proposals(root)
    assert (decided["state"], decided["answered_by"]) == ("rejected", "alice")


def test_no_agent_decides_a_proposal_neither_a_turn_ao_started_nor_an_architect_decision(proposing, monkeypatch,
                                                                                        capsys):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    did = proposal["question"]
    capsys.readouterr()

    for role in ("implementer", "architect"):
        monkeypatch.setenv("AO_ROLE", role)
        assert cli.main(["-C", root, "answer", did, "a"]) == 2
        assert f"a person decides, not the {role}" in capsys.readouterr().out
    _person(monkeypatch)
    mailbox = os.path.join(root, proposing["mailbox"])
    mail = sorted(os.listdir(mailbox))
    assert cli.main(["-C", root, "decide", "take the proposal", "--why", "it is right", "--answers", did]) == 2
    with pytest.raises(A.AnswerRefused, match="a person decides"):
        A.answer(root, did, "a", by="architect")

    assert "no architect decision settles it" in capsys.readouterr().out
    assert [p["state"] for p in A.proposals(root)] == ["open"]
    assert len(A.decision_rows(root)) == 1 and sorted(os.listdir(mailbox)) == mail     # nothing recorded or mailed


# ---- it parks nothing, and writes no rule ---------------------------------------------------------

def test_a_proposal_waits_for_a_person_without_parking_a_slice_ringing_or_waking_the_architect(proposing):
    root = proposing["root"]
    board = Path(root, ".ao", "board.md")
    board.write_text(board.read_text(encoding="utf-8").replace("## queued\n", "## queued\n- [S1] a slice\n", 1),
                     encoding="utf-8")
    assert _propose(root) == 0
    later = time.time() + 3 * 3600

    assert A.decisions(root, "open") == []
    assert W.escalate_open_decisions(root, "proj", dry_run=True, now=later) == []
    assert W.queue_past_a_question(root) is None
    assert [a for a in A.anomalies(root, proposing, {}, 0, 60) if a["kind"] == "decision-requested"] == []
    assert A.architect_absence(root, proposing)["waiting"] == []

    asked = A.ask(root, "which store keeps the ledger?", ["files", "sqlite"])     # an ordinary question does all four
    assert W.escalate_open_decisions(root, "proj", dry_run=True, now=later) == [asked["id"]]
    assert W.queue_past_a_question(root) == (asked["id"], "S1")
    assert [a["key"] for a in A.anomalies(root, proposing, {}, 0, 60) if a["kind"] == "decision-requested"] == \
        [asked["id"]]
    assert A.architect_absence(root, proposing)["waiting"] == [asked["id"]]


def test_neither_the_proposal_nor_its_answer_writes_the_playbook_or_the_rule_file(proposing, monkeypatch):
    root = proposing["root"]
    os.makedirs(os.path.join(root, ".claude"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)
    skillkit.install_playbook(root, skillkit.detect_agents(root)[1])
    playbook = ".claude/skills/ao/SKILL.md"
    A.project_key(root)                        # a project's name is written the first time any command sees it
    before = _files(root)

    assert _propose(root, "--rule-file", playbook) == 0
    assert "error" not in mcp.call("ao_propose", {"text": "Read the mailbox before the board", "why": WHY,
                                                  "rule_file": RULE}, A.load_config(root), False)
    _person(monkeypatch)
    for proposal in A.proposals(root):
        assert cli.main(["-C", root, "answer", proposal["question"], "a"]) == 0

    after = _files(root)
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    assert changed and all(path.startswith((".ao/ledger/decisions.jsonl", ".ao/decisions/D-")) for path in changed), \
        changed
    assert (after[playbook], after[RULE]) == (before[playbook], before[RULE])
    assert [p["state"] for p in A.proposals(root)] == ["accepted", "accepted"]


def test_a_proposal_whose_question_is_gone_is_listed_as_missing_and_may_be_proposed_again(proposing, monkeypatch,
                                                                                           capsys):
    root = proposing["root"]
    monkeypatch.setattr(time, "time", lambda: 1789000000.25)      # one second: the free file name is the same
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    os.remove(os.path.join(root, A.DECISION_DIR, proposal["question"] + ".json"))
    capsys.readouterr()

    assert cli.main(["-C", root, "proposals"]) == 0

    assert f"MISSING  {proposal['id']}" in capsys.readouterr().out
    assert _propose(root) == 0
    assert [(p["id"], p["state"]) for p in A.proposals(root)] == [("P-1789000000", "missing"),
                                                                  ("P-1789000001", "open")]


def test_the_ledgers_other_readers_read_a_proposal_as_what_it_is(proposing, capsys):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)

    recalled = {entry["kind"]: entry for entry in A.recall_entries("proj", root)}
    assert CHANGE in recalled["proposal"]["text"] and recalled["proposal"]["outcome"] == "proposed by implementer"
    assert recalled["question"]["id"] == proposal["question"]
    assert A.architect_absence(root, proposing)["seen_at"] is None       # another agent's row is no sign of it
    capsys.readouterr()
    assert cli.main(["-C", root, "decide", "--list"]) == 0
    assert "No decisions recorded." in capsys.readouterr().out
