import json
import logging
from pathlib import Path

import pytest
import yaml

from boff.adapters.opencode import OpenCodeAdapter
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
    adapter = OpenCodeAdapter()
    ops = adapter.render(
        Rule(name="x", content="body", category="dev"),
        platform="opencode",
        scope=SCOPE,
    )
    assert len(ops) == 2
    file_op, instr_op = ops
    assert isinstance(file_op, FileOperation)
    assert file_op.target == WORKSPACE / ".opencode" / "rules" / "dev" / "x.md"
    assert file_op.content == "body"
    assert file_op.merge is MergeStrategy.OVERWRITE
    assert isinstance(instr_op, FileOperation)
    assert instr_op.target == WORKSPACE / "opencode.json"
    assert instr_op.merge is MergeStrategy.MERGE
    assert json.loads(instr_op.content) == {"instructions": [".opencode/rules/**/*.md"]}


def test_render_rule_without_category_is_flat() -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(Rule(name="x", content="body"), platform="opencode", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".opencode" / "rules" / "x.md"


def test_render_rule_with_globs_warns_and_deploys_unscoped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = OpenCodeAdapter()
    with caplog.at_level("WARNING", logger="boff.adapters.opencode"):
        ops = adapter.render(
            Rule(name="x", content="body", globs=("**/*.cs",)),
            platform="opencode",
            scope=SCOPE,
        )
    assert len(ops) == 2
    assert text_of(ops[0]) == "body"
    assert "scope will not be enforced" in caplog.text


def test_render_skill_is_folder_per_skill() -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(Skill(name="s", content="body"), platform="opencode", scope=SCOPE)
    assert file_op(ops[0]).target == WORKSPACE / ".opencode" / "skills" / "s" / "SKILL.md"


def test_render_slash_command() -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(
        SlashCommand(name="lint", content="body"), platform="opencode", scope=SCOPE
    )
    assert file_op(ops[0]).target == WORKSPACE / ".opencode" / "commands" / "lint.md"


def test_render_mcp_server() -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(
        MCPServer(name="ctx", raw={"opencode": {"type": "local", "command": ["uvx"]}}),
        platform="opencode",
        scope=SCOPE,
    )
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / "opencode.json"
    assert op.merge is MergeStrategy.MERGE
    payload = json.loads(op.content)
    assert payload == {"mcp": {"ctx": {"type": "local", "command": ["uvx"]}}}


def test_render_mcp_server_without_opencode_entry_raises() -> None:
    adapter = OpenCodeAdapter()
    with pytest.raises(ValueError, match="no 'opencode' entry"):
        adapter.render(
            MCPServer(name="ctx", raw={"claude": {"command": "uvx"}}),
            platform="opencode",
            scope=SCOPE,
        )


def test_render_permissions_orders_by_precedence() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(tool="bash", action="allow", pattern="git *"),
            PermissionRule(tool="bash", action="deny", pattern="rm *"),
            PermissionRule(tool="bash", action="ask", pattern="*"),
        )
    )
    ops = adapter.render(perms, platform="opencode", scope=SCOPE)
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / "opencode.json"
    assert op.merge is MergeStrategy.MERGE
    bash = json.loads(op.content)["permission"]["bash"]
    assert list(bash.items()) == [("git *", "allow"), ("*", "ask"), ("rm *", "deny")]


def test_render_permissions_no_pattern_is_verdict_string() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(rules=(PermissionRule(tool="edit", action="ask"),))
    ops = adapter.render(perms, platform="opencode", scope=SCOPE)
    assert json.loads(text_of(ops[0])) == {"permission": {"edit": "ask"}}


def test_render_permissions_write_collapses_into_edit() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(tool="write", action="deny", pattern="./build/**"),
            PermissionRule(tool="edit", action="allow", pattern="./src/**"),
        )
    )
    ops = adapter.render(perms, platform="opencode", scope=SCOPE)
    edit = json.loads(text_of(ops[0]))["permission"]["edit"]
    assert list(edit.items()) == [("./src/**", "allow"), ("./build/**", "deny")]


def test_render_permissions_webfetch_passes_host_verbatim() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(
        rules=(PermissionRule(tool="webfetch", action="allow", pattern="github.com"),)
    )
    ops = adapter.render(perms, platform="opencode", scope=SCOPE)
    assert json.loads(text_of(ops[0])) == {"permission": {"webfetch": {"github.com": "allow"}}}


def test_render_permissions_unsupported_tool_raises() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(rules=(PermissionRule(tool="mcp", action="allow", pattern="x"),))
    with pytest.raises(ValueError, match="not supported on opencode"):
        adapter.render(perms, platform="opencode", scope=SCOPE)


def test_render_permissions_excludes_rules_scoped_to_other_platform() -> None:
    adapter = OpenCodeAdapter()
    perms = Permissions(
        rules=(
            PermissionRule(
                tool="mcp", action="allow", pattern="x", available_on=frozenset({"claude"})
            ),
            PermissionRule(tool="bash", action="allow", pattern="ls"),
        )
    )
    ops = adapter.render(perms, platform="opencode", scope=SCOPE)
    assert json.loads(text_of(ops[0])) == {"permission": {"bash": {"ls": "allow"}}}


def test_render_agent() -> None:
    adapter = OpenCodeAdapter()
    agent = Agent(
        name="reviewer",
        content="body",
        description="Read-only reviewer",
        model="anthropic/claude-haiku",
        mode="subagent",
        permissions=(
            PermissionRule(tool="read", action="allow"),
            PermissionRule(tool="bash", action="allow", pattern="git log *"),
            PermissionRule(tool="bash", action="deny", pattern="git push *"),
            PermissionRule(tool="bash", action="ask", pattern="*"),
        ),
    )
    ops = adapter.render(agent, platform="opencode", scope=SCOPE)
    assert len(ops) == 1
    op = file_op(ops[0])
    assert op.target == WORKSPACE / ".opencode" / "agents" / "reviewer.md"
    assert op.merge is MergeStrategy.OVERWRITE
    content = text_of(op)
    assert content.endswith("body")
    fm = yaml.safe_load(content.split("---")[1])
    assert fm["description"] == "Read-only reviewer"
    assert fm["mode"] == "subagent"
    assert fm["model"] == "anthropic/claude-haiku"
    assert fm["permission"]["read"] == "allow"
    bash = fm["permission"]["bash"]
    assert list(bash.items()) == [("git log *", "allow"), ("*", "ask"), ("git push *", "deny")]


def test_render_agent_model_map_picks_opencode_value() -> None:
    adapter = OpenCodeAdapter()
    agent = Agent(
        name="planner",
        content="body",
        description="d",
        model={"claude": "opus", "opencode": "anthropic/claude-opus-4-8"},
    )
    content = text_of(adapter.render(agent, platform="opencode", scope=SCOPE)[0])
    fm = yaml.safe_load(content.split("---")[1])
    assert fm["model"] == "anthropic/claude-opus-4-8"


def test_render_agent_model_map_miss_omits_model() -> None:
    adapter = OpenCodeAdapter()
    agent = Agent(name="planner", content="body", description="d", model={"claude": "opus"})
    content = text_of(adapter.render(agent, platform="opencode", scope=SCOPE)[0])
    fm = yaml.safe_load(content.split("---")[1])
    assert "model" not in fm


def test_render_agent_unsupported_tool_raises() -> None:
    adapter = OpenCodeAdapter()
    agent = Agent(
        name="r",
        content="body",
        description="d",
        permissions=(PermissionRule(tool="mcp", action="allow", pattern="x"),),
    )
    with pytest.raises(ValueError, match="not supported on opencode"):
        adapter.render(agent, platform="opencode", scope=SCOPE)


def test_render_settings() -> None:
    adapter = OpenCodeAdapter()
    settings = Settings(
        raw={
            "claude": {"outputStyle": "Explanatory"},
            "opencode": {"theme": "tokyonight", "autoupdate": False},
        }
    )
    ops = adapter.render(settings, platform="opencode", scope=SCOPE)
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == WORKSPACE / "opencode.json"
    assert op.merge is MergeStrategy.MERGE
    assert json.loads(op.content) == {"theme": "tokyonight", "autoupdate": False}


def test_render_settings_without_opencode_block_is_noop() -> None:
    adapter = OpenCodeAdapter()
    settings = Settings(raw={"claude": {"outputStyle": "Explanatory"}})
    assert adapter.render(settings, platform="opencode", scope=SCOPE) == []


def test_render_event_hooks_generates_plugin() -> None:
    adapter = OpenCodeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(name="lint", event="after_edit", script="lint.sh", script_content="y\n"),
            EventHook(name="audit", event="after_bash", command="logger x", script_content="x\n"),
            EventHook(name="notify", event="on_finish", command="ding", script_content="ding\n"),
        )
    )
    ops = adapter.render(eh, platform="opencode", scope=SCOPE)
    targets = {op.target for op in ops if isinstance(op, FileOperation)}
    assert WORKSPACE / ".opencode" / "hooks" / "lint" in targets
    assert WORKSPACE / ".opencode" / "hooks" / "audit" in targets
    assert WORKSPACE / ".opencode" / "hooks" / "notify" in targets

    plugin_op = next(
        op
        for op in ops
        if isinstance(op, FileOperation)
        and op.target == WORKSPACE / ".opencode" / "plugins" / "boff-hooks.js"
    )
    js = plugin_op.content
    assert isinstance(js, str)
    assert "'tool.execute.after'" in js
    assert "'session.idle'" in js
    assert 'input.tool === "edit" || input.tool === "write"' in js
    assert 'input.tool === "bash"' in js
    assert 'BOFF_EVENT: "after_edit"' in js
    assert "BOFF_COMMAND: command" in js
    assert '"/.opencode/hooks/" + name' in js


def test_render_session_event_hooks() -> None:
    adapter = OpenCodeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(
                name="warm", event="session_start", command="tldr warm .", script_content="w\n"
            ),
            EventHook(name="bye", event="session_end", command="echo bye", script_content="b\n"),
        )
    )
    ops = adapter.render(eh, platform="opencode", scope=SCOPE)
    plugin_op = next(
        op
        for op in ops
        if isinstance(op, FileOperation)
        and op.target == WORKSPACE / ".opencode" / "plugins" / "boff-hooks.js"
    )
    js = plugin_op.content
    assert isinstance(js, str)
    assert "'session.start': async () => {" in js
    assert "'session.deleted': async () => {" in js
    assert 'BOFF_EVENT: "session_start"' in js
    assert 'await run("warm",' in js


def test_render_event_hooks_excludes_claude_scoped() -> None:
    adapter = OpenCodeAdapter()
    eh = EventHooks(
        hooks=(
            EventHook(
                name="only-claude",
                event="on_finish",
                command="x",
                script_content="x\n",
                available_on=frozenset({"claude"}),
            ),
        )
    )
    assert adapter.render(eh, platform="opencode", scope=SCOPE) == []


def test_render_output_style_warns_and_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    adapter = OpenCodeAdapter()
    style = OutputStyle(name="tutor", content="body")
    with caplog.at_level(logging.WARNING):
        ops = adapter.render(style, platform="opencode", scope=SCOPE)
    assert ops == []
    assert style.name in caplog.text
    # The warning must point at the alternative, not just refuse.
    assert "mode: primary" in caplog.text


@pytest.mark.parametrize(
    "artifact_type",
    [Rule, Skill, SlashCommand, OutputStyle, MCPServer, Permissions, Agent, Settings, EventHooks],
)
def test_adapter_supports_each_artifact_type(artifact_type: type) -> None:
    assert OpenCodeAdapter().supports(artifact_type)


# --- global scope ----------------------------------------------------------------------------


def test_global_rule_lands_in_the_user_rules_directory(
    global_scope: Scope, fake_home: Path
) -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(Rule(name="x", content="body"), platform="opencode", scope=global_scope)
    config_root = fake_home / ".config" / "opencode"
    assert file_op(ops[0]).target == config_root / "rules" / "x.md"
    assert file_op(ops[1]).target == config_root / "opencode.json"


def test_global_rule_registers_a_tilde_prefixed_flat_glob(global_scope: Scope) -> None:
    adapter = OpenCodeAdapter()
    ops = adapter.render(Rule(name="x", content="body"), platform="opencode", scope=global_scope)
    assert json.loads(text_of(ops[1])) == {"instructions": ["~/.config/opencode/rules/*.md"]}


def test_global_rule_with_a_category_warns_and_deploys_flat(
    global_scope: Scope, fake_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = OpenCodeAdapter()
    with caplog.at_level(logging.WARNING):
        ops = adapter.render(
            Rule(name="x", content="body", category="dev"),
            platform="opencode",
            scope=global_scope,
        )
    # A user-level instructions entry cannot recurse, so the category cannot be honored.
    assert file_op(ops[0]).target == fake_home / ".config" / "opencode" / "rules" / "x.md"
    assert "cannot recurse" in caplog.text


def test_every_global_rule_emits_an_identical_instructions_entry(global_scope: Scope) -> None:
    # `json_deep_merge` replaces lists wholesale, so entries that differed per rule would
    # clobber one another and only the last would survive.
    adapter = OpenCodeAdapter()
    entries = {
        text_of(adapter.render(rule, platform="opencode", scope=global_scope)[1])
        for rule in (
            Rule(name="a", content="body"),
            Rule(name="b", content="body", category="dev"),
        )
    }
    assert len(entries) == 1


@pytest.mark.parametrize(
    ("artifact", "relative"),
    [
        pytest.param(Skill(name="s", content="body"), "skills/s/SKILL.md", id="skill"),
        pytest.param(SlashCommand(name="c", content="body"), "commands/c.md", id="slash-command"),
        pytest.param(Agent(name="a", description="d", content="body"), "agents/a.md", id="agent"),
    ],
)
def test_global_artifacts_sit_directly_under_the_config_root(
    artifact: object, relative: str, global_scope: Scope, fake_home: Path
) -> None:
    # No nested `.opencode/` at user level: the config root *is* the base directory.
    adapter = OpenCodeAdapter()
    ops = adapter.render(artifact, platform="opencode", scope=global_scope)
    assert file_op(ops[0]).target == fake_home / ".config" / "opencode" / relative
