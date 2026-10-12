"""A reviewer that is a Python program answers in UTF-8, whatever its streams would default to (REVIEWER-UTF8).

ao hands a reviewer its prompt and reads its answer as UTF-8. A Python program reads and writes its standard
streams as PYTHONIOENCODING says, which wins over UTF-8 mode, and on Windows, without either, in the ANSI code
page. The Windows leg without UTF-8 mode failed ten tests on it: a stand-in's em dash came back as a byte no
UTF-8 reads, so a hunter's leads and a reviewer's findings lost the separator they are read by, and a Turkish
letter ended the reviewer with an encoding error before it answered. The reviewer's environment now asks for
UTF-8 streams. Here PYTHONIOENCODING=cp1252 makes any platform's Python write as that Windows did.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A

SAID = "- [BLOCKER] src/a.py:3 parse — şu satır ığdır"
ECHO = ("import sys\n"
        "prompt = sys.stdin.read() if len(sys.argv) < 2 else sys.argv[1]\n"
        "print(" + repr(SAID) + ")\n"
        "print('prompt:', prompt)\n")


def _windows_streams(monkeypatch, value="cp1252"):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", value)


def test_a_python_reviewer_writes_its_answer_in_utf8_where_its_streams_default_to_a_code_page(project, monkeypatch):
    _windows_streams(monkeypatch)

    attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", ECHO, "{prompt}"], timeout=30)

    assert attempt["ok"] is True, attempt
    assert attempt["out"].splitlines()[0] == SAID


def test_a_prompt_handed_on_standard_input_is_read_as_it_was_written(project, monkeypatch):
    _windows_streams(monkeypatch)
    prompt = "Gözden geçir — ğüşıöç"
    plan = {"argv": [sys.executable, "-c", ECHO], "channel": "stdin", "run": None, "prompt": prompt, "batch": None}

    attempt = cli._run_reviewer(project["root"], plan["argv"], timeout=30, channel=plan)

    assert attempt["ok"] is True, attempt
    assert attempt["out"].splitlines()[-1] == f"prompt: {prompt}"


def test_an_encoding_the_person_named_as_utf8_keeps_its_error_handler_and_any_other_is_utf8(monkeypatch, tmp_path):
    for given, handed in (("utf8:surrogateescape", "utf8:surrogateescape"), ("UTF-8", "UTF-8"),
                          ("cp1252:replace", "utf-8:replace"), ("cp1252", "utf-8"), ("no-such-codec", "utf-8"),
                          (":strict", "utf-8:strict")):
        monkeypatch.setenv("PYTHONIOENCODING", given)
        assert cli._reviewer_environment(str(tmp_path))["PYTHONIOENCODING"] == handed, given
    monkeypatch.delenv("PYTHONIOENCODING")
    assert cli._reviewer_environment(str(tmp_path))["PYTHONIOENCODING"] == "utf-8"


def test_a_hunt_by_a_python_hunter_reads_its_leads_where_its_streams_default_to_a_code_page(project, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 1\n")
    subprocess.run(["git", "add", "src"], cwd=root, check=True)
    shipped = A.package_adapters()
    stand_in = {"id": "stand-in", "detect": {"binaries": [os.path.basename(sys.executable)]},
                "options": {"trust_none": ["-B"]}}
    monkeypatch.setattr(A, "package_adapters", lambda: dict(shipped, **{"stand-in": stand_in}))
    _windows_streams(monkeypatch)
    lead = "print('- [clock] src/a.py:1 f — a reset named for tomorrow is read as bugün')\n"
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-B", "-c", lead, "{prompt}"]})

    assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 0

    (mail,) = [name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name]
    with open(os.path.join(root, "agent-mail", mail), encoding="utf-8") as fh:
        assert "src/a.py:1 f — a reset named for tomorrow is read as bugün" in fh.read()
