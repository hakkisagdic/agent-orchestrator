"""The MCP file `ao init` writes into the tree is this machine's, and ignored (README-QUICKSTART-7).

Following the quickstart, the first `ao verify` failed: `ao init` wrote `.mcp.json` for Claude Code with an entry
naming this machine's ao, and the untracked file was a product file outside the index. It is ignored where init
writes it now, as the sessions and lanes it settles for this machine are; one the repository already tracks is the
repository's, and stays so.
"""
import subprocess

from ao import cli
from tests import conftest
from tests.test_profiles import _init_args


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([conftest.GIT, "init", "-q"], cwd=root, check=True)
    return root


def _status(root):
    return subprocess.run([conftest.GIT, "status", "--porcelain", "--untracked-files=all"], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def test_the_mcp_file_init_writes_is_ignored(tmp_path, capsys):
    root = _repo(tmp_path)

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", no_mcp=False)) == 0, \
        capsys.readouterr().out

    assert (root / ".mcp.json").is_file()
    assert ".mcp.json" in (root / ".gitignore").read_text(encoding="utf-8").split("\n")
    assert ".mcp.json" not in _status(root)


def test_an_mcp_file_the_repository_tracks_stays_its_own(tmp_path, capsys):
    root = _repo(tmp_path)
    (root / ".mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    subprocess.run([conftest.GIT, "add", ".mcp.json"], cwd=root, check=True)

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", no_mcp=False)) == 0, \
        capsys.readouterr().out

    assert ".mcp.json" not in (root / ".gitignore").read_text(encoding="utf-8").split("\n")


def test_without_mcp_registration_nothing_more_is_ignored(tmp_path, capsys):
    root = _repo(tmp_path)

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", no_mcp=True)) == 0, \
        capsys.readouterr().out

    assert ".mcp.json" not in (root / ".gitignore").read_text(encoding="utf-8").split("\n")
