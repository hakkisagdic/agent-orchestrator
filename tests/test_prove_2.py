"""What the retrospective review of PROVE found that still held (PROVE-2).

A step of the throwaway slice that raised - staging its file, loading its config, or verify, review
or commit-ok themselves - ended `ao prove` in a traceback, where each check that fails says what
would fix it. And a `merge.link_paths` entry that could not be linked was dropped without a word, so
a gate that failed for want of it was reported as the one-line change failing.
"""
import os

from ao import cli, settings as S


def test_a_step_that_raises_is_a_check_that_failed_with_what_would_fix_it(project, monkeypatch):
    def broken(cfg, args):
        raise RuntimeError("the gates file is not JSON")

    monkeypatch.setattr(cli, "cmd_verify", broken)

    ok, failed, fix = cli._prove_throwaway(project)

    assert not ok and failed == "the throwaway slice stopped: RuntimeError: the gates file is not JSON" and fix


def test_a_path_that_could_not_be_linked_is_named_when_the_gates_fail(project, monkeypatch):
    os.makedirs(os.path.join(project["root"], "shared"))
    real = S.get
    monkeypatch.setattr(S, "get", lambda cfg, key: ["shared"] if key == "merge.link_paths" else real(cfg, key))

    def refuse(source, target):
        raise OSError("links are not allowed here")

    monkeypatch.setattr(cli.os, "symlink", refuse)
    monkeypatch.setattr(cli, "cmd_verify", lambda cfg, args: 1)

    ok, failed, _ = cli._prove_throwaway(project)

    assert not ok and failed.endswith("; merge.link_paths could not link shared (links are not allowed here)")
