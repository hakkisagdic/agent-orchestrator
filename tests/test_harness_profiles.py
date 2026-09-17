from types import SimpleNamespace

from ao import cli, lib as A


def _args(**overrides):
    return SimpleNamespace(**dict(dict(profile="claude-kiro", implementer=None, model=None, effort=None,
                                       reviewer_model=None), **overrides))


def test_a_profile_composes_its_blocks_from_the_adapters_it_names(project):
    roles = A.profiles()["claude-kiro"]

    cfg, added = cli._profile_config(project["root"], _args(), {})

    assert added == ["implementer", "reviewer", "architect"]
    assert cfg["implementer"]["name"] == A.load_adapter(roles["implementer"])["actor_name"]
    review_model = A.load_adapter(roles["reviewer"])["models"]["review"]
    assert cfg["reviewer"]["argv"] == A.compose_reviewer(roles["reviewer"], model=review_model)["argv"]
    assert cfg["reviewer"]["id"].endswith(f"-reviewer-{review_model}") and cfg["reviewer"]["family"]
    options = A.load_adapter(roles["architect"])["options"]
    tools = options["allowed_tools"][0]
    assert cfg["architect"]["argv"].count(tools) == 1 and cfg["architect"]["argv"][-1] == options["architect_tools"]
    assert cli.PROJECT_INIT_COMMAND == f"ao init --profile {A.default_profile()}"


def test_an_unnamed_implementer_is_named_by_its_adapter_and_an_absent_one_by_the_default_profile(project):
    cfg = {key: value for key, value in project.items() if key != "implementer"}

    assert A.mail_names(cfg)[0] == A.load_adapter(A.profiles()[A.default_profile()]["implementer"])["actor_name"]
    assert A.mail_names(dict(cfg, implementer={"adapter": "claude-code"}))[0] == "claude"
    assert A.mail_names(dict(cfg, implementer={"adapter": "claude-code", "name": "dev"}))[0] == "dev"
