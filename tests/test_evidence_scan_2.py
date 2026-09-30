"""A secret assigned to the name it goes by in an environment is redacted (EVIDENCE-SCAN-2).

The retrospective review of EVIDENCE-SCAN found the assigned-secret rule reading its name between `\b`
boundaries, which an underscore is not: DB_PASSWORD, CLIENT_SECRET and AWS_SECRET_ACCESS_KEY matched
nothing and were written into the repository as they were. And Slack's app-level tokens, `xapp-`, were
not the Slack tokens the rule named.
"""
import pytest

from ao import lib as A

VALUE = "Q7" + "zX9" * 5                       # fourteen characters of nothing in particular


@pytest.mark.parametrize("line", [
    f'POSTGRES_PASSWORD="{VALUE}"',
    f"DB_PASSWORD={VALUE}",
    f"CLIENT_SECRET={VALUE}",
    f"AWS_SECRET_ACCESS_KEY={VALUE}",
    f"export NPM_TOKEN={VALUE}",
    f'"client_secret": "{VALUE}"',
    f"password = {VALUE}",
])
def test_a_secret_assigned_to_the_name_it_goes_by_is_redacted(line):
    out, hits = A.scan_evidence(f"before {line} after")

    assert hits == ["assigned-secret"] and VALUE not in out and out.startswith("before ")


@pytest.mark.parametrize("line", ["max_tokens=128000", "password: short", "the secret is out", "tokens = 12"])
def test_a_short_value_or_no_assignment_is_left_as_it_is(line):
    assert A.scan_evidence(line) == (line, [])


def test_a_slack_app_level_token_is_redacted():
    token = "xa" + "pp-1-A0123456789-" + "0f" * 8

    assert A.scan_evidence(token)[1] == ["slack-token"]
    assert token not in A.redact(f"log {token}")
