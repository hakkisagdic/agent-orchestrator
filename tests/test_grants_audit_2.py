"""What the retrospective review of ACTOR-GRANTS found that still held (GRANTS-AUDIT-2).

A grant through `--trust-tools` names tools, not commands, and the audit read no rule from it: a grant
that trusted Kiro's shell tool was reported as admitting nothing. A grant written into a launch other
than the resume went unread. `ao commit -m ""` was told it had given both or neither of -m and -F, and
the `-F` form behind the shipped `Bash(ao commit -F:*)` grant had no test that reached Git.
"""
import subprocess
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, lib as A

EVERY_COMMAND = "trusts the shell tool, which runs every command"


@pytest.mark.parametrize("argv, tool", [
    (["kiro-cli", "chat", "--trust-tools", "read,shell"], "shell"),
    (["kiro-cli", "chat", "--trust-tools=fs_read,execute_bash"], "execute_bash"),
    (["kiro-cli", "chat", "--trust-tools=execute_cmd"], "execute_cmd"),
    (["kiro-cli", "chat", "--trust-tools", "*"], "*"),
])
def test_a_grant_that_trusts_the_shell_tool_is_named_as_admitting_every_command(argv, tool):
    assert AL.problems(argv, role="architect") == [(EVERY_COMMAND, "*", f"--trust-tools {tool}")]


def test_a_grant_that_trusts_no_tool_that_runs_a_command_is_not_named():
    assert AL.problems(["kiro-cli", "chat", "--trust-tools=read,grep,glob"], role="reviewer") == []
    assert AL.problems(["kiro-cli", "chat", "--trust-tools="], role="reviewer") == []


def test_the_doctor_says_which_shell_tool_a_grant_trusts(project):
    cfg = dict(project, architect={"name": "architect", "argv": ["kiro-cli", "chat", "--trust-tools", "read,shell"]})

    text = dict(cli._actor_grant_problems(cfg))["actor-grant:architect"]

    assert "architect (architect) trusts the shell tool (--trust-tools shell), which runs every command" in text


def test_a_grant_written_into_another_launch_is_audited_too(project, monkeypatch):
    shipped = A.load_adapter("claude-code")

    def adapter(ident, root=None):
        found = dict(shipped)
        found["send"] = dict(shipped.get("send") or {},
                             argv=["claude", "-p", "{prompt}", "--allowedTools", "Read,Bash(git commit:*)"])
        return found

    monkeypatch.setattr(A, "load_adapter", adapter)
    cfg = dict(project, implementer={"adapter": "claude-code"})

    problems = dict(cli._actor_grant_problems(cfg))

    assert "Bash(git commit:*) admits `git commit --no-verify -m x` (skips the commit hook)" \
        in problems["actor-grant:implementer-send"]


def test_ao_commit_hands_git_a_message_file_as_it_is(project, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, "cmd_commit_check", lambda cfg, args: 0)
    monkeypatch.setattr(A, "landed_commit_problem", lambda root: None)

    assert cli.cmd_commit(project, SimpleNamespace(message=None, file="msg.txt")) == 0
    assert calls == [["git", "commit", "-F", "msg.txt"]]


@pytest.mark.parametrize("message, file", [("", None), ("   ", None), (None, ""), (None, None)])
def test_ao_commit_says_an_empty_message_is_empty(project, capsys, message, file):
    assert cli.cmd_commit(project, SimpleNamespace(message=message, file=file)) == 2
    assert "ao commit needs a message" in capsys.readouterr().out
