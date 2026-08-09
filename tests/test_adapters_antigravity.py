import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from boff.adapters.antigravity import AntigravityAdapter
from boff.artifacts import (
    Agent,
    EventHook,
    EventHooks,
    MCPServer,
    OutputStyle,
    PermissionRule,
    Permissions,
    Rule,
    Rules,
    Settings,
    Skill,
    SlashCommand,
)
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ScopeKind
from tests.conftest import file_op, text_of

PLATFORM = "antigravity"
CONFIG_ROOT = ".agents"
PRIMARY = "GEMINI.md"
WORKSPACE = Path("/tmp/wk")
SCOPE = Scope(kind=ScopeKind.WORKSPACE, workspace_root=WORKSPACE)


def render(adapter: AntigravityAdapter, artifact: object) -> list[Operation]:
    return adapter.render(artifact, platform=PLATFORM, scope=SCOPE)


# --- rules: the whole set collapses into the primary instructions file ----------------------


def test_render_rules_inlines_every_rule_into_the_primary_file() -> None:
    adapter = AntigravityAdapter()
    first, second = Rule(name="alpha", content="body-a"), Rule(name="beta", content="body-b")
    ops = render(adapter, Rules(rules=(first, second)))

    assert len(ops) == 1
    op = file_op(ops[0])
    assert op.target == WORKSPACE / PRIMARY
    assert op.merge is MergeStrategy.OVERWRITE
    content = text_of(ops[0])
    for rule in (first, second):
        assert f"## {rule.name}" in content
        assert rule.content in content
    assert content.index(first.name) < content.index(second.name)


def test_render_rules_ignores_the_category_because_there_is_no_rules_directory() -> None:
    adapter = AntigravityAdapter()
    ops = render(adapter, Rules(rules=(Rule(name="x", content="body", category="dev"),)))
    assert file_op(ops[0]).target == WORKSPACE / PRIMARY


def test_render_rules_excludes_rules_scoped_to_other_platforms() -> None:
    adapter = AntigravityAdapter()
    mine = Rule(name="mine", content="keep")
    theirs = Rule(name="theirs", content="drop", available_on=frozenset({"claude"}))
    content = text_of(render(adapter, Rules(rules=(mine, theirs)))[0])
    assert mine.content in content
    assert theirs.content not in content


def test_render_rules_with_no_applicable_rules_is_a_noop() -> None:
    adapter = AntigravityAdapter()
    only_claude = Rule(name="x", content="body", available_on=frozenset({"claude"}))
    assert render(adapter, Rules(rules=(only_claude,))) == []


def test_render_rules_warns_that_globs_are_not_enforced(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    scoped = Rule(name="scoped", content="body", globs=("**/*.py",))
    with caplog.at_level(logging.WARNING):
        content = text_of(render(adapter, Rules(rules=(scoped,)))[0])
    assert scoped.name in caplog.text
    assert "globs" in caplog.text
    # The rule still deploys, unscoped and without frontmatter.
    assert scoped.content in content
    assert "---" not in content


def test_adapter_does_not_render_an_individual_rule() -> None:
    # Rules load only from the primary file, so the per-rule artifact has no target here.
    assert not AntigravityAdapter().supports(Rule)


# --- surfaces inherited from the base adapter -----------------------------------------------


def test_render_skill_is_folder_per_skill() -> None:
    adapter = AntigravityAdapter()
    ops = render(adapter, Skill(name="s", content="body"))
    assert file_op(ops[0]).target == WORKSPACE / CONFIG_ROOT / "skills" / "s" / "SKILL.md"


def test_render_mcp_server_merges_into_the_workspace_mcp_config() -> None:
    adapter = AntigravityAdapter()
    raw = {"command": "uvx", "args": ["ctx"]}
    ops = render(adapter, MCPServer(name="ctx", raw={PLATFORM: raw}))

    op = file_op(ops[0])
    assert op.target == WORKSPACE / CONFIG_ROOT / "mcp_config.json"
    assert op.merge is MergeStrategy.MERGE
    assert json.loads(text_of(ops[0])) == {"mcpServers": {"ctx": raw}}


# --- subagents -------------------------------------------------------------------------------


def test_render_agent_writes_frontmatter_and_body() -> None:
    adapter = AntigravityAdapter()
    agent = Agent(name="rev", content="You review.", description="Reviews diffs.")
    ops = render(adapter, agent)

    op = file_op(ops[0])
    assert op.target == WORKSPACE / CONFIG_ROOT / "agents" / f"{agent.name}.md"
    content = text_of(ops[0])
    assert (
        content
        == f"---\nname: {agent.name}\ndescription: {agent.description}\n---\n\n{agent.content}"
    )


def test_render_agent_warns_and_ignores_a_model_because_subagents_inherit_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = AntigravityAdapter()
    agent = Agent(name="rev", content="body", description="d", model="gemini-3-pro")
    with caplog.at_level(logging.WARNING):
        content = text_of(render(adapter, agent)[0])
    assert agent.name in caplog.text
    assert isinstance(agent.model, str)
    assert agent.model not in content


def test_render_agent_with_per_agent_permissions_raises() -> None:
    adapter = AntigravityAdapter()
    agent = Agent(
        name="rev",
        content="body",
        description="d",
        permissions=(PermissionRule(tool="bash", action="allow"),),
    )
    with pytest.raises(ValueError, match="per-agent permissions"):
        render(adapter, agent)


def test_render_agent_ignores_permissions_scoped_to_other_platforms() -> None:
    adapter = AntigravityAdapter()
    agent = Agent(
        name="rev",
        content="body",
        description="d",
        permissions=(
            PermissionRule(tool="bash", action="allow", available_on=frozenset({"claude"})),
        ),
    )
    assert file_op(render(adapter, agent)[0]).target.name == f"{agent.name}.md"


# --- event hooks -----------------------------------------------------------------------------


def _hooks_json(ops: list[Operation]) -> dict[str, Any]:
    """Return the parsed `.agents/hooks.json` payload from a render's operations."""
    for op in ops:
        if isinstance(op, FileOperation) and op.target.name == "hooks.json":
            parsed: dict[str, Any] = json.loads(text_of(op))
            return parsed
    raise AssertionError("no hooks.json operation was emitted")


@pytest.mark.parametrize(
    ("event", "native_event", "matcher"),
    [
        pytest.param("after_edit", "PostToolUse", "write_to_file", id="after_edit"),
        pytest.param("after_bash", "PostToolUse", "run_command", id="after_bash"),
        pytest.param("on_finish", "Stop", None, id="on_finish"),
    ],
)
def test_render_event_hook_maps_onto_its_native_event(
    event: str, native_event: str, matcher: str | None
) -> None:
    adapter = AntigravityAdapter()
    hook = EventHook(name="h", event=event, script_content="#!/bin/sh\n")
    block = _hooks_json(render(adapter, EventHooks(hooks=(hook,))))

    handlers: Any = block[f"boff-{hook.name}"][native_event]
    if matcher is None:
        # Flat events list handler objects directly, with no matcher wrapper.
        assert handlers[0]["type"] == "command"
    else:
        assert matcher in handlers[0]["matcher"]
        assert handlers[0]["hooks"][0]["type"] == "command"


def test_render_event_hooks_writes_the_dispatcher_and_each_script() -> None:
    adapter = AntigravityAdapter()
    hook = EventHook(name="fmt", event="after_edit", script_content="#!/bin/sh\necho hi\n")
    ops = render(adapter, EventHooks(hooks=(hook,)))

    targets = {file_op(op).target for op in ops}
    hooks_dir = WORKSPACE / CONFIG_ROOT / "hooks"
    assert hooks_dir / "_boff_dispatch.py" in targets
    assert hooks_dir / hook.name in targets
    assert WORKSPACE / CONFIG_ROOT / "hooks.json" in targets


def test_render_event_hook_carries_its_timeout_into_the_handler() -> None:
    adapter = AntigravityAdapter()
    timeout = 17
    hook = EventHook(name="h", event="on_finish", script_content="x", timeout=timeout)
    block = _hooks_json(render(adapter, EventHooks(hooks=(hook,))))
    assert block[f"boff-{hook.name}"]["Stop"][0]["timeout"] == timeout


def test_render_tool_scoped_hook_passes_its_matcher_to_the_dispatcher() -> None:
    adapter = AntigravityAdapter()
    hook = EventHook(name="h", event="after_bash", script_content="x")
    block = _hooks_json(render(adapter, EventHooks(hooks=(hook,))))
    handler: Any = block[f"boff-{hook.name}"]["PostToolUse"][0]
    # The dispatcher re-checks the matcher, because Antigravity runs PostToolUse handlers with
    # a null toolCall at invocation boundaries without consulting it.
    assert handler["hooks"][0]["command"].endswith(f'"{handler["matcher"]}"')


def test_flat_hook_passes_no_matcher_to_the_dispatcher() -> None:
    adapter = AntigravityAdapter()
    hook = EventHook(name="h", event="on_finish", script_content="x")
    block = _hooks_json(render(adapter, EventHooks(hooks=(hook,))))
    command: Any = block[f"boff-{hook.name}"]["Stop"][0]["command"]
    assert command.endswith(hook.name)


@pytest.mark.parametrize("event", ["session_start", "session_end"])
def test_render_session_event_hooks_warn_and_emit_nothing(
    event: str, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = AntigravityAdapter()
    hook = EventHook(name="h", event=event, script_content="x")
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, EventHooks(hooks=(hook,)))
    assert ops == []
    assert event in caplog.text


def test_render_event_hooks_keeps_supported_events_when_one_is_unsupported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = AntigravityAdapter()
    supported = EventHook(name="keep", event="after_bash", script_content="x")
    unsupported = EventHook(name="drop", event="session_end", script_content="x")
    with caplog.at_level(logging.WARNING):
        block = _hooks_json(render(adapter, EventHooks(hooks=(supported, unsupported))))
    assert f"boff-{supported.name}" in block
    assert f"boff-{unsupported.name}" not in block


# --- the generated dispatcher, driven with real Antigravity payload shapes --------------------


def _dispatcher_source() -> str:
    """Return the dispatcher exactly as a deploy would write it."""
    ops = render(AntigravityAdapter(), EventHooks(hooks=(EventHook(name="h", event="on_finish"),)))
    for op in ops:
        if isinstance(op, FileOperation) and op.target.name == "_boff_dispatch.py":
            return text_of(op)
    raise AssertionError("no dispatcher operation was emitted")


def _run_dispatcher(
    tmp_path: Path, payload: dict[str, Any], *, event: str, matcher: str | None
) -> tuple[str, str]:
    """Deploy the dispatcher into ``tmp_path`` and run it on ``payload``. Return (stdout, trace).

    The hook script records the BOFF_* contract it was invoked with; an empty trace means the
    dispatcher declined to run it.
    """
    hooks_dir = tmp_path / CONFIG_ROOT / "hooks"
    hooks_dir.mkdir(parents=True)
    dispatcher = hooks_dir / "_boff_dispatch.py"
    dispatcher.write_text(_dispatcher_source())
    trace = tmp_path / "trace.txt"
    (hooks_dir / "h").write_text(
        f'#!/bin/sh\nprintf "%s|%s|%s" "$BOFF_TOOL" "$BOFF_FILE" "$BOFF_COMMAND" > {trace}\n'
    )
    payload = {**payload, "workspacePaths": [str(tmp_path)]}

    argv = [sys.executable, str(dispatcher), event, "h"]
    if matcher is not None:
        argv.append(matcher)
    result = subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True, check=True
    )
    return result.stdout, trace.read_text() if trace.exists() else ""


def test_dispatcher_exports_the_boff_contract_from_a_run_command_payload(tmp_path: Path) -> None:
    command = "pytest -q"
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": command}}}
    stdout, trace = _run_dispatcher(tmp_path, payload, event="after_bash", matcher="run_command")

    assert stdout == "{}"  # Antigravity requires a JSON object on stdout
    assert trace == f"run_command||{command}"


def test_dispatcher_exports_the_edited_file_from_a_write_to_file_payload(tmp_path: Path) -> None:
    target = "/src/app.py"
    payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": target}}}
    _, trace = _run_dispatcher(tmp_path, payload, event="after_edit", matcher="write_to_file")

    assert trace == f"write_to_file|{target}|"


def test_dispatcher_skips_a_tool_scoped_hook_when_the_payload_has_no_tool_call(
    tmp_path: Path,
) -> None:
    # Antigravity emits PostToolUse at invocation boundaries with a null toolCall, and runs the
    # handler without applying the matcher. The hook must not fire with an empty BOFF_COMMAND.
    stdout, trace = _run_dispatcher(
        tmp_path, {"toolCall": None}, event="after_bash", matcher="run_command"
    )
    assert stdout == "{}"
    assert not trace


def test_dispatcher_skips_a_tool_scoped_hook_when_another_tool_ran(tmp_path: Path) -> None:
    payload = {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "/src/app.py"}}}
    _, trace = _run_dispatcher(tmp_path, payload, event="after_bash", matcher="run_command")
    assert not trace


def test_dispatcher_runs_a_flat_hook_with_no_matcher_and_no_tool_call(tmp_path: Path) -> None:
    stdout, trace = _run_dispatcher(tmp_path, {}, event="on_finish", matcher=None)
    assert stdout == "{}"  # any decision other than "continue" lets the agent stop
    assert trace == "||"


# --- surfaces Antigravity has no workspace target for ----------------------------------------


def test_render_slash_command_warns_and_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    command = SlashCommand(name="ship", content="body")
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, command)
    assert ops == []
    assert command.name in caplog.text


def test_render_output_style_warns_and_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    style = OutputStyle(name="tutor", content="body")
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, style)
    assert ops == []
    assert style.name in caplog.text


def test_render_settings_warns_and_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, Settings(raw={PLATFORM: {"colorScheme": "light"}}))
    assert ops == []
    assert "global" in caplog.text


def test_render_permissions_warns_and_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    rules = (PermissionRule(tool="bash", action="allow"),)
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, Permissions(rules=rules))
    assert ops == []
    assert "global" in caplog.text


def test_render_settings_for_another_platform_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    adapter = AntigravityAdapter()
    with caplog.at_level(logging.WARNING):
        ops = render(adapter, Settings(raw={"claude": {"outputStyle": "Explanatory"}}))
    assert ops == []
    assert not caplog.text


# --- wiring ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact_type",
    [Rules, Skill, SlashCommand, OutputStyle, MCPServer, Permissions, Agent, Settings, EventHooks],
)
def test_adapter_supports_each_artifact_type(artifact_type: type) -> None:
    assert AntigravityAdapter().supports(artifact_type)


def test_native_roots_covers_the_config_root_and_the_generated_primary_file() -> None:
    roots = AntigravityAdapter().native_roots(SCOPE)
    assert set(roots) == {WORKSPACE / CONFIG_ROOT, WORKSPACE / PRIMARY}
