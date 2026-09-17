import os
import shlex

import pytest

from ao import lib as A

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake xcrun is a POSIX shell script, and the xcrun "
                                                       "stub in front of git exists only on macOS")

ELF = b"\x7fELF" + b"\0" * 60           # read for its magic only: none of these is ever run
MACH_O = b"\xcf\xfa\xed\xfe" + b"\0" * 60


def _compiled(path, magic):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic)
    path.chmod(0o755)
    return path


def _stub_beside_xcrun(tmp_path, monkeypatch, answer=None, exit_code=0):
    """macOS's layout, faked: a compiled git beside an xcrun that names the developer directory's git."""
    real = _compiled(tmp_path / "developer" / "usr" / "bin" / "git", ELF)
    stub = _compiled(tmp_path / "usr-bin" / "git", MACH_O)
    calls = tmp_path / "xcrun-calls.txt"
    xcrun = stub.parent / "xcrun"
    xcrun.write_text(f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(calls))}\n"
                     f"echo {shlex.quote(str(answer or real))}\nexit {exit_code}\n", encoding="utf-8")
    xcrun.chmod(0o755)
    monkeypatch.delenv("AO_GIT", raising=False)
    monkeypatch.setenv("PATH", str(stub.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(A, "_GIT_BINARIES", {})
    return stub, real, calls


def test_the_stub_is_passed_over_for_the_git_xcrun_names_asked_once(tmp_path, monkeypatch):
    stub, real, calls = _stub_beside_xcrun(tmp_path, monkeypatch)

    assert A.git_binary() == str(real)
    assert A.git_binary() == str(real)
    assert A.measured_by()["git"] == str(real)
    assert calls.read_text(encoding="utf-8").splitlines() == ["--find git"]


def test_ao_git_still_wins_and_xcrun_is_not_asked(tmp_path, monkeypatch):
    stub, real, calls = _stub_beside_xcrun(tmp_path, monkeypatch)
    monkeypatch.setenv("AO_GIT", str(stub))

    assert A.git_binary() == str(stub) and A.measured_by()["git"] == str(stub)
    assert not calls.exists()


@pytest.mark.parametrize("case", ["a script", "nothing there", "xcrun failed", "the stub itself", "a bare name"])
def test_an_answer_that_is_not_a_compiled_git_keeps_the_stub(tmp_path, monkeypatch, case):
    script = tmp_path / "filter" / "git"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho '1 file changed (compressed)'\n", encoding="utf-8")
    script.chmod(0o755)
    answers = {"a script": (str(script), 0), "nothing there": (str(tmp_path / "missing" / "git"), 0),
               "xcrun failed": (None, 1), "the stub itself": (str(tmp_path / "usr-bin" / "git"), 0),
               "a bare name": ("git", 0)}
    answer, exit_code = answers[case]
    stub, _, calls = _stub_beside_xcrun(tmp_path, monkeypatch, answer=answer, exit_code=exit_code)

    assert A.git_binary() == str(stub) and A.measured_by()["git"] == str(stub)
    assert calls.read_text(encoding="utf-8").splitlines() == ["--find git"]


def test_a_script_beside_xcrun_is_no_stub_and_xcrun_is_not_asked(tmp_path, monkeypatch):
    stub, _, calls = _stub_beside_xcrun(tmp_path, monkeypatch)
    stub.write_text("#!/bin/sh\nexec git \"$@\"\n", encoding="utf-8")

    assert A._xcrun_git(str(stub)) is None
    assert not calls.exists()


def test_a_compiled_git_without_xcrun_beside_it_is_taken_as_it_is(tmp_path, monkeypatch):
    git = _compiled(tmp_path / "bin" / "git", ELF)
    monkeypatch.delenv("AO_GIT", raising=False)
    monkeypatch.setenv("PATH", str(git.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(A, "_GIT_BINARIES", {})

    assert A.git_binary() == str(git) and A.measured_by()["git"] == str(git)
