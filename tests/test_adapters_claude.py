import json
from pathlib import Path

import pytest

from boff.adapters.claude import ClaudeAdapter
from boff.artifacts import (
    Agent,
    EventHook,
    EventHooks,
    MCPServer,
    OutputStyle,
    PermissionRule,
    Permissions,
    Rule,
    Settings,
    Skill,
    SlashCommand,
)
from boff.types import FileOperation, MergeStrategy, Scope, ScopeKind
from tests.conftest import file_op, text_of

WORKSPACE = Path("/tmp/wk")
SCOPE = Scope(kind=ScopeKind.WORKSPACE, workspace_root=WORKSPACE)


def test_render_rule_with_category() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(
        Rule(name="x", content="body", category="dev"),
        platform="claude",
        scope=SCOPE,
    )
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / ".claude" / "rules" / "dev" / "x.md"
    assert op.content == "body"


def test_render_rule_without_category_is_flat() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(Rule(name="x", content="body"), platform="claude", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".claude" / "rules" / "x.md"


def test_render_rule_without_globs_has_no_frontmatter() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(Rule(name="x", content="body"), platform="claude", scope=SCOPE)
    assert text_of(ops[0]) == "body"


def test_render_rule_with_globs_prepends_frontmatter() -> None:
    adapter = ClaudeAdapter()
    globs = ("**/*.cs", "**/Controllers/**")
    ops = adapter.render(
        Rule(name="x", content="body", globs=globs),
        platform="claude",
        scope=SCOPE,
    )
    assert text_of(ops[0]) == f'---\nglobs: "{", ".join(globs)}"\n---\n\nbody'


def test_render_skill_is_folder_per_skill() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(Skill(name="s", content="body"), platform="claude", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".claude" / "skills" / "s" / "SKILL.md"


def test_render_slash_command() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(SlashCommand(name="lint", content="body"), platform="claude", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".claude" / "commands" / "lint.md"


def test_render_output_style_uses_claudes_hyphenated_directory() -> None:
    adapter = ClaudeAdapter()
    style = OutputStyle(
        name="tutor",
        content="---\nname: Tutor\nkeep-coding-instructions: true\n---\n\nexplain first",
    )
    ops = adapter.render(style, platform="claude", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".claude" / "output-styles" / "tutor.md"


def test_render_output_style_ships_the_body_verbatim() -> None:
    # Claude validates output-style frontmatter against a strict schema, so boff must not
    # rewrite or inject keys: whatever the author wrote is what deploys.
    style = OutputStyle(
        name="tutor",
        content="---\nname: Tutor\nkeep-coding-instructions: true\n---\n\nexplain first",
    )
    ops = ClaudeAdapter().render(style, platform="claude", scope=SCOPE)
    assert text_of(ops[0]) == style.content


def test_render_mcp_server() -> None:
    adapter = ClaudeAdapter()
    ops = adapter.render(
        MCPServer(name="ctx", raw={"claude": {"command": "uvx"}}),
        platform="claude",
        scope=SCOPE,
    )
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / ".mcp.json"
    assert op.merge is MergeStrategy.MERGE
    payload = json.loads(op.content)
    assert payload == {"mcpServers": {"ctx": {"command": "uvx"}}}


def test_render_permissions() -> None:
    adapter = ClaudeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(tool="read", action="allow", pattern="./src/**"),
            PermissionRule(tool="webfetch", action="allow", pattern="github.com"),
            PermissionRule(tool="bash", action="ask", pattern="git push *"),
            PermissionRule(tool="edit", action="ask"),
            PermissionRule(tool="bash", action="deny", pattern="curl *"),
        )
    )
    ops = adapter.render(perms, platform="claude", scope=SCOPE)
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / ".claude" / "settings.json"
    assert op.merge is MergeStrategy.MERGE
    assert json.loads(op.content) == {
        "permissions": {
            "allow": ["Read(./src/**)", "WebFetch(domain:github.com)"],
            "ask": ["Bash(git push *)", "Edit"],
            "deny": ["Bash(curl *)"],
        }
    }


def test_render_permissions_mcp_rule() -> None:
    adapter = ClaudeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(
                tool="mcp", action="allow", pattern="github__*", available_on=frozenset({"claude"})
            ),
        )
    )
    ops = adapter.render(perms, platform="claude", scope=SCOPE)
    assert json.loads(text_of(ops[0])) == {"permissions": {"allow": ["mcp__github__*"]}}


def test_render_permissions_excludes_rules_scoped_to_other_platform() -> None:
    adapter = ClaudeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(tool="lsp", action="allow", available_on=frozenset({"opencode"})),
            PermissionRule(tool="bash", action="allow", pattern="ls"),
        )
    )
    ops = adapter.render(perms, platform="claude", scope=SCOPE)
    assert json.loads(text_of(ops[0])) == {"permissions": {"allow": ["Bash(ls)"]}}


def test_render_permissions_unsupported_tool_raises() -> None:
    adapter = ClaudeAdapter()
    perms = Permissions(rules=(PermissionRule(tool="lsp", action="allow"),))
    with pytest.raises(ValueError, match="not supported on claude"):
        adapter.render(perms, platform="claude", scope=SCOPE)


def test_render_agent() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="reviewer",
        content="body",
        description="Read-only reviewer",
        model="haiku",
        mode="subagent",
        permissions=(
            PermissionRule(tool="read", action="allow"),
            PermissionRule(tool="grep", action="allow"),
            PermissionRule(tool="edit", action="deny"),
            PermissionRule(tool="bash", action="deny"),
        ),
    )
    ops = adapter.render(agent, platform="claude", scope=SCOPE)
    assert len(ops) == 1
    op = file_op(ops[0])
    assert op.target == WORKSPACE / ".claude" / "agents" / "reviewer.md"
    assert op.merge is MergeStrategy.OVERWRITE
    content = text_of(op)
    assert content.endswith("body")
    fm = content.split("---")[1]
    assert "name: reviewer" in fm
    assert "model: haiku" in fm
    assert "tools: Read, Grep" in fm
    assert "disallowedTools: Edit, Bash" in fm
    assert "mode:" not in fm  # Claude has no per-agent mode


def test_render_agent_model_map_picks_claude_value() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="planner",
        content="body",
        description="d",
        model={"claude": "opus", "opencode": "anthropic/claude-opus-4-8"},
    )
    fm = text_of(adapter.render(agent, platform="claude", scope=SCOPE)[0]).split("---")[1]
    assert "model: opus" in fm


def test_render_agent_model_map_miss_omits_model() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="planner",
        content="body",
        description="d",
        model={"opencode": "anthropic/claude-opus-4-8"},
    )
    fm = text_of(adapter.render(agent, platform="claude", scope=SCOPE)[0]).split("---")[1]
    assert "model:" not in fm


def test_render_agent_excludes_opencode_scoped_rules() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="r",
        content="body",
        description="d",
        permissions=(
            PermissionRule(tool="read", action="allow"),
            PermissionRule(
                tool="bash", action="ask", pattern="git *", available_on=frozenset({"opencode"})
            ),
        ),
    )
    ops = adapter.render(agent, platform="claude", scope=SCOPE)
    assert "tools: Read" in text_of(ops[0])


def test_render_agent_pattern_rule_raises() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="r",
        content="body",
        description="d",
        permissions=(PermissionRule(tool="bash", action="deny", pattern="git *"),),
    )
    with pytest.raises(ValueError, match="pattern"):
        adapter.render(agent, platform="claude", scope=SCOPE)


def test_render_agent_ask_rule_raises() -> None:
    adapter = ClaudeAdapter()
    agent = Agent(
        name="r",
        content="body",
        description="d",
        permissions=(PermissionRule(tool="edit", action="ask"),),
    )
    with pytest.raises(ValueError, match="ask"):
        adapter.render(agent, platform="claude", scope=SCOPE)


def test_render_settings() -> None:
    adapter = ClaudeAdapter()
    settings = Settings(
        raw={
            "claude": {"outputStyle": "Explanatory", "cleanupPeriodDays": 14},
            "opencode": {"theme": "tokyonight"},
        }
    )
    ops = adapter.render(settings, platform="claude", scope=SCOPE)
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / ".claude" / "settings.json"
    assert op.merge is MergeStrategy.MERGE
    assert json.loads(op.content) == {"outputStyle": "Explanatory", "cleanupPeriodDays": 14}


def test_render_settings_without_claude_block_is_noop() -> None:
    adapter = ClaudeAdapter()
    settings = Settings(raw={"opencode": {"theme": "tokyonight"}})
    assert adapter.render(settings, platform="claude", scope=SCOPE) == []


def test_render_event_hooks() -> None:
    adapter = ClaudeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(name="audit", event="after_bash", command="logger x", script_content="x\n"),
            EventHook(
                name="lint", event="after_edit", script="lint.sh", script_content="y\n", timeout=30
            ),
        )
    )
    ops = adapter.render(eh, platform="claude", scope=SCOPE)
    targets = {op.target for op in ops if isinstance(op, FileOperation)}
    assert WORKSPACE / ".claude" / "hooks" / "_boff_dispatch.py" in targets
    assert WORKSPACE / ".claude" / "hooks" / "audit" in targets
    assert WORKSPACE / ".claude" / "hooks" / "lint" in targets

    settings_op = next(
        op
        for op in ops
        if isinstance(op, FileOperation) and op.target == WORKSPACE / ".claude" / "settings.json"
    )
    assert settings_op.merge is MergeStrategy.MERGE
    block = json.loads(settings_op.content)["hooks"]
    # Both map to PostToolUse; grouped by matcher in authored order (Bash, then Edit|Write).
    matchers = [g["matcher"] for g in block["PostToolUse"]]
    assert matchers == ["Bash", "Edit|Write"]
    bash_entry = block["PostToolUse"][0]["hooks"][0]
    assert bash_entry["type"] == "command"
    assert "_boff_dispatch.py" in bash_entry["command"]
    assert "after_bash" in bash_entry["command"]
    assert ".claude/hooks/audit" in bash_entry["command"]
    assert block["PostToolUse"][1]["hooks"][0]["timeout"] == 30


def test_render_session_event_hooks() -> None:
    adapter = ClaudeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(
                name="warm", event="session_start", command="tldr warm .", script_content="w\n"
            ),
            EventHook(name="bye", event="session_end", command="echo bye", script_content="b\n"),
        )
    )
    ops = adapter.render(eh, platform="claude", scope=SCOPE)
    settings_op = next(
        op
        for op in ops
        if isinstance(op, FileOperation) and op.target == WORKSPACE / ".claude" / "settings.json"
    )
    block = json.loads(settings_op.content)["hooks"]
    # Session events carry no matcher.
    assert "matcher" not in block["SessionStart"][0]
    assert "matcher" not in block["SessionEnd"][0]
    start_cmd = block["SessionStart"][0]["hooks"][0]["command"]
    assert "session_start" in start_cmd
    assert ".claude/hooks/warm" in start_cmd
    assert "session_end" in block["SessionEnd"][0]["hooks"][0]["command"]


def test_render_event_hooks_excludes_opencode_scoped() -> None:
    adapter = ClaudeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(
                name="only-oc",
                event="on_finish",
                command="x",
                script_content="x\n",
                available_on=frozenset({"opencode"}),
            ),
        )
    )
    assert adapter.render(eh, platform="claude", scope=SCOPE) == []


@pytest.mark.parametrize(
    "artifact_type",
    [Rule, Skill, SlashCommand, OutputStyle, MCPServer, Permissions, Agent, Settings, EventHooks],
)
def test_adapter_supports_each_artifact_type(artifact_type: type) -> None:
    assert ClaudeAdapter().supports(artifact_type)
