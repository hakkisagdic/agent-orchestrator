"""What ao can reach of an adapter's transcript, and what it only has declared to it.

A transcript block says what a harness's records look like. Whether ao can load them at all is a
different declaration: a session is resolved from a store of one of the kinds this ao dispatches
on, and an adapter that names none of them is read by nothing however precisely it is shaped. A
check that looked only for the shape reported that as a capability, so it reports the two apart,
and `ao adapters` marks the column.
"""
import json
import os
import sys

from ao import cli, lib as A

ADAPTERS = os.path.join(os.path.dirname(os.path.abspath(A.__file__)), "adapters")


def _conformed(adapter, tmp_path):
    harness = tmp_path / "harness"
    source = open(os.path.join(os.path.dirname(A.__file__), "conformance_harness.py"), encoding="utf-8").read()
    harness.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    harness.chmod(0o755)
    return dict((capability, (state, detail))
                for capability, state, detail in A.conform_adapter(adapter, str(harness), str(tmp_path)))


SHAPED = {"id": "shaped", "name": "Shaped", "verified": "untested",
          "send": {"argv": ["shaped", "run", "{prompt}"]},
          "transcript": {"kind": "jsonl", "path": "~/shaped/{session}.jsonl"}}


def test_a_transcript_with_no_store_ao_resolves_is_declared_and_said_so(tmp_path):
    assert A.session_store_reachable(SHAPED) is False
    assert _conformed(SHAPED, tmp_path)["transcript"][0] == "declared"

    reached = dict(SHAPED, sessions={"kind": "escaped-cwd", "dir": "~/shaped", "transcript": "{session}.jsonl"})
    assert A.session_store_reachable(reached) is True
    state, detail = _conformed(reached, tmp_path)["transcript"]
    assert state == "pass", detail


def test_the_store_kinds_adapters_declare_are_the_ones_the_resolver_dispatches_on():
    declared = {str(name)[:-5]: json.load(open(os.path.join(ADAPTERS, name), encoding="utf-8"))
                for name in os.listdir(ADAPTERS) if name.endswith(".json") and name != "vendors.json"}
    kinds = {(adapter.get("sessions") or {}).get("kind") for adapter in declared.values()} - {None}
    assert kinds == set(A.SESSION_STORE_KINDS), \
        f"adapters declare {sorted(kinds)}; ao resolves {sorted(A.SESSION_STORE_KINDS)}"


def test_an_adapter_ao_reaches_resolves_a_pinned_session_and_one_it_does_not_resolves_nothing():
    # A workspace-meta store is read by finding the session on disk, so it is not this case's:
    # an id that exists nowhere resolves to nothing there even though its kind is one ao reaches.
    for ident, expected in (("opencode", True), ("claude-code", True), ("command-code", False)):
        cfg = {"root": "/tmp/ao-reachability", "implementer": {"session": "ses_pinned_one", "adapter": ident}}
        reached = A.role_session_paths(cfg, "implementer")[0] is not None
        assert reached is expected, f"{ident} resolves to nothing for a store of kind " \
            f"{(A.load_adapter(ident).get('sessions') or {}).get('kind')!r}"


def test_a_store_of_a_kind_ao_does_not_dispatch_on_resolves_to_nothing(monkeypatch):
    foreign = {"id": "foreign", "name": "Foreign", "verified": "untested",
               "send": {"argv": ["foreign", "run", "{prompt}"]},
               "transcript": {"kind": "jsonl", "path": "~/foreign/{session}.jsonl"},
               "sessions": {"kind": "a-store-this-ao-cannot-open", "dir": "~/foreign"}}
    monkeypatch.setattr(A, "load_adapter", lambda ident, *a, **kw: foreign)
    cfg = {"root": "/tmp/ao-reachability", "implementer": {"session": "ses_pinned_one", "adapter": "foreign"}}
    assert A.role_session_paths(cfg, "implementer") == (None, None)
    assert A.session_store_reachable(foreign) is False


def test_the_listing_marks_the_declared_transcripts_ao_reaches_by_nothing(capsys):
    cli.main(["adapters"])
    rows = dict((line.split()[0], line) for line in capsys.readouterr().out.splitlines() if len(line.split()) > 4)
    assert "not reached" in rows["command-code"], rows.get("command-code")
    for landed in ("opencode", "kiro", "claude-code", "qoder"):
        assert "not reached" not in rows[landed], rows[landed]
