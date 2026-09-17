"""ao instructs its agents in the project's language, a Turkish project's as it always did (LANGUAGE-PROMPTS).

The reviewer's prompt and the markers between its parts, a stand-in review request, the bug hunter's prompt, and
the nudge, wake and refill the watchdog sends were Turkish in every project. They come from src/ao/language.py in
the project's `language` now: English by default, and with `tr` the Turkish texts byte for byte, pinned here by
digest, so a Turkish project's agents read what they read before and its review prompt measures as it did. What
ao reads back is spelled the same in both languages: an answer carrying either language's headings is one verdict
with the same findings, a hunter's leads and a stand-in's nonce read the same, no code reads a marker back, and a
Turkish project's review cut off before the change resumes from its section journal.
"""
import hashlib
import json
import os
import re
import string
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, language, lib as A, storage
from tests.scenarios import World
from tests.test_bug_hunter import _repo as _hunted_repo
from tests.test_review_chain import _args, _fake, _repo_with_change
from tests.test_review_context import _package, _stage_test
from tests.test_review_sections import SCENARIOS
from tests.test_secondary_project import _secondary

ROOT = Path(__file__).resolve().parent.parent
TURKISH_LETTER = re.compile("[çğıİöşüÇĞÖŞÜ]")
LANGUAGES = [None, "tr"]            # a project that sets none, and one that chose Turkish

# sha256 of each Turkish text as ao gave it before LANGUAGE-PROMPTS, its placeholders unfilled. A change here is a
# change to what a Turkish project's agents read, and to how its review prompt measures: the claims that prompt
# leaves room for key the section journal an in-flight review resumes from. Change a digest only on purpose.
TURKISH = {
    "prompt.review": "7a6c3729fefefa5754896c3b350501c6447f2ed4f1dba9f4fbabfb72e96bf2b5",
    "prompt.review-candidate": "7bf44715ae9eb215bd3b32103c60c71fa279428f260f58022c0f3cf18615f3ea",
    "prompt.review-context": "9bfb71d0c42537524d6beab52e7c014721d8e9261e56cad906c29ac581b6cfb1",
    "prompt.review-section": "8063a940247fdbf8be4db5d8445f9945c817b3a0970c601e8bfb39155dc6ce60",
    "prompt.review-request": "1ab6ef78285dbd1b7ed1dd26dfeeb46c167013bde8bdb16d8702007f6c792072",
    "prompt.hunt": "6dfafb32102cade4747e8ff833ee05d5591e5409948142a3bc64331b50042553",
    "prompt.nudge": "9282c72232f39ab53301411c00144e59a375e7b3d4b7b9b309ba6578d82b15b4",
    "prompt.nudge-parked": "ddaa03c5459c39bbecb304588aef4f67fe30245fbbf041601a1e0bd45e012b44",
    "prompt.nudge-secondary": "635dc74bb018c36284083d373ddf93cef5d2fb6d222c63dd19c698dec7ffa25a",
    "prompt.nudge-editing": "5c84dd61201804d13ac3ec00ea81b5b9013fbd824bf0aea356f00dc13ba44e6a",
    "prompt.wake": "f64e9e18ae0e753f4048a3de7f3bb60679d32ac32ae1dd9e32101c3b10e09ad6",
    "prompt.refill": "049bdca4daf5e861c68d46f45663e87fd722f2ad7d665896e8cb00d0dcfef71e",
}
REVIEW_MARKERS = ("prompt.review-candidate", "prompt.review-context", "prompt.review-section")
HEADINGS = ("urgent", "stop", "decision")           # the markers a prompt tells an agent to write
# The words a reader acts on, spelled the same in both languages: the answer schema, the board's and the
# protocol's words, and what an agent may not do.
PROTOCOL = re.compile(r"(?<![\w-])(VERDICT: APPROVED|NEEDS_CHANGES|BLOCKER: <n>|HIGH: <n>|MEDIUM: <n>|LOW: <n>|"
                      r"- \[SEVERITY\]|READY|NONCE: |ANOMALY|AO_ROLE=architect|blocked|shape|agent-mail|inbox|"
                      r"PUSH|push|force-push|PR|local commit)(?![\w-])")
APPROVED = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"
# A reviewer that keeps every prompt it is handed, fails a section named in AO_TEST_FAIL, and otherwise answers
# with the file AO_TEST_ANSWER names.
REVIEWER = """
import json, os, re, sys
prompt = sys.argv[1]
with open(os.environ["AO_TEST_PROMPTS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(prompt) + "\\n")
asked = re.findall(r"Scenario \\d+", prompt)
if asked and asked[-1] in os.environ["AO_TEST_FAIL"].split(","):
    sys.exit(1)
with open(os.environ["AO_TEST_ANSWER"], encoding="utf-8") as fh:
    sys.stdout.write(fh.read())
"""
# A hunter that keeps its prompt and names one lead.
HUNTER = ("import os, sys\n"
          "open(os.environ['AO_TEST_PROMPTS'], 'w', encoding='utf-8').write(sys.argv[1])\n"
          "print('- [clock] src/a.py:1 f - a reset named for tomorrow is read as today')\n")


def _choose(project, chosen):
    """(the project with `language` set as every reader loads it, the language it writes); None sets nothing."""
    cfg = project if chosen is None else dict(project, language=chosen)
    with open(os.path.join(project["root"], ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    assert language.of(cfg) == (chosen or "en")
    return cfg, chosen or "en"


def _texts(lang):
    return {key: entry[lang] for key, entry in language.TEXTS.items()}


def _reviewer(tmp_path, monkeypatch, answer=APPROVED, fail=""):
    """(a reviewer route, what it has been handed so far)."""
    kept, reply = tmp_path / "prompts.jsonl", tmp_path / "answer.txt"
    reply.write_text(answer, encoding="utf-8")
    monkeypatch.setenv("AO_TEST_PROMPTS", str(kept))
    monkeypatch.setenv("AO_TEST_ANSWER", str(reply))
    monkeypatch.setenv("AO_TEST_FAIL", fail)

    def handed():
        return [json.loads(line) for line in kept.read_text(encoding="utf-8").splitlines()] if kept.exists() else []
    return {"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]}, handed


def _staged_diff(root):
    candidate = A.index_candidate(root)
    return A.candidate_diff(root, candidate, A.candidate_scope(candidate, None))


def _newest_review(root):
    directory = Path(root, "semantic-review")
    return sorted(directory.glob("*.md"))[-1].read_text(encoding="utf-8")


def _handed_to_turns(monkeypatch):
    """Every prompt a watchdog cycle builds for a turn, as it is handed over."""
    handed, plan = [], A.prompt_plan
    monkeypatch.setattr(A, "prompt_plan", lambda template, text, *args, **kwargs:
                        handed.append(text) or plan(template, text, *args, **kwargs))
    return handed


def _architect_turns(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).endswith("/claude")]


# ---- the catalogue -----------------------------------------------------------------------------

def _named(text, lang):
    """What a prompt names for its reader to act on: commands, paths, protocol words, placeholders and markers."""
    written = {language.MARKERS[key][form] for key in HEADINGS for form in language.LANGUAGES}
    named = {re.sub(r"<[^<>]*>", "<>", span) for span in re.findall(r"`([^`]*)`", text)
             if span not in written and not TURKISH_LETTER.search(span)}
    named |= set(re.findall(r"\.ao/[\w.<>/-]*\w", text)) | set(PROTOCOL.findall(text))
    named |= {name for _, name, _, _ in string.Formatter().parse(text) if name}
    named |= {key for key in HEADINGS if language.MARKERS[key][lang] in text}
    # The review prompt names the candidate's marker whole and the context's by how it begins.
    named |= {key for key in REVIEW_MARKERS if language.TEXTS[key][lang].split(" (")[0] in text}
    return named


def _shapes(text):
    """The answer lines a prompt asks for - a finding, a note, a lead - up to their dash, with every word blanked."""
    return [re.sub(r"[^\W\d_]+", "w", line.split(" — ")[0]) for line in text.splitlines()
            if line.startswith("- [") or re.match(r"- [^\s:]+:[^\s:]+ — ", line)]


def test_the_turkish_prompts_are_the_ones_every_project_was_given_and_the_english_say_the_same():
    prompts = {key: entry for key, entry in language.TEXTS.items() if key.startswith("prompt.")}

    assert {key: hashlib.sha256(entry["tr"].encode("utf-8")).hexdigest() for key, entry in prompts.items()} == TURKISH
    for key, entry in prompts.items():
        assert entry["en"] != entry["tr"] and not TURKISH_LETTER.search(entry["en"]), key
        assert _named(entry["en"], "en") == _named(entry["tr"], "tr"), key
    # Each language's prompt asks for its own headings and names its own markers.
    assert re.findall(r"^## .+$", prompts["prompt.review"]["en"], re.M) == ["## Findings", "## Notes"]
    assert re.findall(r"^## .+$", prompts["prompt.review"]["tr"], re.M) == ["## Bulgular", "## Notlar"]
    assert "'## DECISION REQUIRED'" in prompts["prompt.nudge"]["en"] and "`## URGENT`" in prompts["prompt.wake"]["en"]
    assert "'## KARAR GEREKLİ'" in prompts["prompt.nudge"]["tr"] and "`## ACİL`" in prompts["prompt.wake"]["tr"]
    # The answer lines each asks for have one shape in both, the one its reader parses.
    assert _shapes(prompts["prompt.review"]["en"]) == _shapes(prompts["prompt.review"]["tr"]) == ["- [w] w:w", "- w:w"]
    assert _shapes(prompts["prompt.hunt"]["en"]) == _shapes(prompts["prompt.hunt"]["tr"]) == ["- [w] w:w w"]


def test_no_code_reads_a_review_marker_back():
    """A marker is written into a prompt for a reviewer to read. A reader in ao would have to read every language's
    form, so none may hold one: the texts live in the catalogue, and only the prompt's builder names their keys."""
    forms = [form for key in REVIEW_MARKERS for form in language.TEXTS[key].values()]
    for path in sorted((ROOT / "src" / "ao").rglob("*.py")):
        if path.name == "language.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert [form for form in forms if form in source] == [], path
        if path.name != "cli_review.py":
            assert not re.search(r"prompt\.review-(candidate|context|section)", source), path


# ---- the reviewer ------------------------------------------------------------------------------

@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_review_prompt_and_its_markers_are_the_projects_language(project, tmp_path, monkeypatch, chosen):
    cfg, lang = _choose(project, chosen)
    root = _package(cfg)
    _stage_test(root)                   # a test-only candidate, judged beside the source it runs
    route, handed = _reviewer(tmp_path, monkeypatch)

    assert cli.cmd_review(dict(cfg, reviewer=route), _args()) == 0

    (prompt,) = handed()
    texts = _texts(lang)
    head = (texts["prompt.review"].format(boundary="b") + f"\n\n{texts['prompt.review-candidate']}\n"
            + _staged_diff(root).decode("utf-8") + f"\n\n{texts['prompt.review-context']}\n")
    assert prompt.startswith(head) and "def helper():" in prompt[len(head):]
    other = _texts("tr" if lang == "en" else "en")
    assert not [key for key in REVIEW_MARKERS + ("prompt.review",) if other[key].split("\n")[0] in prompt]
    assert lang == "tr" or not TURKISH_LETTER.search(prompt)


def test_a_turkish_projects_review_cut_off_before_the_change_resumes_from_its_section_journal(
        project, tmp_path, monkeypatch):
    cfg, _ = _choose(project, "tr")
    root = cfg["root"]
    _repo_with_change(root)
    route, handed = _reviewer(tmp_path, monkeypatch)
    cfg = dict(cfg, reviewer=route)
    names = ["scenario:1", "scenario:2", "scenario:3"]
    # The journal a review cut off after two sections left, where ao has always kept it: keyed by the diff, the
    # boundary, the sections and the reviewers, and by no word of the prompt.
    keyed = ["sha256:" + hashlib.sha256(_staged_diff(root)).hexdigest(), SCENARIOS, names, ["r1"]]
    journal = Path(root, ".ao", "reviews", "sections",
                   hashlib.sha256(json.dumps(keyed, sort_keys=True).encode("utf-8")).hexdigest()[:24] + ".jsonl")
    for name in names[:2]:
        storage.append_jsonl(str(journal), {"at": 1_789_000_000, "section": name, "position": 0, "route": "r1",
                                            "counts": {"BLOCKER": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                                            "out": APPROVED, "verdict": "APPROVED"})

    assert cli.cmd_review(cfg, _args(boundary=SCENARIOS)) == 0

    # Only the unanswered section is asked, in the prompt a Turkish project was always handed.
    (prompt,) = handed()
    texts = _texts("tr")
    assert prompt.startswith(texts["prompt.review"].format(boundary=SCENARIOS)
                             + f"\n\n{texts['prompt.review-candidate']}\n" + _staged_diff(root).decode("utf-8"))
    assert re.search(rf"\n\n{re.escape(texts['prompt.review-section'])}\nScenario 3: a restart replays the journal ",
                     prompt)
    assert [row["section"] for row in storage.read_jsonl(str(journal))] == names
    evidence = A.review_evidence(_newest_review(root))
    assert evidence["verdict"] == "APPROVED" and [section["section"] for section in evidence["sections"]] == names


def _answer(headings, breaks):
    findings, notes = headings
    return "\n".join(["VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 1", "LOW: 0", "", findings,
                      "- [BLOCKER] src/a.py:1 - the value written is never read back",
                      f"  {breaks}: set x to 2, read x, get 1",
                      "- [MEDIUM] src/a.py:1 - the name says nothing", "", notes,
                      "- src/b.py:3 - a concern outside the candidate", ""])


@pytest.mark.parametrize("chosen", LANGUAGES)
@pytest.mark.parametrize("answered", ["en", "tr"])
def test_a_review_answered_with_either_languages_headings_is_one_verdict_with_the_same_findings(
        project, tmp_path, monkeypatch, chosen, answered):
    cfg, _ = _choose(project, chosen)
    root = cfg["root"]
    _repo_with_change(root)
    asked = language.TEXTS["prompt.review"][answered]
    headings = re.findall(r"^## .+$", asked, re.M)
    breaks = re.search(r"^  (.+?): <", asked, re.M).group(1)
    route, _ = _reviewer(tmp_path, monkeypatch, answer=_answer(headings, breaks))

    assert cli.cmd_review(dict(cfg, reviewer=route), _args()) == 1

    body = _newest_review(root)
    evidence = A.review_evidence(body)
    assert evidence["verdict"] == "NEEDS_CHANGES"
    assert evidence["counts"] == {"BLOCKER": 1, "HIGH": 0, "MEDIUM": 1, "LOW": 0}
    assert [verdict for _, verdict in A.reviews(root, "semantic-review")] == ["NEEDS_CHANGES"]
    recalled = [(entry["text"], entry["outcome"]) for entry in A.recall_entries("proj", root)
                if entry["kind"] == "finding"]
    assert recalled == [("src/a.py:1 - the value written is never read back", "BLOCKER in a NEEDS_CHANGES review"),
                        ("src/a.py:1 - the name says nothing", "MEDIUM in a NEEDS_CHANGES review")]
    looped = [(loop["file"], loop["clause"], loop["sev"]) for loop in A.review_loop(root, "semantic-review", 1)]
    assert looped == [("src/a.py", "the value written is never read back", "BLOCKER"),
                      ("src/a.py", "the name says nothing", "MEDIUM")]
    assert "    - src/b.py:3 - a concern outside the candidate" in body


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_stand_in_request_asks_for_the_nonce_in_the_projects_language_and_is_collected_the_same(
        project, tmp_path, chosen):
    cfg, lang = _choose(project, chosen)
    root = cfg["root"]
    _repo_with_change(root)
    cfg = dict(cfg, reviewer={"id": "r1", "family": "x", "argv": _fake("down", exit_code=17)})

    assert cli.cmd_review(cfg, _args()) == 3

    (request,) = sorted(Path(A.review_requests_dir(root)).glob("*.md"))
    nonce, text = request.stem, request.read_text(encoding="utf-8")
    texts = _texts(lang)
    assert (f"\n\n---\n\n{texts['prompt.review-request'].format(nonce=nonce)}\n\n"
            + texts["prompt.review"].format(boundary="b") + f"\n\n{texts['prompt.review-candidate']}\n") in text
    assert lang == "tr" or not TURKISH_LETTER.search(text)

    answer = tmp_path / "carried.txt"
    answer.write_text(f"NONCE: {nonce}\n{APPROVED}", encoding="utf-8")
    assert cli.cmd_collect_review(cfg, SimpleNamespace(nonce=nonce, response=str(answer), model="model-1",
                                                       by="a person")) == 0
    assert A.review_request(root, nonce)["collected"]["by"] == "a person"


# ---- the hunter --------------------------------------------------------------------------------

@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_hunter_is_asked_in_the_projects_language_and_its_leads_are_read_the_same(
        project, tmp_path, monkeypatch, chosen):
    cfg, lang = _choose(project, chosen)
    root = cfg["root"]
    _hunted_repo(root)
    kept = tmp_path / "hunt-prompt.txt"
    monkeypatch.setenv("AO_TEST_PROMPTS", str(kept))
    cfg = dict(cfg, hunter={"id": "h1", "argv": [sys.executable, "-c", HUNTER, "{prompt}"]})

    assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 0

    prompt = kept.read_text(encoding="utf-8")
    assert prompt.startswith(_texts(lang)["prompt.hunt"].format(categories=", ".join(A.HUNT_CATEGORIES)) + "\n--- ")
    assert lang == "tr" or not TURKISH_LETTER.search(prompt)
    (mail,) = [name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name]
    assert "src/a.py:1 f — a reset named for tomorrow is read as today" in Path(root, "agent-mail", mail).read_text(
        encoding="utf-8")


# ---- the watchdog ------------------------------------------------------------------------------

@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_dry_cycle_builds_the_nudge_and_what_it_adds_in_the_projects_language(project, monkeypatch, tmp_path, chosen):
    cfg, lang = _choose(project, chosen)
    world = World(cfg, monkeypatch, tmp_path)
    handed = _handed_to_turns(monkeypatch)
    monkeypatch.setattr(A, "foreign_edits", lambda root, cfg, minutes=15: ["src/app.py"])
    world.board("blocked", "- [S1] the ledger slice · needs: the store decision")
    world.board("queued", "- [S2] the next slice")
    world.transcript_age(900)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"], slice_id="S1")["id"]

    world.cycle()

    assert "DRY RUN" in world.verdict or "nudging" in world.verdict
    texts = _texts(lang)
    assert handed == [texts["prompt.nudge"] + texts["prompt.nudge-parked"].format(waits=decision, ready="S2")
                      + texts["prompt.nudge-editing"].format(paths="src/app.py")]
    assert lang == "tr" or not TURKISH_LETTER.search(handed[0])


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_nudge_names_a_secondary_projects_ready_item_in_the_projects_language(project, monkeypatch, tmp_path, chosen):
    cfg, lang = _choose(project, chosen)
    world = World(cfg, monkeypatch, tmp_path)
    handed = _handed_to_turns(monkeypatch)
    monkeypatch.setattr(A, "foreign_edits", lambda root, cfg, minutes=15: [])
    world.board("blocked", "- [B1] needs the owner · waiting: architect")
    world.transcript_age(900)
    other = _secondary(world, tmp_path, "- [S9] secondary work", written_ago=3600)

    world.cycle()

    assert handed == [_texts(lang)["prompt.nudge"]
                      + _texts(lang)["prompt.nudge-secondary"].format(name="ao", root=str(other), item="S9")]


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_architect_is_woken_in_the_projects_language(project, monkeypatch, tmp_path, chosen):
    cfg, lang = _choose(project, chosen)
    world = World(cfg, monkeypatch, tmp_path)
    world.transcript_age(900)
    world.mail("20260917-1200-kiro-to-fable-BLOCKED-queue.md", f"# queue empty\n\n{language.marker(cfg, 'decision')}\n")

    world.cycle(dry_run=False)

    (wake,) = _architect_turns(world)
    assert _texts(lang)["prompt.wake"] in wake


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_architect_refills_an_empty_queue_in_the_projects_language(project, monkeypatch, tmp_path, chosen):
    cfg, lang = _choose(project, chosen)
    world = World(cfg, monkeypatch, tmp_path)
    world.transcript_age(900)

    world.cycle(dry_run=False)

    (refill,) = _architect_turns(world)
    assert _texts(lang)["prompt.refill"] in refill
