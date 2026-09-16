import json
import os
import pathlib
import re
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W


def _stored(root):
    return json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))


def _write(root, cfg):
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)


def _board_running(root, line):
    path = os.path.join(root, ".ao", "board.md")
    text = open(path, encoding="utf-8").read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## running\n", f"## running\n{line}\n", 1))


def test_the_role_table_reassigns_on_the_next_slice_and_refuses_self_review(project, capsys):
    root = project["root"]
    cfg = dict(project, reviewer={"id": "r1", "family": "other", "argv": ["reviewer", "{prompt}"]})
    _write(root, cfg)

    assert cli.cmd_role(A.load_config(root), SimpleNamespace(action="set", role="reviewer", actor="kiro")) == 2
    assert "no actor reviews its own work" in capsys.readouterr().out

    stored = _stored(root)
    stored.setdefault("actors", {})
    _write(root, dict(stored, actors={"kiro": dict(project["implementer"]), "fable": dict(project["architect"]),
                                      "r1": dict(cfg["reviewer"]), "dev": {"adapter": "claude-code", "session": "auto",
                                                                           "name": "dev"}},
                      roles={"implementer": "kiro", "architect": "fable", "reviewer": "r1"}))
    _board_running(root, "- [S1] a slice in flight · since: 2026-09-16 09:00")

    assert cli.cmd_role(A.load_config(root), SimpleNamespace(action="set", role="implementer", actor="dev")) == 0
    assert "takes effect once S1 leaves running" in capsys.readouterr().out
    assert A.load_config(root)["implementer"]["actor"] == "kiro"

    path = os.path.join(root, ".ao", "board.md")
    text = open(path, encoding="utf-8").read().replace("- [S1] a slice in flight · since: 2026-09-16 09:00\n", "")
    open(path, "w", encoding="utf-8").write(text.replace("## done\n", "## done\n- [S1] a slice in flight\n"))

    assert A.load_config(root)["implementer"]["actor"] == "dev"
    assert A.mail_names(A.load_config(root))[0] == "dev"


def test_messages_and_prompts_address_roles_after_both_actors_are_renamed(project):
    root = project["root"]
    cfg = dict(project, implementer=dict(project["implementer"], name="dev"),
               architect=dict(project["architect"], name="lead"))
    _write(root, cfg)
    cfg = A.load_config(root)
    A.write_mail(root, cfg, "20260916-0900-dev-to-lead-BLOCKED-store.md", "# which store?\n\n## ACİL\n",
                 {"kind": "blocked", "from": "dev", "to": "lead"})

    [urgent] = A.urgent_messages(root, cfg, "architect")
    assert urgent["id"] == "20260916-0900-dev-to-lead-BLOCKED-store.md"
    meta = A.mail_meta(os.path.join(root, "agent-mail", urgent["id"]))
    assert meta["from_role"] == "implementer" and meta["to_role"] == "architect"
    assert A.to_architect("20260916-0900-dev-to-fable-BLOCKED-x.md", cfg) is False

    name = A.note(root, cfg, None, "take the next item", "body")
    assert "-lead-to-dev-" in name
    assert "architect" in W.WAKE_PROMPT.lower() or "mimar" in W.WAKE_PROMPT
    assert not re.search(r"fable|kiro", W.WAKE_PROMPT)


def test_no_actor_name_is_used_as_an_address_outside_the_adapters():
    source = pathlib.Path(A.__file__).parent
    addressing = re.compile(r"-to-(fable|kiro)-|(fable|kiro)-to-")
    found = [f"{path.name}:{number}" for path in sorted(source.glob("*.py"))
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if addressing.search(line)]
    assert found == []
