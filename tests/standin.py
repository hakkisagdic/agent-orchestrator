"""Stand-in programs a test starts, on POSIX and on Windows alike (WINDOWS-LANE-PARITY).

A stand-in harness - a reviewer, a conformance harness, a fake gh - was a Python script whose first line named
this interpreter. POSIX runs such a script; Windows runs no shebang line, and ao knows a harness by the name of
the program it starts, so `[sys.executable, script]` would be no harness of any adapter's. Every test that
started one was skipped on Windows, the reviewer and authority tests among them.

On Windows a stand-in is now what pip makes of a console script there: distlib's console launcher, then a
line naming this interpreter, then the script as `__main__.py` in a zip. The launcher starts the interpreter
on its own file, which Python runs as a zip application, so `agy.exe` is a program named agy that runs the
script, as `agy` is on POSIX. Where pip brings no launcher, a test that needs one is skipped and says why.
"""
import importlib.util
import io
import os
import struct
import sys
import sysconfig
import zipfile

import pytest


def _launcher():
    """The bytes of the console launcher pip's distlib makes Windows console scripts with, for this
    interpreter's machine; None where there is none. pip is found, never imported."""
    try:
        found = importlib.util.find_spec("pip")
    except (ImportError, ValueError):
        found = None
    folders = list(found.submodule_search_locations or []) if found else []
    bits = "64" if struct.calcsize("P") == 8 else "32"
    arm = "-arm" if sysconfig.get_platform() == "win-arm64" else ""
    for folder in folders:
        try:
            with open(os.path.join(folder, "_vendor", "distlib", f"t{bits}{arm}.exe"), "rb") as fh:
                return fh.read()
        except OSError:
            continue
    return None


def program(directory, name, source):
    """A stand-in program named `name` in `directory` that runs the Python `source` with this interpreter: its path.

    On POSIX an executable script whose first line names this interpreter; on Windows `name.exe`, as pip writes a
    console script there. A test on a Windows whose pip holds no launcher is skipped, saying so. A program that is
    there already with the same bytes is left as it is: Windows may still hold one that has just run.
    """
    directory = str(directory)
    os.makedirs(directory, exist_ok=True)
    if os.name != "nt":
        path, content = os.path.join(directory, name), f"#!{sys.executable}\n{source}".encode("utf-8")
    else:
        launcher = _launcher()
        if launcher is None:
            pytest.skip("a stand-in needs pip's console launcher to be a program Windows starts, and this Python's "
                        "pip holds none")
        archive = io.BytesIO()
        # Dated as the format's first day, so one script is one program whenever it is written.
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(zipfile.ZipInfo("__main__.py", date_time=(1980, 1, 1, 0, 0, 0)), source)
        interpreter = f'"{sys.executable}"' if " " in sys.executable else sys.executable
        path = os.path.join(directory, name + ".exe")
        content = launcher + f"#!{interpreter}\n".encode("utf-8") + archive.getvalue()
    try:
        with open(path, "rb") as fh:
            if fh.read() == content:
                return path
    except OSError:
        pass
    with open(path, "wb") as fh:
        fh.write(content)
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path
