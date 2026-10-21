"""Every gate ledger counts only rows this machine's checkpoint store records ao committing (LEDGER-ATTESTED).

An implementer can write any file in the working tree. A self-consistent row appended by hand - an APPROVED review,
a waiver, a grant, a passing verification, an architect decision, or a plan re-admission - links cleanly to the
row before it, or starts at `previous: null` in a new file. `read_chained_jsonl` and `read_chained_history` now
return only the attested prefix (`storage._attested_length`), `append_chained_jsonl` refuses a ledger holding
foreign rows past that prefix, an interrupted append whose write-ahead digest matches the checkpoint intent is
taken back on the next append, and `ao doctor` names `<ledger>-unattested` and `<ledger>-unreadable` across one
loop over the gate ledgers.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage
from tests.conftest import review_off

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _stage(root, rel="src/a.py", text="value = 1\n"):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", rel)
    return A.index_candidate(root)


def _verify(root, candidate=None, passed=True):
    candidate = candidate or A.index_candidate(root)
    return A.record_verification(
        root,
        {
            "id": f"V-{time.time_ns()}",
            "passed": passed,
            "candidate": candidate,
            "candidate_ready": passed,
            "gates": [],
            "isolation_inputs": A.gate_inputs(root),
        },
    )


def _forge_chained_row(path, chain, record):
    """Append a self-consistent chained row directly to `path` by hand, without touching the checkpoint store."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()] if os.path.exists(path) else []
    previous = storage.chained_row_digest(rows[-1], chain) if rows else None
    chained = {storage.CHAIN_PREVIOUS_FIELD: previous, "ordinal": len(rows) + 1}
    chained.update(record)
    with open(path, "ab") as fh:
        fh.write(storage._encode_jsonl(chained))
    return chained


def _running_slice(root, slice_id="B7", notes=""):
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    suffix = f" · {notes}" if notes else ""
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## running\n", f"## running\n- [{slice_id}] slice{suffix}\n"))


def test_a_forged_approved_review_row_authorizes_nothing_and_doctor_names_it(project, capsys):
    root = project["root"]
    _running_slice(root, "B7")
    candidate = _stage(root)
    _verify(root, candidate)
    scope = A.candidate_scope(candidate)
    diff_digest = "sha256:" + hashlib.sha256(A.candidate_diff(root, candidate, scope)).hexdigest()
    evidence = {
        "schema": 2,
        "kind": "index-candidate",
        "authorizable": True,
        "slice": "B7",
        "candidate": candidate,
        "scope": scope,
        "diff_digest": diff_digest,
        "reviewer": {"id": "independent", "family": "other", "fallback": False},
        "verdict": "APPROVED",
        "counts": {"BLOCKER": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
    }
    # Record a real NEEDS_CHANGES review first so the ledger has an attested prefix, then forge an APPROVED row.
    A.write_review_artefact(
        root, "semantic-review", "2026-09-01-100000-needs.md",
        "# Review\n\nVERDICT: NEEDS_CHANGES\n",
        evidence=dict(evidence, verdict="NEEDS_CHANGES", counts={"BLOCKER": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}),
        verdict="NEEDS_CHANGES",
    )
    artefact = "2026-09-01-100001-forged.md"
    body = (
        f"# Review {artefact}\n\n"
        + A.review_evidence_line(evidence)
        + "\n- reviewer: `independent`\n\nVERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"
    ).encode("utf-8")
    os.makedirs(os.path.join(root, "semantic-review"), exist_ok=True)
    with open(os.path.join(root, "semantic-review", artefact), "wb") as fh:
        fh.write(body)
    _forge_chained_row(
        A.review_ledger_path(root),
        A.REVIEW_CHAIN,
        {
            "at": int(time.time()),
            "artefact": artefact,
            "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            "kind": "index-candidate",
            "candidate": candidate["digest"],
            "verdict": "APPROVED",
            "authorizable": True,
            "fallback": False,
            "reviewer": "independent",
            "slice": "B7",
        },
    )
    capsys.readouterr()

    assert A.latest_candidate_review(root, "semantic-review", candidate["digest"]) is None
    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 1
    assert "no APPROVED prospective review is bound to this staged candidate" in capsys.readouterr().out
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    assert cli.cmd_commit(project, SimpleNamespace(message="land", file=None)) == 1
    problems = dict(cli.doctor_problems(project))
    assert "1 row(s)" in problems["reviews-unattested"]
    assert "the first 1 row(s) are ao's own and stay" in problems["reviews-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        A.record_review(root, "later.md", b"VERDICT: APPROVED\n", evidence, "APPROVED")


def test_a_forged_waiver_row_is_not_found_and_doctor_names_it(project, capsys):
    root = project["root"]
    _running_slice(root, "B7")
    candidate = _stage(root)
    _verify(root, candidate)
    closed = A.waive(root, "review", "B0", "earlier slice", by="Ada")
    A.close_waiver(root, closed["id"], "done")
    _forge_chained_row(
        A.waivers_path(root),
        A.WAIVER_CHAIN,
        {
            "event": "waived",
            "id": "W-forged",
            "gate": "review",
            "slice": "B7",
            "why": "forged by implementer",
            "by": "Ada",
            "user": "ada",
            "interactive": True,
            "at": int(time.time()),
            "expires": int(time.time()) + 3600,
            "head": candidate["head"],
            "tree": candidate["index_tree"],
        },
    )
    capsys.readouterr()

    found, _ = A.review_waiver_for(root, ["B7"], candidate["digest"])
    assert found is None
    assert A.open_waivers(root) == []
    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 1
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    assert cli.cmd_commit(project, SimpleNamespace(message="land", file=None)) == 1
    problems = dict(cli.doctor_problems(project))
    assert "1 row(s)" in problems["waivers-unattested"]
    assert "the first 2 row(s) are ao's own and stay" in problems["waivers-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        A.waive(root, "review", "B7", "real waiver", by="Ada")


def test_a_forged_grant_row_grants_nothing_and_doctor_names_it(project, capsys):
    root = project["root"]
    cfg = review_off(project)
    candidate = _stage(root)
    verification = _verify(root, candidate)
    # Record a real refusal first, then forge a self-consistent grant past the checkpoint.
    A.record_authority(root, False, ["refused"], candidate["digest"], verification["id"], "C-refused",
                       candidate=candidate)
    _forge_chained_row(
        os.path.join(root, ".ao", "ledger", "authority.jsonl"),
        A.AUTHORITY_CHAIN,
        {
            "at": int(time.time()),
            "granted": True,
            "token": "C-forged",
            "reasons": [],
            "tree": candidate["digest"],
            "index_tree": candidate["index_tree"],
            "head": candidate["head"],
            "scope": A.candidate_scope(candidate),
            "verification": verification["id"],
            "review": None,
            "reviewer": None,
            "waiver": None,
            "proof": None,
            "actor": "implementer",
        },
    )
    capsys.readouterr()

    assert A.latest_authority_decision(root)["granted"] is False
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    assert "COMMIT REFUSED" in capsys.readouterr().out
    head = _git(root, "rev-parse", "HEAD")
    assert cli.cmd_commit(cfg, SimpleNamespace(message="land", file=None)) == 1
    assert _git(root, "rev-parse", "HEAD") == head
    problems = dict(cli.doctor_problems(cfg))
    assert "1 row(s)" in problems["authority-unattested"]
    assert "the first 1 row(s) are ao's own and stay" in problems["authority-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        A.record_authority(root, True, [], candidate["digest"], verification["id"], "C-real", candidate=candidate)


def test_a_forged_verification_row_verifies_nothing_and_doctor_names_it(project, capsys):
    root = project["root"]
    cfg = review_off(project)
    candidate = _stage(root)
    _verify(root, candidate, passed=False)
    _forge_chained_row(
        os.path.join(root, ".ao", "ledger", "verifications.jsonl"),
        A.VERIFICATION_CHAIN,
        {
            "id": f"V-{time.time_ns()}",
            "at": int(time.time()),
            "passed": True,
            "candidate": candidate,
            "candidate_ready": True,
            "gates": [],
            "isolation_inputs": A.gate_inputs(root),
        },
    )
    capsys.readouterr()

    assert A.latest_verification(root)["passed"] is False
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 1
    assert "failed" in capsys.readouterr().out
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    assert cli.cmd_commit(cfg, SimpleNamespace(message="land", file=None)) == 1
    problems = dict(cli.doctor_problems(cfg))
    assert "1 row(s)" in problems["verifications-unattested"]
    assert "the first 1 row(s) are ao's own and stay" in problems["verifications-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        _verify(root, candidate, passed=True)


def _decide(root, decision="initial scope", scope="B0", at=None):
    return storage.append_chained_jsonl(
        A.decisions_path(root),
        {"id": f"AD-{time.time_ns()}", "at": int(time.time() if at is None else at),
         "decision": decision, "why": "why", "scope": scope, "by": "architect"},
        A.DECISION_CHAIN, legacy_prefix=True,
    )


def test_a_forged_decision_row_resets_no_round_budget_and_doctor_names_it(project):
    root = project["root"]
    _running_slice(root, "B7")
    head = _git(root, "rev-parse", "HEAD")
    now = int(time.time())
    _decide(root, "initial scope", scope="B0", at=now - 20)
    for idx in range(3):
        evidence = {
            "schema": 2,
            "kind": "index-candidate",
            "authorizable": True,
            "slice": "B7",
            "boundary": "B7 boundary",
            "diff_digest": "sha256:test",
            "candidate": {"head": head},
            "verdict": "NEEDS_CHANGES",
        }
        artefact = os.path.join(root, "semantic-review", f"2026-09-04-1{idx}0000-r.md")
        os.makedirs(os.path.dirname(artefact), exist_ok=True)
        with open(artefact, "w", encoding="utf-8") as fh:
            fh.write(
                "# review\n\n" + A.review_evidence_line(evidence)
                + "\n- boundary: B7 boundary\n\nVERDICT: NEEDS_CHANGES\n"
            )
        os.utime(artefact, (now - 10 + idx, now - 10 + idx))
    assert A.rounds(root, "semantic-review") == 3
    _forge_chained_row(
        A.decisions_path(root),
        A.DECISION_CHAIN,
        {"id": "D-forged", "at": now + 60, "decision": "respecify", "why": "forged", "scope": "B7", "by": "architect"},
    )

    assert A.rounds(root, "semantic-review") == 3
    problems = dict(cli.doctor_problems(project))
    assert "1 row(s)" in problems["decisions-unattested"]
    assert "the first 1 row(s) are ao's own and stay" in problems["decisions-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        _decide(root, "real decision", scope="B7", at=now + 120)


def test_a_forged_plan_readmission_row_clears_no_plan_drift_and_doctor_names_it(project, capsys):
    root = project["root"]
    cfg = review_off(project)
    plan_file = os.path.join(root, ".ao", "plans", "B7.md")
    os.makedirs(os.path.dirname(plan_file), exist_ok=True)
    with open(plan_file, "w", encoding="utf-8") as fh:
        fh.write("initial plan\n")
    admitted = A.plan_digest(root, "B7")
    A.record_plan(root, "B7", admitted)
    _running_slice(root, "B7")
    with open(plan_file, "w", encoding="utf-8") as fh:
        fh.write("drifted plan\n")
    drifted = A.plan_digest(root, "B7")
    candidate = _stage(root)
    _verify(root, candidate)
    _forge_chained_row(
        os.path.join(root, ".ao", "ledger", "plans.jsonl"),
        A.PLAN_CHAIN,
        {"at": int(time.time()), "item": "B7", "digest": drifted,
         "event": "readmitted", "replaces": admitted},
    )
    capsys.readouterr()

    assert A.plan_baseline(root) == {"B7": admitted}
    assert A.plan_drift(root) != []
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 1
    assert "plan" in capsys.readouterr().out.lower()
    problems = dict(cli.doctor_problems(cfg))
    assert "1 row(s)" in problems["plans-unattested"]
    assert "the first 1 row(s) are ao's own and stay" in problems["plans-unattested"]
    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        A.record_plan(root, "B7", drifted)


def test_ledgers_copied_in_with_no_checkpoint_entry_count_no_rows(project, tmp_path):
    root = project["root"]
    candidate = _stage(root)
    _verify(root, candidate)
    A.record_review(root, "2026-09-01-100000-r.md", b"VERDICT: APPROVED\n",
                    {"kind": "index-candidate", "candidate": candidate, "slice": "B7"}, "APPROVED")
    A.waive(root, "review", "B7", "reason", by="Ada")
    _decide(root, "d", scope="B7")
    A.record_plan(root, "B7", "p1")
    for n in range(3):
        A.record_authority(root, True, [], candidate["digest"], "V-1", f"C-{n}", candidate=candidate)
    storage.seal_chained_jsonl(os.path.join(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN, keep=1)

    # Clear the machine's checkpoint store as if these ledgers were copied in from another machine.
    with open(storage.checkpoint_path(), "w", encoding="utf-8") as fh:
        json.dump({}, fh)

    assert A.authority_rows(root) == []
    assert A.authority_history(root) == []
    assert A.latest_verification(root) is None
    assert A.verification_history(root) == []
    assert A.waiver_rows(root) == []
    assert storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN) == []
    assert storage.read_chained_history(A.review_ledger_path(root), A.REVIEW_CHAIN) == []
    assert storage.read_chained_jsonl(A.decisions_path(root), A.DECISION_CHAIN, legacy_prefix=True) == []
    assert A.plan_baseline(root) == {}
    problems = dict(cli.doctor_problems(project))
    for name in ("authority", "verifications", "waivers", "reviews", "decisions", "plans"):
        assert f"{name}-unattested" in problems
        assert "no row of it is ao's own; a person moves the file aside" in problems[f"{name}-unattested"]


def test_crash_between_writing_a_row_and_recording_its_count_recovers_on_next_append(project, monkeypatch):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    first = A.record_authority(root, False, ["refused"], "sha256:0", "V-0", "C-0")

    # Simulate a crash (process death) after the row is written and synced to disk, before _record_committed_length
    # updates the count in the checkpoint store: undo() does not run when the process dies.
    real_record = storage._record_committed_length
    real_append = storage._append_payload_unlocked

    def crash_before_count(*args, **kwargs):
        raise RuntimeError("process died before recording committed count")

    monkeypatch.setattr(storage, "_record_committed_length", crash_before_count)
    # Use the real _append_payload_unlocked but return a no-op undo so the written row stays on disk as on SIGKILL.
    monkeypatch.setattr(storage, "_append_payload_unlocked", lambda *a, **kw: (real_append(*a, **kw), lambda: None)[1])
    with pytest.raises(RuntimeError, match="process died"):
        A.record_authority(root, True, [], "sha256:interrupted", "V-1", "C-interrupted")
    monkeypatch.setattr(storage, "_record_committed_length", real_record)
    monkeypatch.setattr(storage, "_append_payload_unlocked", real_append)

    # The uncommitted second row is on disk, and its write-ahead digest is in the checkpoint store.
    raw_lines = open(path, "rb").read().splitlines()
    assert len(raw_lines) == 2
    entry = storage._checkpoint_entry(path)
    assert entry["count"] == 1 and isinstance(entry.get("append"), str)

    # A read never cuts; it ignores the uncommitted row.
    assert A.authority_rows(root) == [first]
    assert len(open(path, "rb").read().splitlines()) == 2

    # The next append recognizes its own interrupted row by the recorded intent digest, cuts it, clears the intent,
    # and appends the new row cleanly.
    second = A.record_authority(root, True, [], "sha256:recovered", "V-2", "C-recovered")
    assert A.authority_rows(root) == [first, second]
    assert len(open(path, "rb").read().splitlines()) == 2
    entry_after = storage._checkpoint_entry(path)
    assert entry_after["count"] == 2 and "append" not in entry_after


def test_a_foreign_row_with_a_different_digest_than_the_recorded_intent_is_refused_not_cut(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    first = A.record_authority(root, False, ["refused"], "sha256:0", "V-0", "C-0")
    # Record a write-ahead intent whose digest is a valid sha256 hex digest, but different from the foreign row's.
    intent_digest = "sha256:" + "a" * 64
    storage._record_append_intent(path, intent_digest)
    forged = _forge_chained_row(
        path,
        A.AUTHORITY_CHAIN,
        {"at": 1, "granted": True, "token": "C-foreign", "reasons": [], "tree": "sha256:foreign",
         "verification": "V-1", "review": None, "reviewer": None},
    )
    assert storage.chained_row_digest(forged, A.AUTHORITY_CHAIN) != intent_digest
    before = open(path, "rb").read()

    assert A.authority_rows(root) == [first]
    with pytest.raises(storage.LedgerCorruption, match="the first 1 row\\(s\\) are ao's own and stay"):
        A.record_authority(root, True, [], "sha256:new", "V-2", "C-new")
    # The foreign row was refused, not cut.
    assert open(path, "rb").read() == before


def test_doctor_names_each_unreadable_gate_ledger_without_swallowing_it(project):
    root = project["root"]
    candidate = _stage(root)
    _verify(root, candidate)
    A.record_review(root, "2026-09-01-100000-r.md", b"VERDICT: APPROVED\n",
                    {"kind": "index-candidate", "candidate": candidate, "slice": "B7"}, "APPROVED")
    A.waive(root, "review", "B7", "reason", by="Ada")
    _decide(root, "d", scope="B7")
    A.record_plan(root, "B7", "p1")
    A.record_authority(root, True, [], candidate["digest"], "V-1", "C-1", candidate=candidate)

    for rel in ("authority.jsonl", "verifications.jsonl", "waivers.jsonl", "reviews.jsonl",
                "decisions.jsonl", "plans.jsonl"):
        with open(os.path.join(root, ".ao", "ledger", rel), "w", encoding="utf-8") as fh:
            fh.write("{corrupt\n")

    problems = dict(cli.doctor_problems(project))
    for name in ("authority", "verifications", "waivers", "reviews", "decisions", "plans"):
        assert f"{name}-unreadable" in problems
        assert "cannot be read" in problems[f"{name}-unreadable"]


def test_aos_own_ledgers_keep_working_across_append_read_and_seal(project):
    root = project["root"]
    candidate = _stage(root)
    for n in range(4):
        _verify(root, candidate)
        A.record_authority(root, n == 3, [] if n == 3 else ["refused"], candidate["digest"], f"V-{n}", f"C-{n}",
                           candidate=candidate)
    w = A.waive(root, "review", "B7", "reason", by="Ada")
    A.close_waiver(root, w["id"], "done")
    A.record_review(root, "2026-09-01-100000-r.md", b"VERDICT: APPROVED\n",
                    {"kind": "index-candidate", "candidate": candidate, "slice": "B7"}, "APPROVED")
    _decide(root, "d1", scope="B7")
    A.record_plan(root, "B7", "p1")

    auth_path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    ver_path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    storage.seal_chained_jsonl(auth_path, A.AUTHORITY_CHAIN, keep=2)
    storage.seal_chained_jsonl(ver_path, A.VERIFICATION_CHAIN, keep=2, legacy_prefix=True)

    assert len(A.authority_rows(root)) == 2 and len(A.authority_history(root)) == 4
    assert len(A.verification_history(root)) == 4 and A.latest_verification(root)["passed"] is True
    assert len(A.waiver_rows(root)) == 2 and A.open_waivers(root) == []
    assert len(storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)) == 1
    assert len(storage.read_chained_jsonl(A.decisions_path(root), A.DECISION_CHAIN, legacy_prefix=True)) == 1
    assert A.plan_baseline(root) == {"B7": "p1"}
    problems = dict(cli.doctor_problems(project))
    for name in ("opt-ins", "authority", "verifications", "waivers", "reviews", "decisions", "plans"):
        assert f"{name}-unattested" not in problems
        assert f"{name}-unreadable" not in problems


def test_a_ledger_reached_through_a_path_whose_realpath_differs_looks_up_its_attested_prefix_under_realpath(
        project, tmp_path):
    """On macOS /var is a symlink to /private/var: `_attested_length` passed `identity=path` to `_checkpoint_entry`,
    skipping `os.path.realpath(path)`, while `_record_committed_length` and `_check_committed_length` keyed by
    `os.path.realpath(path)`."""
    root = project["root"]
    link = tmp_path / "linked-tree"
    try:
        os.symlink(root, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")
    linked = str(link)
    assert linked != os.path.realpath(linked)

    candidate = _stage(root)
    _verify(linked, candidate)
    A.record_review(linked, "2026-09-01-100000-r.md", b"VERDICT: APPROVED\n",
                    {"kind": "index-candidate", "candidate": candidate, "slice": "B7"}, "APPROVED")
    A.record_authority(linked, True, [], candidate["digest"], "V-1", "C-1", candidate=candidate)

    assert A.latest_verification(linked)["passed"] is True
    assert len(storage.read_chained_jsonl(A.review_ledger_path(linked), A.REVIEW_CHAIN)) == 1
    assert len(A.authority_rows(linked)) == 1
    problems = dict(cli.doctor_problems(dict(project, root=linked)))
    for name in ("opt-ins", "authority", "verifications", "waivers", "reviews", "decisions", "plans"):
        assert f"{name}-unattested" not in problems
        assert f"{name}-unreadable" not in problems


def test_non_gate_chained_stores_with_no_checkpoint_entry_still_read_and_append(project):
    """MEDIUM: one list of gate ledgers in storage is used by both the attested-prefix rule and `ao doctor`;
    every other chained store (`mail-store`, `slices`, `repairs`, `merges`) reads and appends as before when
    this machine has no checkpoint entry for it."""
    root = project["root"]
    assert [name for name, *_ in storage.GATE_LEDGERS] == [
        "opt-ins", "authority", "verifications", "waivers", "reviews", "decisions", "plans"
    ]
    slices_path = os.path.join(root, ".ao", "ledger", "slices.jsonl")
    mail_path = os.path.join(root, ".ao", "ledger", "mail-store.jsonl")
    storage.append_chained_jsonl(slices_path, {"at": 1, "id": "S1", "from": "queued", "to": "running", "by": "human"},
                                 A.SLICE_CHAIN)
    storage.append_chained_jsonl(mail_path, {"at": 1, "event": "message", "id": "m1.md", "body": "hi"},
                                 A.MAIL_STORE_CHAIN)

    # Clear the machine's checkpoint store as on a fresh machine or data from before #62.
    with open(storage.checkpoint_path(), "w", encoding="utf-8") as fh:
        json.dump({}, fh)

    assert [row["id"] for row in storage.read_chained_jsonl(slices_path, A.SLICE_CHAIN)] == ["S1"]
    assert [row["id"] for row in storage.read_chained_history(slices_path, A.SLICE_CHAIN)] == ["S1"]
    assert [row["id"] for row in storage.read_chained_jsonl(mail_path, A.MAIL_STORE_CHAIN)] == ["m1.md"]
    assert [row["id"] for row in storage.read_chained_history(mail_path, A.MAIL_STORE_CHAIN)] == ["m1.md"]
    problems = dict(cli.doctor_problems(project))
    assert "slices-unattested" not in problems and "mail-store-unattested" not in problems

    storage.append_chained_jsonl(slices_path, {"at": 2, "id": "S1", "from": "running", "to": "landed", "by": "human"},
                                 A.SLICE_CHAIN)
    storage.append_chained_jsonl(mail_path, {"at": 2, "event": "handled", "id": "m1.md"}, A.MAIL_STORE_CHAIN)
    assert len(storage.read_chained_jsonl(slices_path, A.SLICE_CHAIN)) == 2
    assert len(storage.read_chained_jsonl(mail_path, A.MAIL_STORE_CHAIN)) == 2


def test_review_row_count_takes_the_ledger_lock_reads_once_and_closes_its_handle(project, monkeypatch):
    """LOW: `review_row_count` takes the ledger lock and reads once (attested and raw counts from the same bytes,
    the handle closed)."""
    import builtins
    root = project["root"]
    candidate = _stage(root)
    A.record_review(root, "2026-09-01-100000-r.md", b"VERDICT: APPROVED\n",
                    {"kind": "index-candidate", "candidate": candidate, "slice": "B7"}, "APPROVED")
    target = os.path.realpath(A.review_ledger_path(root))
    real_open = builtins.open
    opened = []

    def tracking_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if isinstance(file, (str, bytes, os.PathLike)) and os.path.realpath(os.fsdecode(file)) == target:
            assert os.path.exists(target + ".lock")
            opened.append(handle)
        return handle

    monkeypatch.setattr(builtins, "open", tracking_open)
    assert A.review_row_count(root) == 1
    assert len(opened) == 1
    assert opened[0].closed


def test_refused_append_with_foreign_row_and_unterminated_tail_leaves_file_byte_for_byte_as_it_was(project):
    """LOW: `_repair_partial_tail` must not cut an unterminated tail before the foreign-row check runs; a refused
    append leaves the file byte for byte as it was."""
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    A.record_authority(root, False, ["refused"], "sha256:0", "V-0", "C-0")
    _forge_chained_row(
        path,
        A.AUTHORITY_CHAIN,
        {"at": 1, "granted": True, "token": "C-foreign", "reasons": [], "tree": "sha256:foreign",
         "verification": "V-1", "review": None, "reviewer": None},
    )
    with open(path, "ab") as fh:
        fh.write(b'{"unterminated": true')
    before = open(path, "rb").read()

    with pytest.raises(storage.LedgerCorruption, match="does not record ao appending"):
        A.record_authority(root, True, [], "sha256:new", "V-2", "C-new")
    assert open(path, "rb").read() == before
