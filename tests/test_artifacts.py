from collections.abc import Callable
from typing import Any

import pytest

from boff.artifacts import (
    Agent,
    MCPServer,
    OutputStyle,
    PermissionRule,
    Permissions,
    Rule,
    Skill,
    SlashCommand,
)

# Each factory builds one artifact type, forwarding availability kwargs, so the shared
# `available_on` contract is asserted once per type instead of copied per type. Omitting
# `available_on` entirely is what exercises the default, so the kwargs stay variadic.


def _make_rule(**kw: Any) -> Rule:
    return Rule(name="x", content="body", **kw)


def _make_permission_rule(**kw: Any) -> PermissionRule:
    return PermissionRule(tool="bash", action="allow", **kw)


def _make_agent(**kw: Any) -> Agent:
    return Agent(name="a", content="body", description="d", **kw)


def _make_output_style(**kw: Any) -> OutputStyle:
    return OutputStyle(name="tutor", content="body", **kw)


_AVAILABILITY_FACTORIES = [
    pytest.param(_make_rule, id="rule"),
    pytest.param(_make_permission_rule, id="permission"),
    pytest.param(_make_agent, id="agent"),
    pytest.param(_make_output_style, id="output_style"),
]


@pytest.mark.parametrize("make", _AVAILABILITY_FACTORIES)
def test_available_on_defaults_to_all_platforms(make: Callable[..., Any]) -> None:
    artifact = make()
    assert artifact.is_available_on("claude")
    assert artifact.is_available_on("opencode")


@pytest.mark.parametrize("make", _AVAILABILITY_FACTORIES)
def test_available_on_restricts_to_listed_platforms(make: Callable[..., Any]) -> None:
    artifact = make(available_on=frozenset({"claude"}))
    assert artifact.is_available_on("claude")
    assert not artifact.is_available_on("opencode")


def test_rule_globs_default_empty() -> None:
    assert Rule(name="x", content="body").globs == ()


def test_rule_globs_preserve_order() -> None:
    rule = Rule(name="x", content="body", globs=("**/*.cs", "**/Controllers/**"))
    assert rule.globs == ("**/*.cs", "**/Controllers/**")


def test_skill_carries_no_category() -> None:
    skill = Skill(name="s", content="body")
    assert not hasattr(skill, "category")


def test_slash_command_round_trip() -> None:
    cmd = SlashCommand(name="lint", content="# lint")
    assert cmd.name == "lint"
    assert cmd.content == "# lint"


def test_output_style_round_trip() -> None:
    body = "---\nname: Tutor\n---\n\nexplain first"
    style = OutputStyle(name="tutor", content=body)
    assert style.name == "tutor"
    assert style.content == body


def test_mcp_server_holds_raw_per_platform() -> None:
    server = MCPServer(name="x", raw={"claude": {"command": "uvx"}})
    assert server.raw["claude"]["command"] == "uvx"


def test_permissions_rules_for_filters_by_platform() -> None:
    perms = Permissions(
        rules=(
            PermissionRule(tool="bash", action="allow"),
            PermissionRule(tool="mcp", action="allow", available_on=frozenset({"claude"})),
        )
    )
    assert len(perms.rules_for("claude")) == 2
    assert len(perms.rules_for("opencode")) == 1


def test_permissions_available_on_defaults_to_all() -> None:
    perms = Permissions()
    assert perms.is_available_on("claude")
    assert perms.is_available_on("opencode")


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        pytest.param(None, {"claude": None, "opencode": None}, id="none"),
        pytest.param("haiku", {"claude": "haiku", "opencode": "haiku"}, id="string"),
        pytest.param(
            {"claude": "opus", "opencode": "anthropic/claude-opus-4-8"},
            {"claude": "opus", "opencode": "anthropic/claude-opus-4-8"},
            id="map",
        ),
        pytest.param({"claude": "opus"}, {"claude": "opus", "opencode": None}, id="map-miss"),
    ],
)
def test_agent_model_for_resolves_per_platform(
    model: str | dict[str, str] | None, expected: dict[str, str | None]
) -> None:
    agent = Agent(name="a", content="body", description="d", model=model)
    assert agent.model_for("claude") == expected["claude"]
    assert agent.model_for("opencode") == expected["opencode"]


def test_agent_permissions_for_filters_by_platform() -> None:
    agent = Agent(
        name="a",
        content="body",
        description="d",
        permissions=(
            PermissionRule(tool="read", action="allow"),
            PermissionRule(
                tool="bash", action="ask", pattern="*", available_on=frozenset({"opencode"})
            ),
        ),
    )
    assert len(agent.permissions_for("claude")) == 1
    assert len(agent.permissions_for("opencode")) == 2
