"""What ao hands PowerShell, run by a PowerShell (WINDOWS-CLOSE, #9, #71).

ao hands PowerShell three scripts: the process table's query (procs.py), the toast (watchdog.py)
and bin/ao.ps1, the read-only subset for a Windows machine without Python. Each was written from
the language reference and none had been run by a PowerShell. PowerShell's own parser reads each of
them wherever a PowerShell is installed: Windows ships one, and `pwsh` runs on Linux and macOS too.
bin/ao.ps1 reads a board on Windows, whose home and paths it is written for. Where a PowerShell is
missing each test is skipped, saying so. That bin/ao.ps1 is ASCII holds on every platform.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from ao import procs, watchdog as W

PS1 = pathlib.Path(__file__).resolve().parent.parent / "bin" / "ao.ps1"
# Windows PowerShell 5.1 is the one Windows ships and the one procs.py asks; PowerShell 7 is `pwsh`.
SHELLS = ("powershell", "pwsh")
SCRIPTS = {
    "the process table's query": lambda: procs._Windows.SNAPSHOT,
    "the toast": lambda: W.TOAST_SCRIPT,
    "bin/ao.ps1": lambda: PS1.read_text(encoding="ascii"),
}
PARSE = ("$errors = $null; "
         "$null = [System.Management.Automation.Language.Parser]::ParseInput($env:AO_TEST_SCRIPT, [ref]$null, "
         "[ref]$errors); "
         "foreach ($e in $errors) { 'line ' + $e.Extent.StartLineNumber + ': ' + $e.Message }")


def _powershell(name):
    found = shutil.which(name)
    if not found:
        pytest.skip(f"{name} is not installed here; Windows ships PowerShell and the Windows lane runs this")
    return found


def _run(shell, command, **env):
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                          env=dict(os.environ, **env), capture_output=True, timeout=120)


def test_the_windows_subset_is_ascii_because_windows_powershell_reads_it_in_the_code_page():
    # A script without a byte-order mark is read in the ANSI code page: a character outside ASCII
    # would be another character there. bin/ao.ps1 spells each one as [char]0x....
    assert PS1.read_bytes().isascii()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_powershell_parses_every_script_ao_hands_it(shell, script):
    done = _run(_powershell(shell), PARSE, AO_TEST_SCRIPT=SCRIPTS[script]())

    said = done.stdout.decode("utf-8", "replace").strip()
    assert done.returncode == 0 and said == "", said or done.stderr.decode("utf-8", "replace")


# Dot-sourced, bin/ao.ps1 shows the board and leaves its functions defined; the titles its board parse
# read are written as UTF-8 to a file, so no console code page stands between them and this test.
READ_BOARD = (". $env:AO_TEST_PS1 board -Root $env:AO_TEST_ROOT 6>$null >$null; "
              "$titles = @((Get-Board (Find-Root $env:AO_TEST_ROOT))['queued'] | ForEach-Object { $_.Title }); "
              "[System.IO.File]::WriteAllText($env:AO_TEST_OUT, (ConvertTo-Json -InputObject $titles -Compress), "
              "(New-Object System.Text.UTF8Encoding $false))")


@pytest.mark.skipif(sys.platform != "win32", reason="bin/ao.ps1 is written for Windows paths and a Windows home; "
                                                    "the Windows lane runs it")
@pytest.mark.parametrize("shell", SHELLS)
def test_the_windows_subset_reads_a_board_in_utf8_under_a_path_holding_wildcards(shell, tmp_path):
    root = tmp_path / "proje [bir]"
    (root / ".ao").mkdir(parents=True)
    (root / ".ao" / "board.md").write_text(
        "# Board\n\n## queued\n\n- [S1] İşçi → ılık çay · needs: S0\n- [S2] a plain title\n", encoding="utf-8")
    out = tmp_path / "titles.json"

    done = _run(_powershell(shell), READ_BOARD, AO_TEST_PS1=str(PS1), AO_TEST_ROOT=str(root), AO_TEST_OUT=str(out))

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert json.loads(out.read_text(encoding="utf-8-sig")) == ["İşçi → ılık çay", "a plain title"]
