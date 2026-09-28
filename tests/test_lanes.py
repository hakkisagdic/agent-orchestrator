"""A board item gets a git worktree of its own, prepared before it is called ready (LANE-START).

Parallel work that shares one checkout interleaves its diffs, and a worktree added by hand is
prepared by hand or not at all. `ao lane start` makes one only for a READY item, beside the main
checkout on a branch of its own, prepares it from the lane settings and reports it ready only
when every step passed; `ao lane remove` refuses a lane that holds work nobody committed.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A

SRC = str(Path(A.__file__).resolve().parent.parent)

BOARD = """# Board

## running
- [R] already being worked

## blocked

## queued
- [B] ready to start
- [D] ready too
- [C] waits for another · needs: D
- [W] a leaf waiting on a person · waiting: human
- [ACME-187/1] a sub-slice

## verified

## done
- [A] landed
"""


def _git(cwd, *args):
    return subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def _args(action, item=None):
    return SimpleNamespace(action=action, item=item)


def _board(root):
    (Path(root) / ".ao" / "board.md").write_text(BOARD, encoding="utf-8")


def _can_link(tmp_path):
    try:
        os.symlink(str(tmp_path), str(tmp_path / "link-probe"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")


def _configure_lanes(root, lane):
    """Write lane settings into the project's own config, where an `ao` started as a process reads them."""
    path = Path(root) / ".ao" / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(dict(config, lane=lane)), encoding="utf-8")


def _group_alive(pid):
    try:
        os.killpg(pid, 0)
    except OSError:
        return False
    return True


def _until(condition, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _start_in_a_process(root, tmp_path, item):
    """`ao lane start <item>` as its own process, returned once its record names the post-create command."""
    env = dict(os.environ, HOME=str(tmp_path / "home"), PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")
    ao = subprocess.Popen([sys.executable, "-m", "ao", "-C", root, "lane", "start", item], env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not _until(lambda: ((A.lane_record(root, item) or {}).get("command") or {}).get("pid")):
        ao.kill()
        pytest.fail(ao.communicate(timeout=30)[0].decode("utf-8", "replace"))
    return ao, A.lane_record(root, item)["command"]["pid"]


# The command starts a process of its own and waits: stopping the command must reach both.
LINGERING = [sys.executable, "-c", "import subprocess, sys, time; "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"]


def test_a_ready_item_gets_a_worktree_of_its_own_on_its_own_branch_beside_the_main_checkout(project, tmp_path,
                                                                                            capsys):
    root = project["root"]
    _board(root)

    assert cli.cmd_lane(project, _args("start", "B")) == 0

    lane = tmp_path / "proj-lanes" / "B"
    record = A.lane_record(root, "B")
    assert os.path.realpath(record["path"]) == os.path.realpath(lane) and (lane / ".ao-project").is_file()
    assert _git(lane, "branch", "--show-current") == "lane/B"
    assert record["base"] == _git(root, "rev-parse", "HEAD") == _git(lane, "rev-parse", "HEAD")
    assert (record["item"], record["branch"], record["state"]) == ("B", "lane/B", "ready")
    assert os.path.realpath(lane) in {os.path.realpath(tree["path"]) for tree in A.worktree_list(root)}
    assert json.loads((Path(root) / ".ao" / "lanes" / "B.json").read_text(encoding="utf-8"))["state"] == "ready"
    assert "lane B ready" in capsys.readouterr().out

    assert cli.cmd_lane(project, _args("list")) == 0
    listed = capsys.readouterr().out
    assert "B  ready  lane/B" in listed and "board: queued" in listed

    assert cli.cmd_lane(project, _args("start", "B")) == 2
    assert "B already has a lane, ready" in capsys.readouterr().out

    # The record names directories on this machine: git never offers it for a commit, whatever .gitignore says.
    assert ".ao/lanes" not in _git(root, "status", "--porcelain", "--untracked-files=all")
    # A lane looks merged the moment it starts; the worktree command keeps it all the same.
    fact = next(fact for fact in A.worktree_facts(root, project)
                if os.path.realpath(fact["path"]) == os.path.realpath(lane))
    assert not fact["may_go"] and any("`ao lane remove B`" in why for why in fact["keep"])
    assert cli.cmd_worktrees(project, SimpleNamespace(action="prune", yes=True)) == 0
    assert lane.is_dir() and A.lane_rows(root)[0]["exists"]


def test_an_id_no_directory_or_branch_can_hold_names_its_lane_with_hyphens(project, tmp_path):
    root = project["root"]
    _board(root)

    assert cli.cmd_lane(project, _args("start", "ACME-187/1")) == 0

    assert _git(tmp_path / "proj-lanes" / "ACME-187-1", "branch", "--show-current") == "lane/ACME-187-1"
    assert A.lane_record(root, "ACME-187/1")["item"] == "ACME-187/1"


def test_an_item_off_the_board_or_not_ready_is_refused_and_nothing_is_made(project, tmp_path, capsys):
    root = project["root"]
    _board(root)

    for item, why in (("Z", "Z is not on the board"), ("C", "C needs D, which has not landed"),
                      ("W", "W is waiting on human"), ("R", "R is running on the board"),
                      ("A", "A is done on the board")):
        assert cli.cmd_lane(project, _args("start", item)) == 2
        out = capsys.readouterr().out
        assert why in out and "`ao board ready` lists them" in out

    assert cli.cmd_lane(project, _args("start")) == 2
    assert not (tmp_path / "proj-lanes").exists() and A.lane_records(root) == []
    assert _git(root, "branch", "--list", "lane/*") == ""


def test_preparation_links_writes_and_runs_before_the_lane_is_called_ready(project, tmp_path, capsys):
    _can_link(tmp_path)
    root = project["root"]
    _board(root)
    (Path(root) / ".venv").mkdir()
    (Path(root) / ".venv" / "marker").write_text("from the main checkout", encoding="utf-8")
    script = ("import os, sys; print('cwd', os.getcwd()); print('lane', os.environ['AO_TEST_LANE']); "
              "print('item', sys.argv[1]); print('linked', open(os.path.join('.venv', 'marker')).read())")
    cfg = dict(project, lane={"link_paths": [".venv"], "env": ["AO_TEST_LANE={lane}", "WHERE={path}"],
                              "post_create": [sys.executable, "-c", script, "{item}"]})

    assert cli.cmd_lane(cfg, _args("start", "ACME-187/1")) == 0

    lane, record = tmp_path / "proj-lanes" / "ACME-187-1", A.lane_record(root, "ACME-187/1")
    assert os.path.islink(lane / ".venv")
    assert os.path.realpath(lane / ".venv") == os.path.realpath(Path(root) / ".venv")
    assert (lane / ".env").read_text(encoding="utf-8") == f"AO_TEST_LANE=ACME-187-1\nWHERE={record['path']}\n"
    log = (Path(root) / ".ao" / "lanes" / "ACME-187-1.log").read_text(encoding="utf-8")
    assert f"cwd {os.path.realpath(lane)}" in log and "lane ACME-187-1" in log and "item ACME-187/1" in log
    assert "linked from the main checkout" in log
    assert (record["state"], record["prepared"]) == ("ready", [".venv", ".env"])
    assert "linked .venv · wrote 2 variable(s) to .env · ran " in capsys.readouterr().out


def test_a_failed_preparation_is_reported_and_its_worktree_left_for_inspection(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    failing = dict(project, lane={"post_create": [sys.executable, "-c",
                                                  "import sys; print('no database here'); sys.exit(3)"]})

    assert cli.cmd_lane(failing, _args("start", "B")) == 1

    out, record = capsys.readouterr().out, A.lane_record(root, "B")
    assert "lane B not ready" in out and "exited 3" in out and "no database here" in out
    assert f"the worktree stays at {record['path']} for inspection" in out
    assert (tmp_path / "proj-lanes" / "B").is_dir()
    assert record["state"] == "failed" and "exited 3" in record["problem"]
    assert cli.cmd_lane(failing, _args("start", "B")) == 2      # it stands until someone removes it

    missing = dict(project, lane={"link_paths": ["node_modules"],
                                  "post_create": [sys.executable, "-c", "open('ran', 'w').close()"]})
    assert cli.cmd_lane(missing, _args("start", "D")) == 1
    assert "lane.link_paths names node_modules, which the main checkout" in capsys.readouterr().out
    assert not (tmp_path / "proj-lanes" / "D" / "ran").exists()   # nothing ran after the step that failed

    hung = dict(project, lane={"post_create": [sys.executable, "-c", "import time; time.sleep(30)"],
                               "post_create_timeout": 1})
    assert cli.cmd_lane(hung, _args("start", "ACME-187/1")) == 1
    assert "ran past 1 seconds and was stopped" in capsys.readouterr().out
    assert [row["state"] for row in A.lane_rows(root)] == ["failed", "failed", "failed"]


@pytest.mark.skipif(os.name == "nt", reason="the failing hook here is a POSIX shell script")
def test_a_worktree_git_made_before_a_hook_failed_is_a_failed_lane_that_can_be_removed(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    hook = Path(root) / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'husky: install first' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(root, "config", "core.hooksPath", str(hook.parent))       # whatever the machine's git config says

    assert cli.cmd_lane(project, _args("start", "B")) == 1

    out, record = capsys.readouterr().out, A.lane_record(root, "B")
    assert "lane B not ready: git worktree add failed after making the worktree: " in out
    assert "husky: install first" in record["problem"] and "\n" not in record["problem"]
    assert record["state"] == "failed" and (tmp_path / "proj-lanes" / "B").is_dir()
    hook.unlink()
    assert cli.cmd_lane(project, _args("remove", "B")) == 0
    assert not (tmp_path / "proj-lanes").exists() and A.lane_records(root) == []


def test_remove_refuses_a_lane_with_uncommitted_changes_and_says_why(project, tmp_path, capsys):
    _can_link(tmp_path)
    root = project["root"]
    _board(root)
    (Path(root) / ".venv").mkdir()
    cfg = dict(project, lane={"link_paths": [".venv"], "env": ["PORT=3001"]})
    assert cli.cmd_lane(cfg, _args("start", "B")) == 0
    lane = tmp_path / "proj-lanes" / "B"
    (lane / "notes.txt").write_text("written in the lane, never committed", encoding="utf-8")
    (lane / ".ao-project").write_text("changed in the lane", encoding="utf-8")
    capsys.readouterr()

    assert cli.cmd_lane(cfg, _args("remove", "B")) == 2

    out = capsys.readouterr().out
    assert "lane B holds 2 uncommitted change(s)" in out and "M .ao-project" in out and "?? notes.txt" in out
    assert ".venv" not in out and "PORT" not in out            # what ao put there is not someone's work
    assert lane.is_dir() and A.lane_record(root, "B") is not None

    (lane / "notes.txt").unlink()
    _git(lane, "checkout", "--", ".ao-project")
    _git(lane, "commit", "-q", "--allow-empty", "-m", "work on B")
    tip = _git(lane, "rev-parse", "HEAD")
    assert cli.cmd_lane(cfg, _args("remove", "B")) == 0

    assert not lane.exists() and (Path(root) / ".venv").is_dir()   # the link went, never what it pointed at
    assert not (tmp_path / "proj-lanes").exists()                  # and with its last lane, the lanes directory
    assert _git(root, "branch", "--list", "lane/*") == "" and A.lane_records(root) == []
    assert tip in _git(root, "for-each-ref", "--format=%(objectname)", "refs/ao/archive/").split()
    assert "lane B removed" in capsys.readouterr().out


def test_remove_takes_no_record_at_its_word(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    assert cli.cmd_lane(project, _args("start", "B")) == 0
    path, lane = Path(root) / ".ao" / "lanes" / "B.json", tmp_path / "proj-lanes" / "B"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(dict(record, path=root)), encoding="utf-8")
    capsys.readouterr()

    assert cli.cmd_lane(project, _args("remove", "B")) == 2

    assert f"names {root}, and ao makes it at" in capsys.readouterr().out
    assert os.path.isdir(os.path.join(root, ".ao")) and lane.is_dir()
    assert _git(root, "branch", "--list", "lane/B") != ""

    _git(root, "worktree", "remove", "--force", str(lane))
    lane.mkdir()
    path.write_text(json.dumps(record), encoding="utf-8")
    assert cli.cmd_lane(project, _args("remove", "B")) == 2
    assert "is not a worktree of this repository" in capsys.readouterr().out and lane.is_dir()

    assert cli.cmd_lane(project, _args("remove", "Q")) == 2
    assert "Q has no lane here" in capsys.readouterr().out


def test_a_lane_setting_ao_cannot_use_refuses_the_start_and_nothing_is_made(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    # A shell string where a list belongs, and two bare strings: the obvious hand-written mistakes.
    handwritten = dict(project, lane={"post_create": "make setup && npm ci", "link_paths": ".venv",
                                      "env": "PORT=3001"})

    assert cli.cmd_lane(handwritten, _args("start", "B")) == 2

    out = capsys.readouterr().out
    assert "lane.post_create is set to 'make setup && npm ci' in the project settings" in out
    assert "lane.link_paths is set to '.venv'" in out and "lane.env is set to 'PORT=3001'" in out
    assert "lane B ready" not in out

    for lane, why in (({"link_paths": ["../beside"]}, "lane.link_paths names '../beside'"),
                      ({"env": ["PORT 3001"]}, "lane.env holds 'PORT 3001'"),
                      ({"env": ["PORT=3001"], "env_file": ".."}, "lane.env_file is '..'")):
        assert cli.cmd_lane(dict(project, lane=lane), _args("start", "B")) == 2
        assert why in capsys.readouterr().out
    assert not (tmp_path / "proj-lanes").exists() and A.lane_records(root) == []
    assert _git(root, "branch", "--list", "lane/*") == ""


@pytest.mark.skipif(os.name == "nt", reason="the signals a terminal sends are POSIX signals")
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_signal_that_stops_ao_while_it_prepares_a_lane_stops_the_command_and_fails_the_lane(project, tmp_path,
                                                                                            capsys, signame):
    _can_link(tmp_path)
    root = project["root"]
    _board(root)
    (Path(root) / ".venv").mkdir()
    _configure_lanes(root, {"link_paths": [".venv"], "post_create": LINGERING})
    ao, command = _start_in_a_process(root, tmp_path, "B")

    ao.send_signal(getattr(signal, signame))

    said = ao.communicate(timeout=30)[0].decode("utf-8", "replace")
    assert ao.returncode == 130 and "lane B not ready: ao was stopped" in said, said
    assert _until(lambda: not _group_alive(command), 5)        # the command and what it started
    record = A.lane_record(root, "B")
    assert record["state"] == "failed" and "stopped" in record["problem"] and record["prepared"] == [".venv"]
    assert "command" not in record and "process" not in record
    assert [row["state"] for row in A.lane_rows(root)] == ["failed"]

    assert cli.cmd_lane(project, _args("remove", "B")) == 0        # ao's own link is nobody's work
    assert not (tmp_path / "proj-lanes").exists() and (Path(root) / ".venv").is_dir()
    assert "lane B removed" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="a killed process leaves its process group behind on POSIX")
def test_a_command_a_killed_start_left_running_keeps_the_lane_preparing_until_remove_stops_it(project, tmp_path,
                                                                                             capsys):
    root = project["root"]
    _board(root)
    _configure_lanes(root, {"post_create": LINGERING})
    ao, command = _start_in_a_process(root, tmp_path, "B")

    ao.kill()                                                   # nothing ao can catch
    ao.communicate(timeout=30)

    assert _group_alive(command)
    row = A.lane_rows(root)[0]
    assert row["state"] == "preparing" and f"process {command}" in row["held"]
    assert cli.cmd_lane(project, _args("list")) == 0
    assert f"process {command}" in capsys.readouterr().out

    assert cli.cmd_lane(project, _args("remove", "B")) == 0

    out = capsys.readouterr().out
    assert f"stop the post-create command ao left running (process {command})" in out
    assert not _group_alive(command) and not (tmp_path / "proj-lanes").exists()
    assert A.lane_records(root) == [] and _git(root, "branch", "--list", "lane/*") == ""


def test_a_lane_whose_directory_was_deleted_by_hand_is_removed_with_its_branch(project, tmp_path, capsys):
    import shutil
    root = project["root"]
    _board(root)
    assert cli.cmd_lane(project, _args("start", "B")) == 0
    shutil.rmtree(tmp_path / "proj-lanes" / "B")               # to free the disk, say
    capsys.readouterr()

    assert cli.cmd_lane(project, _args("list")) == 0
    assert "worktree gone" in capsys.readouterr().out
    assert cli.cmd_lane(project, _args("remove", "B")) == 0

    assert "lane B removed" in capsys.readouterr().out
    assert A.lane_records(root) == [] and _git(root, "branch", "--list", "lane/*") == ""
    assert len(A.worktree_list(root)) == 1
    assert cli.cmd_lane(project, _args("start", "B")) == 0       # and the item can start again


def test_a_worktree_git_could_not_add_leaves_no_branch_and_no_record_behind(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    # A required filter that fails, as a missing git-lfs does: git makes the branch, then fails the checkout.
    (Path(root) / "blob.bin").write_text("data\n", encoding="utf-8")
    (Path(root) / ".gitattributes").write_text("*.bin filter=broken\n", encoding="utf-8")
    _git(root, "add", "blob.bin", ".gitattributes")
    _git(root, "commit", "-q", "-m", "a filtered file")
    _git(root, "config", "filter.broken.smudge", "false")
    _git(root, "config", "filter.broken.required", "true")

    assert cli.cmd_lane(project, _args("start", "B")) == 1

    assert "lane B not started: git could not add the worktree" in capsys.readouterr().out
    assert _git(root, "branch", "--list", "lane/*") == "" and A.lane_records(root) == []
    assert len(A.worktree_list(root)) == 1 and not (tmp_path / "proj-lanes" / "B").exists()

    _git(root, "config", "--unset", "filter.broken.required")
    _git(root, "config", "filter.broken.smudge", "cat")
    assert cli.cmd_lane(project, _args("start", "B")) == 0      # nothing was left to refuse it


def test_what_ao_puts_in_a_lane_is_ignored_there_so_a_routine_commit_never_carries_it(project, tmp_path):
    _can_link(tmp_path)
    root = project["root"]
    _board(root)
    # The usual pattern ignores a directory, and a link at that name is no directory.
    (Path(root) / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "ignore dependencies")
    (Path(root) / "node_modules").mkdir()
    cfg = dict(project, lane={"link_paths": ["node_modules"], "env": ["WHERE={path}"]})

    assert cli.cmd_lane(cfg, _args("start", "B")) == 0

    lane = tmp_path / "proj-lanes" / "B"
    assert os.path.islink(lane / "node_modules") and (lane / ".env").is_file()
    assert _git(lane, "status", "--porcelain", "--untracked-files=all") == ""
    _git(lane, "add", "-A")
    _git(lane, "commit", "-q", "--allow-empty", "-m", "a routine commit in the lane")
    assert _git(lane, "ls-tree", "-r", "--name-only", "HEAD").split() == [".ao-project", ".gitignore"]
    # The repository's own exclude file says so, never a file of the branch.
    exclude = _git(root, "rev-parse", "--git-path", "info/exclude")
    assert {"/node_modules", "/.env"} <= set((Path(root) / exclude).read_text(encoding="utf-8").split("\n"))


def test_a_lane_another_checkout_started_is_refused_for_what_it_is(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    arch = Path(root) / ".claude" / "worktrees" / "arch"
    _git(root, "worktree", "add", "-q", "-b", "arch", str(arch))
    (arch / ".ao").mkdir()
    for name in ("config.json", "board.md"):
        (arch / ".ao" / name).write_text((Path(root) / ".ao" / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.cmd_lane(dict(project, root=str(arch)), _args("start", "B")) == 0
    capsys.readouterr()

    assert cli.cmd_lane(project, _args("start", "B")) == 2

    out = capsys.readouterr().out
    assert "is already a worktree of this repository, on lane/B" in out and "nests" not in out
    fact = next(fact for fact in A.worktree_facts(root, project) if fact["branch"] == "lane/B")
    assert not fact["may_go"] and any("another checkout" in why for why in fact["keep"])


def test_ao_remove_refuses_while_a_lane_stands(project, tmp_path, capsys):
    root = project["root"]
    _board(root)
    assert cli.cmd_lane(project, _args("start", "B")) == 0
    capsys.readouterr()

    assert cli.cmd_remove(project, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    assert "lane B" in capsys.readouterr().out
    assert cli.cmd_remove(project, SimpleNamespace(yes=True, allow_shared_hooks=False)) == 1

    out = capsys.readouterr().out
    assert "remove refused" in out and "`ao lane remove B`" in out
    assert (Path(root) / ".ao" / "lanes" / "B.json").is_file() and (Path(root) / ".ao-project").is_file()
