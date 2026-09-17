"""ao writes English unless a project chooses Turkish, and reads both languages always (LANGUAGE-FILES).

Every product review of ao found Turkish in the repository it governed and in its agents'
instructions: the files `ao init` writes and the markers its mail carries. Both now come from
src/ao/language.py in the project's `language` setting - English by default, Turkish with `tr` -
and the markers of both languages are read in every project, so the urgent notes, decision requests
and handoffs an existing Turkish project already holds are found, surfaced and escalated as before.

A guard keeps a Turkish letter out of every string literal under src/ao outside the catalogue, save
the texts the next two slices convert, each named with the slice that removes it. The allowlist is
meant to shrink to nothing: an entry that no longer matches anything fails until it is dropped.
"""
import ast
import json
import os
import re
import string
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, language, lib as A, mcp, settings as S, telegram, watchdog as W
from tests import conftest
from tests.scenarios import World
from tests.test_profiles import _init_args

ROOT = Path(__file__).resolve().parent.parent
TURKISH = re.compile("[çğıİöşüÇĞÖŞÜ]")
CATALOGUE = "src/ao/language.py"
INIT_FILES = (".ao/authority.md", ".ao/board.md", ".ao/backlog.md", "agent-mail/README.md")
STEERING = ".kiro/steering/ao-coordination.md"      # the coordination file init writes for the kiro adapter
PROMPTS, OUTPUT = "LANGUAGE-PROMPTS", "LANGUAGE-OUTPUT"

# (path, top-level definition, a piece of the literal - "" for every literal the definition holds):
# the slice that converts it and removes the entry.
ALLOWLIST = {
    # the digest, the handoff, e-mail and Telegram setup and messages, notifications, decisions text
    ("src/ao/email.py", "SETUP", ""): OUTPUT,
    ("src/ao/mcp.py", "call", "uygulayıcı takıldı"): OUTPUT,
    ("src/ao/telegram.py", "poll", "karar bulunamadı"): OUTPUT,
    ("src/ao/telegram.py", "poll", "Kaydedildi"): OUTPUT,
    ("src/ao/telegram.py", "_command", ""): OUTPUT,
    ("src/ao/parts/cli_channels.py", "cmd_telegram", ""): OUTPUT,
    ("src/ao/parts/cli_channels.py", "_decision_text", ""): OUTPUT,
    ("src/ao/parts/cli_channels.py", "cmd_handoff", ""): OUTPUT,
    ("src/ao/parts/cli_project.py", "cmd_digest", ""): OUTPUT,
    ("src/ao/parts/cli_project.py", "cmd_decide", ""): OUTPUT,
    ("src/ao/parts/cli_project.py", "cmd_hold", ""): OUTPUT,
    ("src/ao/parts/cli_project.py", "cmd_email", ""): OUTPUT,
    ("src/ao/parts/lib_state.py", "ask", ""): OUTPUT,
    ("src/ao/watchdog.py", "notify", ""): OUTPUT,
    ("src/ao/watchdog.py", "touch_architect_quota", ""): OUTPUT,
    ("src/ao/watchdog.py", "escalate", ""): OUTPUT,
    ("src/ao/watchdog.py", "tell_retried_wake", ""): OUTPUT,
    ("src/ao/watchdog.py", "_cycle_impl", "kotası tükendi"): OUTPUT,
}


def _plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _choose(project, chosen):
    """The project with `language` set, in the config every reader loads from disk."""
    cfg = dict(project, language=chosen)
    with open(os.path.join(project["root"], ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    return cfg


def _mail(cfg, name, body):
    path = os.path.join(cfg["root"], cfg["mailbox"], name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _class(cfg, name):
    path = os.path.join(cfg["root"], cfg["mailbox"], name)
    return A.mail_class(name, A.mail_meta(path), _read(path))


def _kind(cfg, name):
    """The kind of the anomaly the watchdog raises for a report, or None."""
    return next((a["kind"] for a in A.anomalies(cfg["root"], cfg, {}, 0, 60) if name in (a.get("reports") or [])), None)


# ---- ao init -----------------------------------------------------------------------------------

def _init(root, capsys):
    """`ao init` in a fresh repository for the kiro adapter, with no reviewer to probe."""
    root.mkdir(exist_ok=True)
    subprocess.run([conftest.GIT, "init", "-q"], cwd=root, check=True)
    assert cli.cmd_init({"root": str(root)}, _init_args(name="acme-api", profile=None, agent="kiro")) == 0, \
        _plain(capsys.readouterr().out)
    return {rel: _read(root / rel) for rel in INIT_FILES + (STEERING,)}


def test_init_writes_english_unless_the_project_or_the_machine_chose_turkish(project, tmp_path, capsys):
    english = _init(tmp_path / "english", capsys)

    assert english[".ao/authority.md"].startswith("# Authority — the canonical source\n")
    assert english[".ao/board.md"] == language.TEXTS["init.board"]["en"]
    assert english[".ao/backlog.md"].startswith("# acme-api — pre-authorised work queue\n")
    assert "`DECISION REQUIRED`" in english[".ao/authority.md"] and "`DECISION REQUIRED`" in english[".ao/backlog.md"]
    assert "`URGENT`" in english["agent-mail/README.md"] and "`## URGENT`" in english["agent-mail/README.md"]
    assert "`HANDOFF`" in english["agent-mail/README.md"] and "(`## URGENT`)" in english[STEERING]
    assert [rel for rel, text in english.items() if TURKISH.search(text)] == []
    # Every rule and command the Turkish files carry is in the English ones.
    for key in ("init.authority", "init.board", "init.backlog", "init.mail-readme"):
        commands = {span for span in re.findall(r"`([^`]*)`", language.TEXTS[key]["tr"]) if not TURKISH.search(span)}
        assert commands - {"ACIL", "DEVIR"} <= set(re.findall(r"`([^`]*)`", language.TEXTS[key]["en"])), key

    chosen = tmp_path / "chosen"
    (chosen / ".ao").mkdir(parents=True)
    (chosen / ".ao" / "config.json").write_text(json.dumps({"project": "acme-api", "language": "tr"}), encoding="utf-8")
    turkish = _init(chosen, capsys)

    # A Turkish project is given what ao init wrote before English was the default, byte for byte.
    assert turkish[".ao/authority.md"] == language.TEXTS["init.authority"]["tr"]
    assert turkish[".ao/board.md"] == language.TEXTS["init.board"]["tr"]
    assert turkish[".ao/backlog.md"] == language.TEXTS["init.backlog"]["tr"].format(name="acme-api")
    assert turkish[".ao/backlog.md"].startswith("# acme-api — önceden yetkilendirilmiş iş kuyruğu\n")
    assert "`KARAR GEREKLİ`" in turkish[".ao/authority.md"] and "`## ACİL`" in turkish["agent-mail/README.md"]
    assert "`DEVIR`" in turkish["agent-mail/README.md"] and "(`## ACİL`)" in turkish[STEERING]

    with open(S.machine_path(), "w", encoding="utf-8") as fh:
        json.dump({"language": "tr"}, fh)
    assert _init(tmp_path / "machine", capsys) == turkish


def test_init_keeps_the_files_a_project_has_whatever_its_language_now_is(project, tmp_path, capsys):
    root = tmp_path / "kept"
    first = _init(root, capsys)
    stored = json.loads((root / ".ao" / "config.json").read_text(encoding="utf-8"))
    (root / ".ao" / "config.json").write_text(json.dumps(dict(stored, language="tr")), encoding="utf-8")
    (root / "agent-mail" / "README.md").unlink()

    again = _init(root, capsys)

    assert {rel: text for rel, text in again.items() if rel != "agent-mail/README.md"} == \
        {rel: text for rel, text in first.items() if rel != "agent-mail/README.md"}
    assert again["agent-mail/README.md"] == language.TEXTS["init.mail-readme"]["tr"].format()
    assert "kept   .ao/authority.md" in _plain(capsys.readouterr().out)


# ---- the catalogue -----------------------------------------------------------------------------

def _fields(text):
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name is not None})


def test_every_catalogue_key_has_both_languages_and_they_take_the_same_values():
    assert language.LANGUAGES == S.CHOICES["language"] and language.LANGUAGES[0] == S.default("language") == "en"

    for key, entry in language.TEXTS.items():
        assert sorted(entry) == sorted(language.LANGUAGES), key
        assert all(isinstance(text, str) and text.strip() for text in entry.values()), key
        assert len({tuple(_fields(text)) for text in entry.values()}) == 1, key
    for key, entry in language.MARKERS.items():
        assert sorted(entry) == sorted(language.LANGUAGES), key
        assert all(isinstance(form, str) and form.strip() for form in entry.values()), key
        assert len(set(entry.values())) == len(entry), key
    for key, entry in language.WORDS.items():
        assert sorted(entry) == sorted(language.LANGUAGES), key
        assert all(isinstance(words, tuple) and all(isinstance(word, str) and word for word in words)
                   for words in entry.values()), key
    assert [key for key, entry in language.TEXTS.items() if TURKISH.search(entry["en"])] == []


def test_a_text_is_the_projects_language_the_machines_choice_or_english(project, monkeypatch):
    monkeypatch.setitem(language.TEXTS, "sample", {"en": "{n} left", "tr": "{n} kaldı"})
    monkeypatch.setitem(language.TEXTS, "english-only", {"en": "only {n}"})

    assert language.text(project, "sample", n=2) == "2 left"
    assert language.text(dict(project, language="tr"), "sample", n=2) == "2 kaldı"
    assert language.text(dict(project, language="tr"), "english-only", n=2) == "only 2"
    assert language.text(dict(project, language="de"), "sample", n=2) == "2 left"      # not a choice: the default
    with open(S.machine_path(), "w", encoding="utf-8") as fh:
        json.dump({"language": "tr"}, fh)
    assert language.text(project, "sample", n=2) == "2 kaldı" and language.text(None, "sample", n=2) == "2 kaldı"
    assert language.text(dict(project, language="en"), "sample", n=2) == "2 left"


# ---- markers: written in the project's language, read in both ---------------------------------

def test_the_readers_hold_every_form_ao_ever_wrote():
    assert set(A.URGENT_MARKERS) == {"## URGENT", "## ACİL", "## STOP", "## DUR"}
    assert set(language.lowered("decision", "urgent")) == {"## decision required", "## karar gerekli",
                                                           "## urgent", "## acil"}
    assert {"decision", "karar"} <= set(A._DECISION_KINDS) and {"report", "rapor"} <= set(A._FYI_KINDS)
    assert {"human", "insan"} <= set(A.HUMAN_WAITING)
    assert {"blocker:", "blockers:", "engel:", "engeller:"} <= set(language.words("blockers"))
    assert {"none", "yok", "hiç"} <= set(language.words("no-blockers"))


@pytest.mark.parametrize("chosen", ["en", "tr"])
def test_every_marker_ao_writes_is_the_projects_and_every_reader_knows_it(project, monkeypatch, tmp_path, capsys,
                                                                           chosen):
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))     # nothing reaches a phone
    cfg = _choose(project, chosen)
    other = dict(cfg, language=next(lang for lang in language.LANGUAGES if lang != chosen))
    form = {key: forms[chosen] for key, forms in language.MARKERS.items()}
    foreign = {key: forms[other["language"]] for key, forms in language.MARKERS.items()}

    note = A.note(cfg["root"], cfg, None, "stop S3", "hold the deploy", urgent=True)
    body = _read(os.path.join(cfg["root"], cfg["mailbox"], note))
    assert f"-{form['urgent-kind']}-" in note and f"-{foreign['urgent-kind']}-" not in note
    assert f"\n{form['urgent']}\n" in body and foreign["urgent"] not in body
    assert A.mail_meta(os.path.join(cfg["root"], cfg["mailbox"], note))["kind"] == form["urgent-kind"].lower()
    for reader in (cfg, other):
        assert [m["id"] for m in A.urgent_messages(cfg["root"], reader)] == [note]
    os.remove(os.path.join(cfg["root"], cfg["mailbox"], note))

    report = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty", "needs": "next slice"}, cfg, False)
    path = os.path.join(cfg["root"], cfg["mailbox"], report["written"])
    assert f"\n{form['decision']}\n" in _read(path) and foreign["decision"] not in _read(path)
    monkeypatch.setattr(A, "agent_pids", lambda *args, **kwargs: [])
    for reader in (cfg, other):
        assert _class(reader, report["written"]) == "needs-decision"
        assert _kind(reader, report["written"]) == "decision-requested"
        assert A.waiting_on_architect(cfg["root"], reader)[0] == report["written"]
    again = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty", "needs": "next slice"}, cfg, False)
    assert again["repeated"] == 2 and re.search(rf"^{form['repeat']}: 2 · ", _read(path), re.M)
    assert mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty"}, other, False)["repeated"] == 3
    assert re.search(rf"^{foreign['repeat']}: 3 · ", _read(path), re.M) and form["repeat"] + ":" not in _read(path)
    os.remove(path)

    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    monkeypatch.setattr(A, "account_usage", lambda *args, **kwargs: None)
    assert cli.cmd_handoff(cfg, SimpleNamespace(reason=None, no_send=True)) == 0
    [handoff] = A.mailbox(cfg["root"], cfg["mailbox"])
    assert handoff.endswith(f"-to-anyone-{form['handoff-kind']}.md")
    os.remove(os.path.join(cfg["root"], cfg["mailbox"], handoff))

    updates = [{"update_id": 7, "message": {"text": "stop the deploy", "chat": {"id": 42}, "from": {"username": "a"}}}]
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params:
                        {"ok": True, "result": updates} if method == "getUpdates" else {"ok": True})
    monkeypatch.setattr(telegram, "_offset_path", lambda: str(tmp_path / "telegram-offset"))
    [phoned] = telegram.poll(cfg["root"], cfg)["written"]
    assert f"-human-to-kiro-{form['urgent-kind']}-stop-the-deploy.md" in phoned
    assert f"\n{form['urgent']}\n" in _read(os.path.join(cfg["root"], cfg["mailbox"], phoned))
    for reader in (cfg, other):
        assert [m["id"] for m in A.urgent_messages(cfg["root"], reader)] == [phoned]


# ---- mail written in Turkish before the change -------------------------------------------------

def test_an_urgent_note_written_in_turkish_still_holds_the_implementer_in_an_english_project(project, monkeypatch,
                                                                                            capsys):
    assert language.of(project) == "en"
    name = "20260916-1201-fable-to-kiro-ACIL-stop-s3.md"
    _mail(project, name, "---\nao: 1\nkind: acil\nfrom: fable\nto: kiro\n---\n# stop S3\n\n## ACİL\n\nhold it\n")
    english = "20260916-1202-fable-to-kiro-URGENT-stop-s4.md"
    _mail(project, english, "---\nao: 1\nkind: urgent\nfrom: fable\nto: kiro\n---\n# stop S4\n\n## URGENT\n\nhold\n")
    monkeypatch.setenv("AO_ROLE", "implementer")

    assert [m["id"] for m in A.urgent_messages(project["root"], project)] == [name, english]
    cli._urgent_banner(project)                                     # what ao lock and ao verify print first
    lines = _plain("\n".join(cli._mailbox_banner(project)))        # what ao status, ao board and ao mail show
    printed = _plain(capsys.readouterr().out)

    assert "2 URGENT message(s) from the architect" in printed and name in printed
    assert "URGENT for the implementer: stop S3" in lines and "URGENT for the implementer: stop S4" in lines
    assert _class(project, name) == _class(project, english) == "needs-read"
    assert [m["id"] for m in mcp.call("ao_inbox", {}, project, False)["messages"]] == [name, english]


@pytest.mark.parametrize("chosen", ["en", "tr"])
@pytest.mark.parametrize("heading", ["## KARAR GEREKLİ", "## DECISION REQUIRED"])
def test_a_decision_request_in_either_language_wakes_the_architect_in_either_project(project, monkeypatch, tmp_path,
                                                                                      chosen, heading):
    cfg = _choose(project, chosen)
    world = World(cfg, monkeypatch, tmp_path)
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", f"# queue empty\n\n{heading}\n")

    assert _class(cfg, "20260916-1200-kiro-to-fable-BLOCKED-queue.md") == "needs-decision"
    assert A.waiting_on_architect(cfg["root"], cfg)[0] == "20260916-1200-kiro-to-fable-BLOCKED-queue.md"
    world.cycle(dry_run=False)

    wakes = [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]
    assert len(wakes) == 1
    assert "20260916-1200-kiro-to-fable-BLOCKED-queue.md" in W.load_state(world.root)["handed"]


def test_a_handoff_and_the_kinds_named_in_turkish_are_listed_and_classed_as_before(project):
    names = {"devir": "20260916-1200-fable-to-anyone-DEVIR.md",
             "handoff": "20260916-1201-fable-to-anyone-HANDOFF.md",
             "karar": "20260916-1202-kiro-to-fable-KARAR-store.md",
             "decision": "20260916-1203-kiro-to-fable-DECISION-x.md",
             "rapor": "20260916-1204-kiro-to-fable-RAPOR-s1.md",
             "report": "20260916-1205-kiro-to-fable-REPORT-s2.md"}
    for name in names.values():
        _mail(project, name, "# a message\n\nwritten by hand\n")

    assert A.mailbox(project["root"], project["mailbox"]) == sorted(names.values())
    assert {names["devir"], names["handoff"]} <= set(A.implementer_inbox(project["root"], project))
    assert _class(project, names["devir"]) == _class(project, names["handoff"]) == "needs-read"
    assert _class(project, names["karar"]) == _class(project, names["decision"]) == "needs-decision"
    assert _class(project, names["rapor"]) == _class(project, names["report"]) == "fyi"


def test_the_words_a_turkish_report_or_board_uses_are_read_as_before(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "agent_pids", lambda *args, **kwargs: [])
    for name, line in (("20260916-1200-kiro-to-fable-DONE-a.md", "Engel: yok"),
                       ("20260916-1201-kiro-to-fable-DONE-b.md", "Blockers: none"),
                       ("20260916-1202-kiro-to-fable-STATUS-c.md", "Engeller: hangi depo?"),
                       ("20260916-1203-kiro-to-fable-STATUS-d.md", "Blockers: which store?")):
        _mail(project, name, f"# {name}\n\n{line}\n")
    kinds = {a["kind"]: a["reports"] for a in A.anomalies(root, project, {}, 0, 60) if a.get("reports")}
    assert sorted(kinds["report-waiting"]) == ["20260916-1200-kiro-to-fable-DONE-a.md",
                                               "20260916-1201-kiro-to-fable-DONE-b.md"]
    assert sorted(kinds["decision-requested"]) == ["20260916-1202-kiro-to-fable-STATUS-c.md",
                                                   "20260916-1203-kiro-to-fable-STATUS-d.md"]

    board = os.path.join(root, ".ao", "board.md")
    text = _read(board).replace("## blocked\n", "## blocked\n- [S1] a · waiting: insan\n- [S2] b · waiting: human\n"
                                                "- [S3] c · waiting: architect\n")
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text)
    assert [item["id"] for item in A.human_waits(root)] == ["S1", "S2"]
    assert A.declared_paths({"notes": {"paths": "src/a.py (yeni), src/b.py (new), src/c.py"}}) == \
        [("src/a.py", True), ("src/b.py", True), ("src/c.py", False)]
    assert A._GREEN_CLAIM.search("testler yeşil") and A._GREEN_CLAIM.search("all tests pass")
    assert A._RED_ADMISSION.search("iki test kırmızı") and A._RED_ADMISSION.search("3 failed")
    assert A._recall_words("bu dilim için daha çok test") == {"dilim", "test"}


def test_a_standing_report_counted_in_turkish_counts_on_in_an_english_project(project):
    path = _mail(project, "20260916-1200-kiro-to-fable-BLOCKED-queue-empty.md",
                 "# queue empty\n\n## KARAR GEREKLİ\n\nTekrar: 4 · son: 2026-09-16 12:00\n")
    os.utime(path, (1_700_000_000, 1_700_000_000))

    assert A.bump_repeat(path, project) == 5

    body = _read(path)
    assert re.search(r"^Repeat: 5 · last: ", body, re.M) and "Tekrar" not in body and "## KARAR GEREKLİ" in body
    assert os.path.getmtime(path) == 1_700_000_000


# ---- the guard ---------------------------------------------------------------------------------

def _literals(path):
    """(top-level definition, text) of each string literal in a module, an f-string read whole, docstrings left out."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    formatted = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    found = []
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = top.name
        elif isinstance(top, (ast.Assign, ast.AnnAssign)):
            targets = top.targets if isinstance(top, ast.Assign) else [top.target]
            scope = ",".join(getattr(target, "id", "?") for target in targets)
        else:
            scope = "<module>"
        for node in ast.walk(top):
            if isinstance(node, ast.JoinedStr):
                found.append((scope, "".join(part.value if isinstance(part, ast.Constant) else "{}"
                                             for part in node.values)))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings and id(node) not in formatted:
                found.append((scope, node.value))
    return found


def _turkish(root):
    """(path, definition, text) of every string literal under src/ao holding a Turkish letter, the catalogue aside."""
    return [(path.relative_to(root).as_posix(), scope, text)
            for path in sorted((Path(root) / "src" / "ao").rglob("*.py"))
            if path.relative_to(root).as_posix() != CATALOGUE
            for scope, text in _literals(path) if TURKISH.search(text)]


def _allowed(entry, found):
    path, scope, piece = entry
    return found[0] == path and found[1] == scope and piece in found[2]


def test_no_string_literal_holds_turkish_outside_the_catalogue_but_what_the_next_slices_convert():
    found = _turkish(ROOT)

    unlisted = [f"{path} {scope}: {text.strip()[:80]!r}" for path, scope, text in found
                if not any(_allowed(entry, (path, scope, text)) for entry in ALLOWLIST)]
    stale = [entry for entry in ALLOWLIST if not any(_allowed(entry, literal) for literal in found)]

    assert unlisted == [], "put the text in src/ao/language.py:\n" + "\n".join(unlisted)
    assert stale == [], f"converted, so drop these from ALLOWLIST: {stale}"
    assert set(ALLOWLIST.values()) <= {PROMPTS, OUTPUT}


def test_the_guard_reads_code_and_not_a_docstring_or_a_comment(tmp_path):
    sample = tmp_path / "src" / "ao" / "sample.py"
    sample.parent.mkdir(parents=True)
    sample.write_text('"""Bir modül: ## ACİL."""\n# bir yorum: ## ACİL\nHEADING = "## ACİL"\n\n\n'
                      'def told(n):\n    """Kaç kez söylendiği."""\n    return f"{n} kez söylendi"\n', encoding="utf-8")
    (tmp_path / "src" / "ao" / "language.py").write_text('TEXTS = {"x": {"tr": "kaldı"}}\n', encoding="utf-8")

    found = _turkish(tmp_path)

    assert found == [("src/ao/sample.py", "HEADING", "## ACİL"), ("src/ao/sample.py", "told", "{} kez söylendi")]
    assert _allowed(("src/ao/sample.py", "told", ""), found[1]) and not _allowed(("src/ao/sample.py", "told", ""), found[0])
    assert _allowed(("src/ao/sample.py", "told", "kez"), found[1])
    assert not _allowed(("src/ao/sample.py", "told", "dk"), found[1])
