"""What a person decides a rule proposal beside says what it measured, and who decided it (RULE-PROPOSALS-2).

The review of RULE-PROPOSALS found three things. The evidence sentence put a first-pass rate measured over the
reviewed slices under a count of every landed one, so a person read a rate whose denominator was not the one
the sentence named. A person's decision on a proposal named no person, though ao records the login and whether
a terminal was attached for every other act it attributes to one. And a review ledger that could not be read
was shown as no slice having landed.
"""
from ao import cli, language, lib as A, storage
from tests.test_rule_proposals import CHANGE, WHY, _person, _propose, proposing  # noqa: F401  (a fixture)


def _landed(n, waived=0, defects=0):
    return [{"project": "proj", "slice": f"S{i}", "verdicts": ["APPROVED"], "rounds": 1, "first_pass": i % 2 == 0,
             "waived": i <= waived, "started_at": 1000.0 * i, "landed_at": 1000.0 * i + 500, "hours": 0.14,
             "size": None, "defect_found": i <= defects, "tier": None} for i in range(n, 0, -1)]


def test_the_sentence_names_each_rates_denominator(proposing, monkeypatch, capsys):
    root = proposing["root"]
    monkeypatch.setattr(A, "slice_outcomes", lambda root, project=None: _landed(10, waived=2, defects=1))

    proposal, question, _ = A.propose(root, A.load_config(root), CHANGE, WHY)

    stats = proposal["evidence"]["stats"]
    assert (stats["slices"], stats["waived"]) == (10, 2)
    assert (f"The last 10 landed slice(s), 8 of them reviewed: {stats['first_pass_pct']}% of those approved first "
            f"time, median 1 review round(s); 10% of all 10 with a defect found later.") in question["context"]
    assert cli.main(["-C", root, "proposals"]) == 0
    assert (f"evidence: the last 10 landed slice(s), 8 of them reviewed, {stats['first_pass_pct']}% of those approved "
            f"first time, median 1 review round(s), 10% of all 10 with a defect found later") in capsys.readouterr().out


def test_a_turkish_project_reads_the_same_denominators():
    text = language.text({"language": "tr"}, "proposal.evidence", n=10, reviewed=8, first_pass=50, rounds=1,
                         defects=10)
    assert text == ("Son 10 inen dilim, 8 tanesi review edildi: bunların %50'i ilk seferde onaylandı, ortanca 1 "
                    "review turu; 10 dilimin %10'inde sonradan kusur bulundu.")


def test_a_review_ledger_that_cannot_be_read_is_said_unread_not_empty(proposing, monkeypatch, capsys):
    root = proposing["root"]
    real = storage.read_chained_jsonl

    def torn(path, chain, *args, **kwargs):
        if path == A.review_ledger_path(root):
            raise ValueError("row 3 breaks the chain")
        return real(path, chain, *args, **kwargs)

    monkeypatch.setattr(storage, "read_chained_jsonl", torn)

    proposal, question, _ = A.propose(root, A.load_config(root), CHANGE, WHY)

    assert proposal["evidence"]["slices"] == [] and "row 3 breaks the chain" in proposal["evidence"]["unread"]
    assert "The review ledger could not be read (" in question["context"]
    assert "No slice has landed yet" not in question["context"]
    monkeypatch.setattr(storage, "read_chained_jsonl", real)
    assert cli.main(["-C", root, "proposals"]) == 0
    assert "evidence: none, the review ledger could not be read when it was proposed (" in capsys.readouterr().out


def test_a_decision_at_a_terminal_records_the_login_and_the_terminal(proposing, monkeypatch, capsys):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    _person(monkeypatch)
    monkeypatch.setattr(A, "_login_and_terminal", lambda: ("a-person", True))

    assert cli.main(["-C", root, "answer", proposal["question"], "a"]) == 0

    (decided,) = A.proposals(root)
    assert (decided["answered_by"], decided["answered_user"], decided["answered_interactive"]) == \
        ("terminal", "a-person", True)
    capsys.readouterr()
    assert cli.main(["-C", root, "proposals", "--all"]) == 0
    assert "(terminal, login a-person, a terminal attached, " in capsys.readouterr().out


def test_a_decision_without_a_terminal_says_so(proposing, monkeypatch, capsys):
    root = proposing["root"]
    assert _propose(root) == 0
    (proposal,) = A.proposals(root)
    _person(monkeypatch)
    monkeypatch.setattr(A, "_login_and_terminal", lambda: ("a-person", False))

    assert cli.main(["-C", root, "answer", proposal["question"], "b"]) == 0

    capsys.readouterr()
    assert cli.main(["-C", root, "proposals", "--all"]) == 0
    assert "(terminal, login a-person, no terminal attached, " in capsys.readouterr().out
