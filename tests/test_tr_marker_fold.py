"""A Turkish report marked `## ACİL` is read as urgent by every reader of lowered markers (TR-MARKER-FOLD).

Python lowers the capital İ to an i and a combining dot, so `"## ACİL".lower()` is not `## acil`, the form those
readers search for. `lib.urgent_messages` compares in capitals and found such a report, but three readers lowered
the message with `str.lower()`: the watchdog's anomalies filed it as a report waiting rather than a decision asked
for, `waiting_on_architect` did not see the implementer waiting and the watchdog went on nudging it, and a mail's
class was read the same way. The bug hunter read language.py on 2026-10-12 and named it. Each reader now lowers the
message as the forms are lowered: `language.folded`, the capital İ as a plain i first.
"""
import os

from ao import language, lib as A


def _mail(project, name, body):
    with open(os.path.join(project["root"], project["mailbox"], name), "w", encoding="utf-8") as fh:
        fh.write(body)


def _report(project, marker):
    implementer, architect = A.mail_names(project)
    name = f"20261012-1200-{implementer}-to-{architect}-REPORT-s3.md"
    _mail(project, name, f"# S3 cannot go on\n\n{marker}\n\nwhich store?\n")
    return name


def test_every_marker_is_found_folded_however_it_is_cased_or_lowered():
    for key in ("urgent", "decision", "stop"):
        for form in language.forms(key):
            # As ao writes it, with the plain capital I, as a person types it in lower case, and as str.lower() left it.
            for written in (form, form.replace("İ", "I"), form.replace("İ", "i").lower(), form.lower()):
                assert any(low in language.folded(written) for low in language.lowered(key)), written
    assert language.folded("## ACİL") == "## acil" and language.folded("## KARAR GEREKLİ") == "## karar gerekli"


def test_a_turkish_report_marked_urgent_is_a_decision_asked_for_in_the_anomalies(project):
    name = _report(project, "## ACİL")

    found = [item for item in A.anomalies(project["root"], project, {}, 0, 360) if name in item.get("reports", [])]

    assert [item["kind"] for item in found] == ["decision-requested"]


def test_the_watchdog_sees_the_implementer_waiting_on_a_turkish_urgent_report(project):
    name = _report(project, "## ACİL")

    assert A.waiting_on_architect(project["root"], project)[0] == name


def test_a_turkish_decision_asked_for_is_classed_as_one(project):
    assert A.mail_class("20261012-1200-kiro-to-architect-REPORT-s3.md", {}, "# S3\n\n## KARAR GEREKLİ\n") == \
        "needs-decision"
