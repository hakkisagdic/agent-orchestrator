"""An implementer's grant is asked whether it admits changing any setting ao reads (SETTINGS-2).

The retrospective review of SETTINGS found the implementer audit asking only `ao config set review_timeout`:
a grant of `ao config set round_budget:*`, or of the waiver or attempt limits, let an implementer change
what governs its own work, and the doctor said nothing.
"""
import pytest

from ao import allowlist as AL, settings as S

EVERY = ("changes a setting that governs its own work",)


@pytest.mark.parametrize("key", ["round_budget", "watchdog.max_attempts", "waivers.default_hours",
                                 "decisions.human_after_minutes"])
def test_a_grant_to_change_one_setting_is_named(key):
    rule = f"Bash(ao config set {key}:*)"

    assert AL.problems(["claude", "--allowedTools", rule], role="implementer") == [
        (EVERY[0], f"ao config set {key} 1", rule)]


def test_a_grant_to_change_every_setting_adds_no_finding_for_each_setting():
    found = AL.problems(["claude", "--allowedTools", "Read,Bash(ao config set:*)"], role="implementer")

    assert {rule for _, _, rule in found} == {"Bash(ao config set:*)"}
    assert not any(reason == EVERY[0] for reason, _, _ in found)       # named already, for what it admits


def test_every_setting_ao_reads_is_asked():
    assert {command.split()[3] for command in AL.settings_commands()} == set(S.SETTINGS)


def test_only_the_implementer_is_held_to_it():
    assert AL.problems(["claude", "--allowedTools", "Bash(ao config set round_budget:*)"], role="architect") == []
