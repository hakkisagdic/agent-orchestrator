import json
import os
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

from ao import lib as A  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal ao project: git repo, .ao/config.json, board, empty mailbox."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "init"], cwd=root, check=True)
    (root / ".ao").mkdir()
    (root / ".ao" / "ledger").mkdir()
    cfg = {"project": "proj", "mailbox": "agent-mail", "reviews": "semantic-review",
           "implementer": {"adapter": "kiro", "session": "s1", "name": "kiro"},
           "architect": {"name": "fable", "argv": ["claude", "-p", "{prompt}"]}}
    (root / ".ao" / "config.json").write_text(json.dumps(cfg))
    (root / ".ao" / "board.md").write_text(
        "# Board\n\n## running\n\n## blocked\n\n## queued\n\n## inbox\n\n## verified\n\n## done\n")
    (root / "agent-mail").mkdir()
    (root / "semantic-review").mkdir()
    # keep every test's ~/.ao away from the real one
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(A, "HOME", str(home))
    full = dict(cfg, root=str(root))
    return full
