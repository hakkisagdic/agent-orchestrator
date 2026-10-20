"""`ao next` names the command that stands in the way, and whose hand has it (NEXT).

A project's state is spread over `.ao/board.md`, three chained ledgers, `~/.ao/review-requests`,
Git's index and a harness configuration no person has read yet. `ao board` answers where an item is
and `ao status` whether an agent is alive; neither answers which command, in what order, by whom -
and that is where a landing chain stalls, because a person has to hold all of it in memory. So this
reads the same stores the gate reads, through its readers, and pins what it answers:

- the chain is named at its first open stage, and the stage is the one `ao commit-ok` refuses on: an
  item is produced by the reader the grant uses, never by a parallel guess, and a drift test has
  `ao commit-ok` itself refuse for the reason `ao next` named;
- a person's item never runs: `ao next` writes nothing anywhere, not to a ledger, not to a request,
  not a git ref, which is checked by hashing the project and `~/.ao` around every form of the command;
- a request that binds bytes no longer staged is named without a command, because `ao collect-review`
  refuses it and a person who typed the shown command would only meet that refusal;
- a store that cannot be read is an item that says so, never an empty list, which would read as
  nothing waiting.
"""
import hashlib
import json
import os
import subprocess

import pytest

from ao import cli, lib as A, mcp, matrix


def _git(root, *args):
    subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=root, check=True, capture_output=True)


def _stage(project, text="value = 2\n"):
    """A real staged candidate, measured the way every reader measures it."""
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "a.py")
    if not os.path.exists(path):
        open(path, "w", encoding="utf-8").write("value = 1\n")
        _git(root, "add", "src/a.py")
        _git(root, "commit", "-q", "-m", "product base")
    open(path, "w", encoding="utf-8").write(text)
    _git(root, "add", "src/a.py")
    return A.index_candidate(root)


def _verification(project, candidate, **over):
    """One passing verification of exactly these bytes, as `ao verify` records it."""
    root = project["root"]
    record = {"id": f"V-{len(os.listdir(os.path.join(root, '.ao', 'ledger')))}", "at": 1, "passed": True,
              "profile": "quick", "gates": [{"name": "tests", "passed": True}],
              "gates_digest": A.gate_definitions_digest(root, "quick"),
              "candidate": candidate, "candidate_ready": True, "candidate_issues": [],
              "isolation_inputs": A.gate_inputs(root)}
    record.update(over)
    return A.record_verification(root, record)


def _review(project, candidate, name="approved.md", verdict="APPROVED", authorizable=True):
    """A review of exactly these bytes, recorded and written where its record points."""
    root = project["root"]
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": authorizable,
                "candidate": candidate, "scope": A.candidate_scope(candidate),
                "reviewer": {"id": "independent-reviewer", "family": "independent", "fallback": False}}
    body = "\n".join([f"# Review {name}", A.review_evidence_line(evidence), "",
                      f"VERDICT: {verdict}", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", ""])
    path = os.path.join(root, project["reviews"], name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    data = body.encode("utf-8")
    A.record_review(root, name, data, evidence, verdict, reviewer="independent-reviewer")
    return path


def _grant(project, candidate, token="C-1", **over):
    root = project["root"]
    ver = A.latest_verification(root)
    record = {"granted": True, "reasons": [], "tree": "sha256:t",
              "verification": (ver or {}).get("id") or "V-1",
              "review": "approved.md", "candidate": candidate, "scope": A.candidate_scope(candidate)}
    record.update(over)
    # A matrix is the only field that changes the record's schema: with one, `record_authority` writes
    # the capability-matrix grant (schema 3); without, the index-candidate grant (schema 2).
    return A.record_authority(root, record["granted"], record["reasons"], record["tree"],
                              record["verification"], token, review=record["review"],
                              candidate=record["candidate"], scope=record["scope"],
                              matrix=record.get("matrix"))


def _pipeline(project):
    """The implementer's items, as (kind, command): the chain `ao next` reads."""
    return [(item["kind"], item["command"]) for item in A.pipeline_items(project)]


def _items(project):
    return A.next_items(project)


def _kinds(project):
    return [item["kind"] for item in _items(project)]


def _run(project, capsys, *flags):
    assert cli.main(["-C", project["root"], "next", *flags]) == 0
    return capsys.readouterr().out


def _digests(root):
    """Every file below `root`, byte for byte, keyed by its path."""
    out = {}
    for directory, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(directory, name)
            try:
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                out[os.path.relpath(path, root)] = "unreadable"
    return out


# ---- the chain ---------------------------------------------------------------------------------------------

def test_an_empty_project_names_the_command_that_stages_something(project):
    assert _pipeline(project) == [("no-candidate", "ao board ready")]
    assert cli.build_parser().parse_args(A.pipeline_items(project)[0]["command"].split()[1:]).view == "ready"


def test_a_staged_candidate_is_named_at_the_first_stage_that_does_not_hold(project):
    root = project["root"]
    candidate = _stage(project)

    assert _pipeline(project) == [("no-verification", "ao verify")]
    _verification(project, candidate, passed=False, gates=[{"name": "tests", "passed": False}])
    assert _pipeline(project) == [("gates-failed", "ao verify")]
    _verification(project, candidate)
    assert _pipeline(project) == [("no-review", "ao review")]
    _review(project, candidate)
    assert _pipeline(project) == [("no-grant", "ao commit-ok")]
    _grant(project, candidate)
    assert _pipeline(project) == [("granted", 'ao commit -m "<message>"')]


def test_a_stage_behind_an_open_one_is_not_listed(project):
    """An agent has one thing to do; naming the three behind it invites it to skip ahead."""
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    assert len(_pipeline(project)) == 1
    _stage(project, text="value = 3\n")           # the index moved: only re-verifying is named
    assert _pipeline(project) == [("verification-stale", "ao verify")]


def test_a_record_of_other_bytes_grants_nothing(project):
    candidate = _stage(project)
    other = dict(candidate, digest="sha256:" + "0" * 64)
    _verification(project, other)
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "verification-stale"
    assert other["index_tree"] in item["why"] and candidate["index_tree"] in item["why"]
    _verification(project, candidate)
    _review(project, other)
    assert _pipeline(project) == [("no-review", "ao review")]


def test_a_review_that_is_not_an_approval_grants_nothing(project):
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate, verdict="NEEDS_CHANGES")
    assert _pipeline(project) == [("no-review", "ao review")]


def test_a_review_that_cannot_be_authorized_grants_nothing(project):
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate, authorizable=False)
    assert _pipeline(project) == [("no-review", "ao review")]


def test_a_review_file_rewritten_after_it_was_recorded_is_a_refusal(project):
    """The ledger says which bytes it reviewed; a file changed under it is not that review."""
    candidate = _stage(project)
    _verification(project, candidate)
    path = _review(project, candidate)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nthe implementer added a line\n")
    assert A.pipeline_items(project)[0]["kind"] == "no-review"


def test_a_granted_candidate_names_ao_commit_which_shipped_allowlists_admit(project):
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "granted"
    assert item["command"] == 'ao commit -m "<message>"'
    tools = A.turn_adapter("claude-code")["options"]["implementer_tools"]
    assert "Bash(ao commit -m:*)" in tools and "Bash(git commit" not in tools


def test_a_grant_bound_to_other_bytes_is_named_as_stale(project):
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, dict(candidate, digest="sha256:" + "1" * 64))
    assert _pipeline(project) == [("grant-stale", "ao commit-ok")]


def test_a_grant_bound_to_an_older_verification_of_the_same_bytes_is_named_as_stale(project, capsys):
    """Re-verifying the same staged bytes invalidates the grant's verification binding in commit-check."""
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate, id="V-1")
    _review(project, candidate)
    _grant(project, candidate, verification="V-1")
    assert A.pipeline_items(project)[0]["kind"] == "granted"
    _verification(project, candidate, id="V-2")
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "grant-stale" and item["command"] == "ao commit-ok"
    assert "V-2" in item["why"] and "V-1" in item["why"]
    assert cli.main(["-C", root, "commit-check"]) != 0
    assert item["why"] in capsys.readouterr().out


def test_a_grant_whose_scope_no_longer_holds_the_candidate_is_named(project):
    """A path staged after the grant sits outside its scope; the hook refuses, so `ao next` may not say commit."""
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate, scope=A.candidate_scope(candidate, ["src/other.py"]))
    assert A.pipeline_items(project)[0]["kind"] == "grant-stale"
    assert "scope" in A.pipeline_items(project)[0]["why"]


def test_a_schema_that_carries_no_candidate_grants_nothing(project):
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    A.record_authority(project["root"], True, [], "sha256:t", "V-1", "C-1", review="approved.md")
    assert A.pipeline_items(project)[0]["kind"] == "grant-stale"


def test_a_project_under_a_hold_is_named_for_the_person(project):
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    os.makedirs(os.path.join(root, ".ao"), exist_ok=True)
    with open(os.path.join(root, A.HOLD_FILE), "w", encoding="utf-8") as fh:
        json.dump({"by": "fable", "reason": "a release is being cut"}, fh)
    items = A.pipeline_items(project)
    assert [item["kind"] for item in items] == ["project-held"]
    assert items[0]["who"] == A.PERSON


def test_an_unreadable_ledger_is_an_item_that_says_so_never_an_empty_list(project, monkeypatch):
    def broken(root):
        raise RuntimeError("the chain's prefix does not match")
    monkeypatch.setattr(A, "latest_verification", broken)
    _stage(project)
    kind, why = A.pipeline_items(project)[0]["kind"], A.pipeline_items(project)[0]["why"]
    assert kind == "verification-unreadable" and "cannot be read" in why


def test_the_isolation_reading_is_the_gate_s_own(project, capsys):
    """`ao next` and `ao commit-ok` refuse a verification for the same reason, in the same words."""
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate, isolation_inputs=["something", "else"])
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "verification-unisolated"
    assert cli.main(["-C", root, "commit-ok"]) != 0
    assert item["why"] in capsys.readouterr().out


# ---- a person's hand -------------------------------------------------------------------------------------

def test_a_harness_change_no_person_has_read_is_named_for_the_person(project):
    """The fixture's roles name commands nothing has read, which holds every review, wake and lane."""
    kinds = _kinds(project)
    assert "harness-unseen" in kinds
    item = next(item for item in _items(project) if item["kind"] == "harness-unseen")
    assert item["who"] == A.PERSON
    assert item["command"] == "ao harness seen --by <your name> --ask"


def test_a_stand_in_request_waits_for_the_person_who_can_carry_it_back(project):
    root = project["root"]
    candidate = _stage(project)
    request = A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                                     "sha256:" + "0" * 64, "product change", "S1",
                                     candidate["changed_paths"], "judge this candidate")
    item = next(item for item in _items(project) if item["kind"] == "review-uncollected")
    assert item["who"] == A.PERSON and request["nonce"] in item["command"]
    assert "--by <your name>" in item["command"] and item["slice"] == "S1"


def test_a_request_bound_to_bytes_no_longer_staged_names_no_command(project):
    """`ao collect-review` refuses it, so showing its command would only lead a person to that refusal."""
    root = project["root"]
    candidate = _stage(project)
    A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                           "sha256:" + "0" * 64, "product change", "S1", candidate["changed_paths"], "judge this")
    _stage(project, text="value = 9\n")
    kinds = _kinds(project)
    assert "review-superseded" in kinds and "review-uncollected" not in kinds
    item = next(item for item in _items(project) if item["kind"] == "review-superseded")
    assert item["command"] is None


def test_more_requests_than_are_shown_are_counted(project):
    root = project["root"]
    candidate = _stage(project)
    for _ in range(A.REQUEST_LIMIT + 2):
        A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                               "sha256:" + "0" * 64, "product change", "S1", candidate["changed_paths"], "judge")
    kinds = _kinds(project)
    assert kinds.count("review-uncollected") == A.REQUEST_LIMIT
    assert "review-uncollected-more" in kinds
    assert "2 further requests" in next(item for item in _items(project)
                                        if item["kind"] == "review-uncollected-more")["why"]


def test_a_review_requests_directory_that_cannot_be_listed_is_named_as_unreadable(project, monkeypatch):
    root = project["root"]
    candidate = _stage(project)
    A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                           "sha256:" + "0" * 64, "product change", "S1", candidate["changed_paths"], "judge")
    target = A.review_requests_dir(root)
    real_listdir = os.listdir

    def guarded(path):
        if os.path.realpath(path) == os.path.realpath(target):
            raise PermissionError(13, "Permission denied", path)
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", guarded)
    item = next(item for item in _items(project) if item["kind"] == "the review requests-unreadable")
    assert item["who"] == A.PERSON and "PermissionError" in item["why"]


def test_urgent_unacknowledged_mail_is_named_with_the_command_that_reads_it(project, monkeypatch):
    monkeypatch.setattr(A, "urgent_messages", lambda root, cfg, role=None: [{"id": "M-1", "title": "release"}])
    item = next(item for item in _items(project) if item["kind"] == "mail-urgent")
    assert item["who"] == A.PERSON and item["command"] == "ao mail ack M-1"


def test_urgent_mail_for_implementer_or_architect_is_named_and_acknowledged_with_mail_ack(project, capsys):
    root = project["root"]
    box = os.path.join(root, project["mailbox"])
    to_impl = "20261019-1000-fable-to-kiro-DECISION-stop.md"
    to_arch = "20261019-1001-kiro-to-fable-DECISION-ask.md"
    with open(os.path.join(box, to_impl), "w", encoding="utf-8") as fh:
        fh.write("# Stop the slice\n\n## URGENT\n\nWait for the new contract.\n")
    with open(os.path.join(box, to_arch), "w", encoding="utf-8") as fh:
        fh.write("# Contract question\n\n## URGENT\n\nNeed architect answer.\n")

    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)

    urgent_items = [item for item in A.person_items(project) if item["kind"] == "mail-urgent"]
    assert [item["command"] for item in urgent_items] == [f"ao mail ack {to_impl}", f"ao mail ack {to_arch}"]
    pipe_item = A.pipeline_items(project)[0]
    assert pipe_item["kind"] == "mail-urgent" and pipe_item["command"] == f"ao mail ack {to_impl}"
    assert cli.main(["-C", root, "commit-check"]) != 0
    assert pipe_item["why"] in capsys.readouterr().out

    assert cli.main(["-C", root, "mail", "ack", to_arch]) == 0
    assert [item["command"] for item in A.person_items(project) if item["kind"] == "mail-urgent"] == [
        f"ao mail ack {to_impl}"
    ]
    assert cli.main(["-C", root, "mail", "ack", to_impl]) == 0
    assert "mail-urgent" not in _kinds(project)
    assert A.pipeline_items(project)[0]["kind"] == "granted"


def test_an_open_proposal_names_its_question_and_the_answer_command_that_closes_it(project):
    root = project["root"]
    recorded, _question, _created = A.propose(root, project, "keep every slice under 200 lines",
                                              "oversized slices stalled review")
    item = next(item for item in _items(project) if item["kind"] == "proposal-open")
    assert recorded["question"] in item["why"]
    assert item["detail"] == recorded["question"]
    assert item["command"] == f"ao answer {recorded['question']} a|b"
    assert cli.main(["-C", root, "answer", recorded["question"], "a"]) == 0
    assert "proposal-open" not in _kinds(project)


def test_a_slice_blocked_waiting_on_a_person_is_named(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "board.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## blocked\n", "## blocked\n\n- [S7] needs a key · waiting: human\n", 1))
    item = next(item for item in _items(project) if item["kind"] == "slice-waits-human")
    assert item["slice"] == "S7" and item["command"] == "ao slice start S7"
    assert cli.main(["-C", root, "slice", "start", "S7"]) == 0
    assert "slice-waits-human" not in _kinds(project)
    assert [row["id"] for row in A.board(root)["running"]] == ["S7"]


def _switch_review(project, on):
    """The config's switch, written the way `ao features off review` writes it."""
    path = os.path.join(project["root"], ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["features"] = dict(cfg.get("features") or {}, review=on)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    project["features"] = cfg["features"]


def test_a_review_switched_off_without_a_person_s_record_is_named(project):
    """The config is a file the implementer can write, so review stays required until a person records it."""
    from ao import features as F
    _switch_review(project, False)
    assert "review" in F.unrecorded(project)
    item = next(item for item in _items(project) if item["kind"] == "review-off-unrecorded")
    assert item["who"] == A.PERSON and "ao features off review --by <your name>" in item["command"]


def test_a_review_switched_off_by_a_person_holds_no_item(project):
    """A person recorded the switch, so review is off and `ao next` asks for no review either."""
    root = project["root"]
    _switch_review(project, False)
    A.record_opt_in(root, "features.review", False, "alice")
    candidate = _stage(project)
    _verification(project, candidate)
    assert _pipeline(project) == [("no-grant", "ao commit-ok")]
    assert "review-off-unrecorded" not in _kinds(project)


# ---- it writes nothing -----------------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [[], ["--json"], ["--short"]])
def test_no_form_of_the_command_writes_anywhere(project, capsys, monkeypatch, flags):
    """Not a ledger row, not a request, not a git object or index extension, not a byte in ~/.ao or .ao/project-key."""
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "a.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("value = 1\n")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "product base")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("value = 2\n")
    _git(root, "add", "src/a.py")
    home = A.HOME
    assert not os.path.exists(A.project_registry_path())
    assert not os.path.exists(os.path.join(root, ".ao", A.PROJECT_KEY_FILE))
    monkeypatch.setattr(A, "urgent_messages", lambda *a, **k: [{"id": "M-1", "title": "release"}])
    before = (_digests(root), _digests(home))
    head_before = subprocess.run([A.git_binary(), "rev-parse", "HEAD"], cwd=root,
                                 capture_output=True, text=True).stdout

    assert cli.main(["-C", root, "next", *flags]) == 0
    assert capsys.readouterr().out
    mcp_items = mcp.call("ao_next", {}, dict(project, project=None), allow_verify=False, role="architect")["items"]
    assert "no-verification" in [item["kind"] for item in mcp_items]

    assert (_digests(root), _digests(home)) == before
    assert head_before == subprocess.run([A.git_binary(), "rev-parse", "HEAD"], cwd=root,
                                         capture_output=True, text=True).stdout


def test_person_items_leaves_the_process_environment_and_module_bindings_untouched(project, monkeypatch):
    """Reading harness state passes the read-only key and git lock env to the readers directly, never through
    `os.environ` or `A.project_key` (NEXT)."""
    root = project["root"]
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    env_before = dict(os.environ)
    key_fn = A.project_key
    real_changes = A.harness_config_changes
    seen_inside = []

    def check_while_reading(*args, **kwargs):
        seen_inside.append((dict(os.environ), A.project_key, kwargs.get("key")))
        return real_changes(*args, **kwargs)

    monkeypatch.setattr(A, "harness_config_changes", check_while_reading)
    items = A.person_items(project)

    assert any(item["kind"] == "harness-unseen" for item in items)
    assert seen_inside == [(env_before, key_fn, A.next_project_key(root))]
    assert dict(os.environ) == env_before and A.project_key is key_fn
    assert not os.path.exists(A.project_registry_path())
    assert not os.path.exists(os.path.join(root, ".ao", A.PROJECT_KEY_FILE))


def test_a_request_file_ao_cannot_read_is_named_never_dropped(project):
    """A request that stopped matching its own nonce answers nothing; silently skipping it would hide
    that a person's carried copy may be in there."""
    root = project["root"]
    candidate = _stage(project)
    request = A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                                     "sha256:" + "0" * 64, "product change", "S1",
                                     candidate["changed_paths"], "judge this")
    path = os.path.join(A.review_requests_dir(root), request["nonce"] + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"nonce": "0" * 32}, fh)       # the file no longer names the request stored under it
    kinds = _kinds(project)
    assert "review-request-unreadable" in kinds and "review-uncollected" not in kinds
    assert next(item for item in _items(project) if item["kind"] == "review-request-unreadable")["command"] is None


def test_a_grant_of_the_schema_the_mode_does_not_run_is_named(project):
    """The hook refuses a matrix grant on a project that declares no matrix; `ao next` says so before a
    person is told to commit."""
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate, matrix={"digest": "sha256:" + "2" * 64})
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "grant-stale" and "capability matrix" in item["why"]
    assert cli.main(["-C", project["root"], "commit-ok"]) != 0


def test_a_strict_project_is_not_told_to_commit_a_grant_without_a_matrix(project, monkeypatch):
    """The other half of the same rule: strict wants schema 3, and a schema-2 grant is the hook's refusal.

    Strictness is patched rather than declared: `ao capability_matrix` contract is test_capability_matrix's
    to pin, and what matters here is only that the mirror reaches the schema rule the hook reads."""
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    assert A.pipeline_items(project)[0]["kind"] == "granted"     # the chain stands; only the mode changes next
    monkeypatch.setattr(matrix, "is_strict", lambda cfg: True)
    monkeypatch.setattr(matrix, "resolve", lambda cfg, **over: {"digest": "sha256:" + "3" * 64})
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "grant-stale" and "capability-matrix grant" in item["why"]
    assert item["command"] == "ao commit-ok"


def test_a_matrix_that_does_not_resolve_is_blamed_rather_than_the_grant(project):
    """A strict project whose declaration is broken refuses before any grant: the item says so (NEXT).

    Written with the real resolver, no patch — this is the state where `ao next` would otherwise blame a
    schema-2 grant for a matrix that never resolved, and send an agent to a command that refuses it again."""
    root = project["root"]
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    path = os.path.join(root, ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["capability_matrix"] = {"product": ["src/**/*.py"]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    project.update(cfg)
    item = A.pipeline_items(project)[0]
    assert item["kind"] == "grant-stale" and "capability matrix does not resolve" in item["why"]
    assert "schema" not in item["why"]        # the declaration is the blocker, not the row's schema
    assert cli.main(["-C", root, "commit-ok"]) != 0


def test_the_person_s_command_is_shown_and_never_run(project, capsys):
    """`ao next` lists a person's command as text; the harness-change item names one it must not execute."""
    root = project["root"]
    out = _run(project, capsys)
    assert "ao harness seen --by <your name> --ask" in out
    assert A.harness_seen_record(root) is None
    assert "Nothing here was run or recorded" in out


def test_the_terminal_reading_and_the_json_answer_from_one_reading(project, capsys):
    root = project["root"]
    out = _run(project, capsys, "--json")
    document = json.loads(out[out.index("{"):])
    items = _items(project)
    assert document["project"] == project["project"]
    assert document["person"] + document["implementer"] == items
    assert [key for key in items[0]] == list(A.NEXT_ITEM_KEYS)


def _nothing_waits_a_person(monkeypatch):
    """The fixture's config names commands nobody has read; a menu-bar test wants that out of the way."""
    monkeypatch.setattr(A, "harness_unseen", lambda *a, **k: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *a, **k: [])


def test_the_short_line_counts_actions_and_names_one_command(project, capsys, monkeypatch):
    _nothing_waits_a_person(monkeypatch)
    candidate = _stage(project)
    _verification(project, candidate)
    _review(project, candidate)
    _grant(project, candidate)
    assert _run(project, capsys, "--short").strip() == '0 for you · 1 for the implementer · ao commit -m "<message>"'


def test_an_unactionable_person_item_is_counted_for_no_one(project, capsys, monkeypatch):
    """A superseded request is listed by `ao next`; a menu bar counts only what asks for a hand."""
    root = project["root"]
    _nothing_waits_a_person(monkeypatch)
    candidate = _stage(project)
    A.write_review_request(root, project, candidate, A.candidate_scope(candidate),
                           "sha256:" + "0" * 64, "product change", "S1", candidate["changed_paths"], "judge")
    _stage(project, text="value = 8\n")
    assert _run(project, capsys, "--short").strip() == "0 for you · 1 for the implementer · ao verify"
    assert "review-superseded" in _kinds(project)


# ---- the same answer over MCP ----------------------------------------------------------------------------

def test_the_mcp_tool_answers_the_items_the_terminal_shows(project):
    candidate = _stage(project)
    _verification(project, candidate)
    answer = mcp.call("ao_next", {}, project, allow_verify=False, role="architect")
    assert answer["items"] == _items(project)
    assert answer["counts"]["implementer"] == 1


def test_the_mcp_tool_narrows_to_one_actor(project):
    answer = mcp.call("ao_next", {"who": "implementer"}, project, allow_verify=False, role="architect")
    assert all(item["who"] == "implementer" for item in answer["items"])
    assert mcp.call("ao_next", {"who": A.PERSON}, project, allow_verify=False,
                    role="architect")["items"]


def test_a_reviewer_is_never_served_ao_next(project):
    """It names a person's commands; a reviewer's server answers nothing that asks for one (MCP-ROLES)."""
    assert "ao_next" in mcp.ROLE_TOOLS["implementer"] and "ao_next" not in mcp.ROLE_TOOLS["reviewer"]
    answer = mcp.call("ao_next", {}, project, allow_verify=False, role="reviewer")
    assert "error" in answer and answer.get("items") is None
