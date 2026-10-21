"""Authority history the tree cannot make ao forget, and a landed-tree check that reads all of it (LEDGER-HISTORY).

The re-review of BOUNDED-STORES found that a seal was finished from an intent ao read from `.ao/ledger/`, which
the implementer writes: a ledger cut to its newest rows, beside an intent naming the cut rows as sealed, was
accepted, and the record of its length outside the repository rewritten to match. Every reader that asked what
had ever been granted read the live rows alone, so a waiver whose grant was sealed looked unused and stood in for
the review of other bytes, and the archives a seal wrote were never held to it. The re-reviews of LANDED-TREE and
ATTACK-SUITE found that the check for commits no grant bound read the newest 50 commits (20 in the watchdog) and
no merge, told the architect in a row nobody sends, and did not run in a cycle that ended early. The record of a
ledger's length was renamed into place without its folder synced.

A seal's intent is now recorded outside the repository before the ledger is rewritten, and only that intent is
finished; an intent in the tree is refused and left there. The archives are one chain ending at the seal's digest.
History readers read the sealed rows too. The landed-tree check walks every commit since the first grant, names
every merge as `ao commit-check --range` does, and tells a person, whatever else the cycle does.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, email, lib as A, storage, telegram
from ao import watchdog as W

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _authority(root):
    return os.path.join(root, ".ao", "ledger", "authority.jsonl")


def _grants(root, count):
    for n in range(count):
        A.record_authority(root, n % 2 == 0, [] if n % 2 == 0 else ["refused"], f"sha256:{n}", f"V-{n}", f"C-{n}")


def _land(root, rel, text, granted=True, waiver=None):
    """Commit one file's change; `granted` records a grant of exactly the tree committed, built on HEAD, first."""
    with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", rel)
    if granted:
        tree, head = _git(root, "write-tree"), _git(root, "rev-parse", "HEAD")
        A.record_authority(root, True, [], f"sha256:{tree}", "V-1", f"C-{tree[:12]}", waiver=waiver,
                           candidate={"digest": f"sha256:{tree}", "index_tree": tree, "head": head})
    _git(root, "commit", "-q", "-m", f"land {rel}")
    return _git(root, "rev-parse", "HEAD")


def _record():
    with open(storage.checkpoint_path(), "rb") as fh:
        return fh.read()


def _edit_archive(path, field, value, **change):
    """Rewrite the archived row whose `field` is `value`, as an agent with the tree in hand would."""
    folder = os.path.join(os.path.dirname(path), "sealed")
    for name in os.listdir(folder):
        archive = os.path.join(folder, name)
        rows = [json.loads(line) for line in open(archive, encoding="utf-8")]
        if any(row.get(field) == value for row in rows):
            with open(archive, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(dict(row, **change) if row.get(field) == value else row) + "\n"
                              for row in rows)
            return
    raise AssertionError(f"no archived row has {field} {value}")


def _channels(monkeypatch):
    sent = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: sent.append(("desktop", title, msg)) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None:
                        sent.append(("telegram", text, "")) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        sent.append(("email", subject, body)) or True)
    return sent


def _alarm(root, key="landed-without-grant"):
    return [episode for episode in A.active_alarms(A.project_key(root)) if episode["key"] == key]


def _cycle(root):
    return W.run(SimpleNamespace(root=root, idle_minutes=6.0, dry_run=False))


# ---- a seal is finished only from what this machine recorded --------------------------------------------------

@pytest.mark.parametrize("cut", ["its oldest rows", "its oldest and its newest rows", "every row, for a chain of its own"])
def test_a_ledger_cut_beside_a_seal_intent_in_the_tree_is_refused_and_the_record_outside_kept(project, cut):
    root = project["root"]
    path = _authority(root)
    _grants(root, 5)
    lines = open(path, "rb").read().splitlines(keepends=True)
    if cut == "every row, for a chain of its own":
        genesis = "sha256:" + "f" * 64
        forged = {"previous": genesis, "ordinal": 6, "at": 1, "granted": True, "token": "C-forged", "reasons": [],
                  "tree": "sha256:forged", "verification": "V-forged", "review": None, "reviewer": None}
        kept, retired, digest = [storage._encode_jsonl(forged)], 5, genesis
    else:
        kept = lines[3:] if cut == "its oldest rows" else lines[3:4]
        retired, digest = 3, storage.chained_row_digest(json.loads(lines[2]), A.AUTHORITY_CHAIN)
    record = _record()
    with open(path, "wb") as fh:
        fh.write(b"".join(kept))
    pending = storage.seal_path(path) + ".pending"
    with open(pending, "w", encoding="utf-8") as fh:
        json.dump({"chain": A.AUTHORITY_CHAIN, "retired": retired, "digest": digest, "archives": [], "at": 1}, fh)

    with pytest.raises(storage.LedgerCorruption, match="did not begin"):
        A.authority_rows(root)
    with pytest.raises(storage.LedgerCorruption, match="did not begin"):
        A.record_authority(root, True, [], "sha256:x", "V-x", "C-x")

    assert os.path.exists(pending) and not os.path.exists(storage.seal_path(path))
    assert _record() == record


@pytest.mark.parametrize("stop", ["before the ledger is rewritten", "after the ledger is rewritten"])
def test_a_seal_cut_short_is_finished_or_undone_from_the_intent_recorded_outside_the_tree(project, monkeypatch, stop):
    root = project["root"]
    path = _authority(root)
    _grants(root, 6)
    rows, whole = A.authority_rows(root), open(path, "rb").read()
    real = storage.replace_file_durably

    def stopped(target, data, **kwargs):
        if os.path.abspath(target) != os.path.abspath(path):
            return real(target, data, **kwargs)
        if stop.startswith("after"):
            real(target, data, **kwargs)
        raise OSError("the machine stopped here")

    monkeypatch.setattr(storage, "replace_file_durably", stopped)
    with pytest.raises(OSError):
        storage.seal_chained_jsonl(path, A.AUTHORITY_CHAIN, keep=2)
    monkeypatch.setattr(storage, "replace_file_durably", real)

    assert not os.path.exists(storage.seal_path(path) + ".pending")          # the intent was never in the tree
    assert json.loads(_record())[os.path.realpath(path)]["intent"]["retired"] == 4
    if stop.startswith("before"):
        assert A.authority_rows(root) == rows and open(path, "rb").read() == whole
        assert not os.path.exists(storage.seal_path(path))
    else:
        assert A.authority_rows(root) == rows[-2:]
        assert json.load(open(storage.seal_path(path), encoding="utf-8"))["retired"] == 4
    assert A.authority_history(root) == rows
    assert "intent" not in json.loads(_record())[os.path.realpath(path)]


# ---- what a seal retired is history every reader of it reads, held to the seal --------------------------------

def test_an_archived_row_edited_after_its_seal_is_refused_wherever_history_is_read(project):
    root = project["root"]
    verifications = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    for n in range(6):
        A.record_verification(root, {"id": f"V-{n}", "at": n, "passed": n != 1})
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review="2026-10-13-000000-aaa.md")
    _grants(root, 3)
    storage.seal_chained_jsonl(verifications, A.VERIFICATION_CHAIN, keep=2, legacy_prefix=True)
    storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)
    assert A.verification_by_id(root, "V-1")["passed"] is False
    assert A.grant_artefacts_at_risk(root, project) == [("2026-10-13-000000-aaa.md", "missing")]

    _edit_archive(verifications, "id", "V-1", passed=True)                  # the failed run now reads as passed
    _edit_archive(_authority(root), "token", "C-1", review="2026-10-13-000000-other.md")

    with pytest.raises(storage.LedgerCorruption):
        A.verification_by_id(root, "V-1")
    with pytest.raises(storage.LedgerCorruption):
        A.grant_artefacts_at_risk(root, project)
    kinds = {finding["kind"] for finding in A.consistency_findings(root, project)}
    assert {"authority", "verifications"} <= kinds
    assert A.latest_authority_decision(root)["token"] == "C-2"               # the newest decision reads the live rows


def test_a_waiver_whose_grant_was_sealed_stays_spent_and_what_landed_under_it_stays_measured(project):
    root = project["root"]
    head = _git(root, "rev-parse", "HEAD")
    waiver = A.waive(root, "review", "S1", "reviewer at quota", "alice (owner)", hours=24)
    with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("granted\n")
    _git(root, "add", "a.txt")
    tree = _git(root, "write-tree")
    A.record_authority(root, True, [], f"sha256:{tree}", "V-1", "C-under-W", waiver=waiver["id"],
                       candidate={"digest": "sha256:A", "index_tree": tree, "head": head})
    with open(os.path.join(root, "b.txt"), "w", encoding="utf-8") as fh:
        fh.write("staged after the check\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "landed with a path the grant never saw")
    landed = _git(root, "rev-parse", "HEAD")
    _land(root, "c.txt", "the next slice, granted\n")
    assert A.commits_without_grant(root) == [landed]

    storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)

    found, notes = A.review_waiver_for(root, ["S1"], "sha256:B")
    assert found is None and any("already authorised other bytes" in note for note in notes)
    assert A.review_waiver_for(root, ["S1"], "sha256:A")[0]["id"] == waiver["id"]
    (item,) = A.review_waiver_ranges(root)
    assert item["unused"] is False and item["problem"].startswith("UNRESOLVED")
    assert A.commits_without_grant(root) == [landed]


def test_a_commit_is_compared_with_its_grant_when_a_seal_left_only_refusals_after_it(project):
    root = project["root"]
    with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("granted\n")
    _git(root, "add", "a.txt")
    tree, head = _git(root, "write-tree"), _git(root, "rev-parse", "HEAD")
    A.record_authority(root, True, [], f"sha256:{tree}", "V-1", "C-1",
                       candidate={"digest": f"sha256:{tree}", "index_tree": tree, "head": head})
    A.record_authority(root, False, ["refused"], "sha256:other", "V-2", "C-2")
    storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)
    _git(root, "commit", "-q", "-m", "land the granted tree")

    assert A.landed_commit_problem(root) is None


def test_the_checkpoint_store_is_synced_with_its_folder_after_its_rename(project, monkeypatch):
    events = []
    real_replace, real_sync = storage._replace, storage._sync_directory

    def replace(source, target):
        result = real_replace(source, target)
        events.append(("replaced", os.path.abspath(target)))
        return result

    def sync(directory, fsync):
        events.append(("synced", os.path.abspath(directory)))
        return real_sync(directory, fsync)

    monkeypatch.setattr(storage, "_replace", replace)
    monkeypatch.setattr(storage, "_sync_directory", sync)
    A.record_authority(project["root"], True, [], "sha256:t", "V-1", "C-1")

    store = os.path.abspath(storage.checkpoint_path())
    at = events.index(("replaced", store))
    assert ("synced", os.path.dirname(store)) in events[at + 1:]


# ---- the landed-tree check reads every commit since the first grant, merges among them -------------------------

def test_an_ungranted_commit_under_fifty_one_granted_ones_is_named(project):
    root = project["root"]
    _land(root, "first.txt", "granted\n")
    stray = _land(root, "stray.txt", "never granted\n", granted=False)
    for n in range(51):
        _land(root, "later.txt", f"granted {n}\n")

    assert A.commits_without_grant(root) == [stray]
    assert any(key == "landed-without-grant" and stray[:12] in text for key, text in cli.doctor_problems(project))


def test_a_merge_is_named_even_where_git_made_it(project, monkeypatch):
    """Two changes each reviewed alone can act together where neither review looked, and a grant binds one parent:
    ao lands no merge, and the landed-tree check names one as commit-check does (LEADS-GATE)."""
    root = project["root"]
    sent = _channels(monkeypatch)
    _land(root, "base.txt", "base\n")
    trunk = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _git(root, "checkout", "-q", "-b", "side")
    _land(root, "side.txt", "side\n")
    _git(root, "checkout", "-q", trunk)
    _land(root, "trunk.txt", "trunk\n")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merged = _git(root, "rev-parse", "HEAD")
    why = {}

    assert A.commits_without_grant(root, why) == [merged] and "ao lands no merge" in why[merged]
    assert any(key == "landed-without-grant" and merged[:12] in text and "a merge, which ao lands none of" in text
               for key, text in cli.doctor_problems(project))
    W.report_ungranted_commits(root, A.project_key(root))
    assert _alarm(root) and any(kind == "telegram" and merged[:12] in text for kind, text, _ in sent)


def test_the_landed_tree_check_runs_no_merge_driver(project, tmp_path):
    """`git merge-tree` runs the merge driver a repository configures for a path, a command an implementer can
    choose: the check named a merge by merging its parents again. It merges nothing now (LEADS-GATE)."""
    root = project["root"]
    _land(root, "base.txt", "base\n")
    trunk = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _git(root, "checkout", "-q", "-b", "side")
    _land(root, "base.txt", "side\n")
    _git(root, "checkout", "-q", trunk)
    _land(root, "base.txt", "trunk\n")
    subprocess.run([*GIT, "merge", "-q", "--no-ff", "-m", "merge side", "side"], cwd=root, capture_output=True)
    _land(root, "base.txt", "resolved by hand\n", granted=False)          # the conflict, committed as the merge
    ran = tmp_path / "driver-ran"
    _git(root, "config", "merge.planted.driver",
         f'"{Path(sys.executable).as_posix()}" -c "open(\'{ran.as_posix()}\', \'w\').close()"; exit 1')
    os.makedirs(os.path.join(root, ".git", "info"), exist_ok=True)
    with open(os.path.join(root, ".git", "info", "attributes"), "a", encoding="utf-8") as fh:
        fh.write("base.txt merge=planted\n")

    stray = A.commits_without_grant(root)

    assert not ran.exists()
    assert len(stray) == 1 and len(_git(root, "rev-list", "--parents", "-n", "1", stray[0]).split()) == 3


# ---- what it finds reaches a person, in every cycle --------------------------------------------------------------

@pytest.mark.parametrize("missing", ["the transcript", "the implementer"])
def test_a_cycle_that_ends_early_still_tells_a_person_of_a_commit_no_grant_bound(project, monkeypatch, missing):
    root = project["root"]
    sent = _channels(monkeypatch)
    _land(root, "a.txt", "granted\n")
    stray = _land(root, "b.txt", "around the hook\n", granted=False)
    if missing == "the implementer":
        with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({key: value for key, value in project.items() if key not in ("root", "implementer")}, fh)

    _cycle(root)

    assert "nothing to watch" in W._TRACE[-1]
    assert _alarm(root) and any(kind == "desktop" and stray[:12] in msg for kind, _, msg in sent)


def test_twenty_five_commits_landed_in_one_cycle_are_all_told(project, monkeypatch):
    root = project["root"]
    sent = _channels(monkeypatch)
    _land(root, "a.txt", "granted\n")
    strays = [_land(root, "b.txt", f"around the hook {n}\n", granted=False) for n in range(25)]

    W.report_ungranted_commits(root, A.project_key(root))

    assert A.commits_without_grant(root) == strays[::-1]
    (told,) = [text for kind, text, _ in sent if kind == "telegram"]
    assert "25 commit(s)" in told and strays[-1][:12] in told


def test_the_scheduled_doctor_pages_it_under_the_watchdogs_own_alarm(project, monkeypatch, capsys):
    root = project["root"]
    sent = _channels(monkeypatch)
    _land(root, "a.txt", "granted\n")
    stray = _land(root, "b.txt", "around the hook\n", granted=False)

    assert cli._doctor_check(project, page=True) == 1
    W.report_ungranted_commits(root, A.project_key(root))                    # the watchdog's raise of it

    (episode,) = _alarm(root)
    assert episode["count"] == 2
    assert len([msg for kind, _, msg in sent if kind == "desktop" and stray[:12] in msg]) == 1


def test_a_landed_tree_check_that_cannot_run_is_named_and_told_not_passed(project, monkeypatch):
    root = project["root"]
    sent = _channels(monkeypatch)
    _land(root, "a.txt", "granted\n")
    _land(root, "b.txt", "around the hook\n", granted=False)
    lines = open(_authority(root), "rb").read().splitlines(keepends=True)
    with open(_authority(root), "wb") as fh:
        fh.write(b'{"not": "a row ao wrote"\n' + b"".join(lines[1:]))

    found = dict(cli.doctor_problems(project))
    W.report_ungranted_commits(root, A.project_key(root))

    assert "cannot run" in found["landed-without-grant"]
    assert _alarm(root) and any(kind == "telegram" and "cannot run" in text for kind, text, _ in sent)


# ---- independent review: references, interrupted chains and absent ledgers ------------------------------------

@pytest.mark.parametrize("source", ["grant", "verification"])
@pytest.mark.parametrize("operation", ["references", "prune", "command"])
def test_sealed_review_references_survive_every_prune_entrypoint(project, source, operation):
    from tests.test_review_retention import _artefact, _prune
    root = project["root"]
    kept, loose = "2026-08-01-100000-kept.md", "2026-08-01-100001-loose.md"
    kept_path, loose_path = _artefact(root, kept), _artefact(root, loose)
    if source == "grant":
        A.record_authority(root, True, [], "sha256:t", "V-1", "C-kept", review=kept)
        _grants(root, 2)
        storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)
    else:
        for n in range(3):
            A.record_verification(root, {"id": f"V-{n}", "at": n, "review": kept if n == 0 else None})
        storage.seal_chained_jsonl(os.path.join(root, ".ao/ledger/verifications.jsonl"),
                                   A.VERIFICATION_CHAIN, keep=1, legacy_prefix=True)
    if operation == "references":
        assert source in A.review_artefact_references(root, project)[kept]
    elif operation == "prune":
        assert A.prune_review_artefacts(root, project, 30, apply=True)["moved"] == [loose]
    else:
        _prune(project, apply=True)
    assert os.path.exists(kept_path)
    assert os.path.exists(loose_path) == (operation == "references")


def _interrupted_opt_in(root, stop, keep=3):
    for n in range(6):
        A.record_opt_in(root, "features.probe", bool(n % 2), "Person")
    path = A.opt_ins_path(root)

    def interrupt(step):
        if step == stop:
            raise OSError("interrupted seal")

    with pytest.raises(OSError, match="interrupted seal"):
        storage.seal_chained_jsonl(path, A.OPT_IN_CHAIN, keep=keep, _checkpoint=interrupt)
    return path


@pytest.mark.parametrize("stop", ["intent-recorded", "ledger-rewritten"])
@pytest.mark.parametrize("damage", ["first payload", "middle pointer", "middle non-object", "last non-object"])
def test_settlement_refuses_every_broken_retained_chain_without_changing_its_intent(project, stop, damage):
    path = _interrupted_opt_in(project["root"], stop)
    rows = [json.loads(line) for line in Path(path).read_bytes().splitlines()]
    if damage == "first payload":
        rows[0]["by"] = "a different person"
    elif damage == "middle pointer":
        rows[1]["previous"] = "sha256:wrong"
    else:
        rows[-1 if damage == "last non-object" else 1] = ["not a row"]
    Path(path).write_bytes(b"".join(storage._encode_jsonl(row) for row in rows))
    before = Path(storage.checkpoint_path()).read_bytes()

    with pytest.raises(storage.LedgerCorruption):
        A.recorded_opt_in(project["root"], "features.review")

    assert Path(storage.checkpoint_path()).read_bytes() == before
    assert not Path(storage.seal_path(path)).exists()


@pytest.mark.parametrize("reader", ["live", "history", "opt-in", "seal"])
@pytest.mark.parametrize("intent", ["valid", "missing before", "zero count"])
@pytest.mark.parametrize("removed", ["ledger", "directory"])
def test_deleted_ledger_with_checkpoint_intent_is_refused_under_lock(project, monkeypatch, reader, intent, removed):
    from tests.test_leads_adapters_5 import _watch_opt_in_settlement
    path = _interrupted_opt_in(project["root"], "ledger-rewritten")
    Path(path).unlink()                     # an absent setting is not an absent ledger
    if removed == "directory":
        Path(path).parent.rename(Path(path).parent.with_name("ledger-moved"))
    if intent != "valid":
        def damage(entry):
            entry["count"] = 0
            if intent == "missing before":
                entry["intent"].pop("before")
            else:
                entry["intent"]["before"]["count"] = 0
            return entry
        storage._update_checkpoint(path, damage)
    before = Path(storage.checkpoint_path()).read_bytes()
    settled = _watch_opt_in_settlement(monkeypatch, path)
    reads = {"live": lambda: storage.read_chained_jsonl(path, A.OPT_IN_CHAIN),
             "history": lambda: storage.read_chained_history(path, A.OPT_IN_CHAIN),
             "opt-in": lambda: A.recorded_opt_in(project["root"], "features.review"),
             "seal": lambda: storage.seal_chained_jsonl(path, A.OPT_IN_CHAIN, keep=1)}

    with pytest.raises(storage.LedgerCorruption):
        reads[reader]()

    assert settled == [path] and Path(storage.checkpoint_path()).read_bytes() == before
    assert not Path(path).exists() and not Path(storage.seal_path(path)).exists()


@pytest.mark.parametrize("evidence", ["checkpoint", "seal", "both"])
@pytest.mark.parametrize("reader", ["history", "archives", "audit", "report"])
def test_missing_recorded_authority_is_unreadable_in_every_history_reader(project, evidence, reader):
    from tests.test_evidence import _tool
    root = project["root"]
    _grants(root, 3)
    path = _authority(root)
    if evidence != "checkpoint":
        storage.seal_chained_jsonl(path, A.AUTHORITY_CHAIN, keep=1)
    if evidence == "seal":
        storage._update_checkpoint(path, lambda entry: {})
    Path(path).unlink()
    if reader == "audit":
        grants, why = A.ledger_grants(root)
        assert grants is None and why and path in why
        audit = A.trailer_audit(root, "HEAD")
        assert not audit["ledger"] and path in audit["unreadable"]
    else:
        reads = {"history": lambda: A.authority_history(root),
                 "archives": lambda: storage.sealed_rows(path, A.AUTHORITY_CHAIN),
                 "report": lambda: _tool()._ledger(root, "authority.jsonl", A.AUTHORITY_CHAIN)}
        with pytest.raises(storage.LedgerCorruption):
            reads[reader]()


@pytest.mark.parametrize("sealed", ["verifications and grants", "reviews", "waivers"])
def test_slice_outcome_size_survives_sealed_verifications_and_grants(project, sealed):
    from tests.test_outcomes import _review
    root = project["root"]
    _review(root, "r1.md", "APPROVED", "S1")
    waiver = A.waive(root, "review", "S1", "reviewer unavailable", "Person") if sealed == "waivers" else None
    size = {"paths": 1, "kinds": {"product": {"paths": 1, "added": 12, "deleted": 3}}}
    A.record_verification(root, {"id": "V-1", "candidate": {"digest": "sha256:c1"}, "candidate_size": size})
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-landed", review=None if waiver else "r1.md",
                       waiver=waiver["id"] if waiver else None,
                       candidate={"digest": "sha256:c1"})
    _git(root, "-c", "core.hooksPath=", "commit", "--allow-empty", "-m", "land\n\nAo-Grant: C-landed")
    before = A.slice_outcomes(root)
    assert before[0]["size"] == size
    A.record_verification(root, {"id": "V-later", "candidate": {"digest": "sha256:later"}})
    A.record_authority(root, False, ["refused"], "sha256:later", "V-later", "C-later")
    if sealed == "verifications and grants":
        storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)
        storage.seal_chained_jsonl(os.path.join(root, ".ao/ledger/verifications.jsonl"), A.VERIFICATION_CHAIN,
                                   keep=1, legacy_prefix=True)
    elif sealed == "reviews":
        _review(root, "r2.md", "APPROVED", "S2", digest="sha256:later")
        storage.seal_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN, keep=1)
    else:
        A.close_waiver(root, waiver["id"], "reviewed")
        storage.seal_chained_jsonl(A.waivers_path(root), A.WAIVER_CHAIN, keep=1, legacy_prefix=True)

    assert A.slice_outcomes(root) == before


@pytest.mark.parametrize("reader", ["archives", "report"])
@pytest.mark.parametrize("missing", [False, True])
def test_read_only_history_refuses_an_unsettled_checkpoint_intent(project, reader, missing):
    from tests.test_evidence import _tool
    path = _interrupted_opt_in(project["root"], "intent-recorded")
    if missing:
        Path(path).unlink()
    before = Path(storage.checkpoint_path()).read_bytes()
    with pytest.raises(storage.LedgerCorruption, match="settlement"):
        if reader == "archives":
            storage.sealed_rows(path, A.OPT_IN_CHAIN)
        else:
            _tool()._ledger(project["root"], "opt-ins.jsonl", A.OPT_IN_CHAIN)
    assert Path(storage.checkpoint_path()).read_bytes() == before


@pytest.mark.parametrize("reader", ["archives", "report"])
@pytest.mark.parametrize("missing", [False, True])
def test_read_only_history_names_an_in_tree_intent_as_one_ao_did_not_begin(project, reader, missing):
    from tests.test_evidence import _tool
    root = project["root"]
    _grants(root, 3)
    path = _authority(root)
    pending = Path(storage.seal_path(path) + ".pending")
    pending.write_text("{}", encoding="utf-8")
    if missing:
        Path(path).unlink()
    before = Path(storage.checkpoint_path()).read_bytes()
    with pytest.raises(storage.LedgerCorruption, match="did not begin") as refused:
        if reader == "archives":
            storage.sealed_rows(path, A.AUTHORITY_CHAIN)
        else:
            _tool()._ledger(root, "authority.jsonl", A.AUTHORITY_CHAIN)
    assert str(pending) in str(refused.value) and pending.read_text(encoding="utf-8") == "{}"
    assert Path(storage.checkpoint_path()).read_bytes() == before


def test_pruning_refuses_tampered_sealed_references_and_keeps_every_artefact(project, capsys):
    from tests.test_review_retention import _artefact, _prune
    root = project["root"]
    kept = "2026-08-01-100000-kept.md"
    kept_path = _artefact(root, kept)
    loose_path = _artefact(root, "2026-08-01-100001-loose.md")
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-kept", review=kept)
    _grants(root, 2)
    storage.seal_chained_jsonl(_authority(root), A.AUTHORITY_CHAIN, keep=1)
    _edit_archive(_authority(root), "token", "C-kept", review="another.md")
    with pytest.raises(storage.LedgerCorruption):
        A.review_artefact_references(root, project)
    with pytest.raises(storage.LedgerCorruption):
        A.prune_review_artefacts(root, project, 30, apply=True)
    _prune(project, apply=True)
    assert "every artefact: what rests on them cannot be read" in capsys.readouterr().out
    assert os.path.exists(kept_path) and os.path.exists(loose_path)
