from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from boff.adapters import adapter_names, get_adapter
from boff.artifacts import (
    EQUIVALENT_SHAPES,
    Agent,
    Artifact,
    EventHook,
    EventHooks,
    MCPServer,
    Permissions,
    Rule,
    Rules,
    Settings,
    Skill,
    SlashCommand,
)
from boff.deploy import (
    Support,
    _support,  # pyright: ignore[reportPrivateUsage]
    artifact_label,
    deploy_plan,
    fold_units,
    plan_units,
)
from boff.manifest import Manifest, ManifestMeta, Plugin, PluginInstall, load_manifest
from boff.sources.local import LocalSpec
from boff.types import FileOperation, Operation, Scope

DeployOps = Callable[[Manifest, str, Scope], list[Operation]]


def _targets(ops: list[Operation]) -> set[Path]:
    return {op.target for op in ops if isinstance(op, FileOperation)}


def test_deploy_emits_expected_ops(
    sample_manifest: Path, tmp_path: Path, workspace_scope: Scope, deploy_ops: DeployOps
) -> None:
    manifest = load_manifest(sample_manifest)
    ops = deploy_ops(manifest, "claude", workspace_scope)

    targets = _targets(ops)
    assert tmp_path / ".claude" / "rules" / "dev-essentials" / "style.md" in targets
    assert tmp_path / ".claude" / "skills" / "format" / "SKILL.md" in targets
    assert tmp_path / ".claude" / "commands" / "lint.md" in targets
    assert tmp_path / ".claude" / "agents" / "reviewer.md" in targets
    assert tmp_path / ".mcp.json" in targets
    assert tmp_path / "greeting.txt" in targets
    assert tmp_path / ".config" / "mise" / "conf.d" / "boff-0.toml" in targets
    assert tmp_path / ".claude" / "hooks" / "_boff_dispatch.py" in targets

    mcp_ops = [
        op for op in ops if isinstance(op, FileOperation) and op.target == tmp_path / ".mcp.json"
    ]
    assert len(mcp_ops) == 2  # one MERGE op per MCP server


def test_deploy_opencode_targets(
    sample_manifest: Path, tmp_path: Path, workspace_scope: Scope, deploy_ops: DeployOps
) -> None:
    manifest = load_manifest(sample_manifest)
    ops = deploy_ops(manifest, "opencode", workspace_scope)

    targets = _targets(ops)
    assert tmp_path / ".opencode" / "rules" / "dev-essentials" / "style.md" in targets
    assert tmp_path / ".opencode" / "skills" / "format" / "SKILL.md" in targets
    assert tmp_path / ".opencode" / "commands" / "lint.md" in targets
    assert tmp_path / ".opencode" / "agents" / "reviewer.md" in targets
    assert tmp_path / "opencode.json" in targets
    assert tmp_path / ".opencode" / "hooks" / "audit" in targets
    assert tmp_path / ".opencode" / "hooks" / "lint" in targets
    assert tmp_path / ".opencode" / "plugins" / "boff-hooks.js" in targets
    assert tmp_path / "greeting.txt" in targets

    config_ops = [
        op
        for op in ops
        if isinstance(op, FileOperation) and op.target == tmp_path / "opencode.json"
    ]
    # instructions glob (style rule) + one per MCP server + permissions block + settings block
    # (event hooks write .opencode/hooks/* and a plugin, not opencode.json)
    assert len(config_ops) == 5


def test_deploy_antigravity_plugin(tmp_path: Path, workspace_scope: Scope) -> None:
    """The local source installs plugins for antigravity the same way as for claude."""
    plugin_dir = tmp_path / "bundle" / "plugins" / "hi"
    plugin_dir.mkdir(parents=True)
    greeting = "hello\n"
    (plugin_dir / "greeting.txt").write_text(greeting)
    manifest = Manifest(
        root=tmp_path / "bundle",
        meta=ManifestMeta(name="t", description="t"),
        plugins=(
            Plugin(
                name="hi",
                install={
                    "antigravity": PluginInstall(
                        source="local",
                        spec=LocalSpec(source="local", path=plugin_dir),
                    )
                },
            ),
        ),
    )
    plan = deploy_plan(manifest, ["antigravity"], workspace_scope)
    targets = _targets(plan.get("antigravity", []))
    assert tmp_path / "greeting.txt" in targets


def test_deploy_plan_buckets_by_owner(
    sample_manifest: Path, tmp_path: Path, workspace_scope: Scope
) -> None:
    manifest = load_manifest(sample_manifest)
    plan = deploy_plan(manifest, ["claude"], workspace_scope)

    assert set(plan) == {"claude", "tool:mise"}
    # The plugin (greeting.txt) is owned by its platform, not a separate bucket.
    claude_targets = _targets(plan["claude"])
    assert tmp_path / "greeting.txt" in claude_targets
    assert tmp_path / ".claude" / "rules" / "dev-essentials" / "style.md" in claude_targets
    mise_targets = _targets(plan["tool:mise"])
    assert mise_targets == {tmp_path / ".config" / "mise" / "conf.d" / "boff-0.toml"}


def test_deploy_plan_is_a_fold_over_plan_units(
    sample_manifest: Path, workspace_scope: Scope
) -> None:
    # `deploy_plan` must stay byte-identical to its pre-refactor self: same owners, same op
    # order. `state._owner_record` records merged key paths in op order, and that lands on disk.
    manifest = load_manifest(sample_manifest)
    platforms = ["claude", "opencode"]

    assert deploy_plan(manifest, platforms, workspace_scope) == fold_units(
        plan_units(manifest, platforms, workspace_scope)
    )


def test_plan_units_emits_platform_major_artifacts_then_plugins_then_tools(
    sample_manifest: Path, workspace_scope: Scope
) -> None:
    manifest = load_manifest(sample_manifest)
    owners = [unit.owner for unit in plan_units(manifest, ["claude"], workspace_scope)]
    labels = [unit.label for unit in plan_units(manifest, ["claude"], workspace_scope)]

    assert owners[-1] == "tool:mise"
    assert labels[-1] == "mise"
    assert labels[-2] == "plugin say-hi"
    assert labels[0] == "rule style"


def test_plan_units_keeps_units_that_emitted_nothing(
    tmp_path: Path, workspace_scope: Scope, write_manifest: Callable[..., Path]
) -> None:
    # `fold_units` drops them, but `boff check` needs the attribution to classify each artifact.
    # Not the sample manifest: its `reviewer` agent carries per-agent permissions, which
    # antigravity rejects outright.
    root = tmp_path / "stack"
    write_manifest(
        root,
        "rules:\n  - style\nslash_commands:\n  - lint\n"
        "permissions:\n  allow:\n    - { tool: read }\n"
        # A settings block reaches an adapter only for the platforms it names, so this one
        # must name antigravity for its warn-and-skip renderer to run at all.
        "settings:\n  antigravity:\n    theme: dark\n",
    )
    (root / "rules").mkdir()
    (root / "rules" / "style.md").write_text("# style\n")
    (root / "slash_commands").mkdir()
    (root / "slash_commands" / "lint.md").write_text("# lint\n")

    manifest = load_manifest(root)
    units = plan_units(manifest, ["antigravity"], workspace_scope)

    dropped = [unit for unit in units if unit.support is Support.DROPPED]
    assert {unit.label for unit in dropped} == {"slash command lint", "permissions", "settings"}
    assert all(not unit.ops for unit in dropped)
    assert "antigravity" not in fold_units(dropped)


@pytest.mark.parametrize(
    ("platform", "kind", "expected"),
    [
        pytest.param("claude", Rule, Support.RENDERED, id="claude-renders-each-rule"),
        pytest.param("claude", Rules, Support.SHADOWED, id="claude-shadows-the-aggregate"),
        pytest.param(
            "antigravity", Rules, Support.RENDERED, id="antigravity-renders-the-aggregate"
        ),
        pytest.param("antigravity", Rule, Support.SHADOWED, id="antigravity-shadows-each-rule"),
        pytest.param("antigravity", SlashCommand, Support.DROPPED, id="antigravity-drops-commands"),
        pytest.param("antigravity", Settings, Support.DROPPED, id="antigravity-drops-settings"),
        pytest.param("antigravity", Permissions, Support.DROPPED, id="antigravity-drops-perms"),
        pytest.param("antigravity", EventHook, Support.UNSUPPORTED, id="no-renderer-at-all"),
    ],
)
def test_support_classifies_each_adapter_artifact_pair(
    platform: str, kind: type[Any], expected: Support
) -> None:
    assert _support(get_adapter(platform), kind) is expected


@pytest.mark.parametrize("platform", adapter_names())
def test_an_adapter_renders_at_most_one_shape_per_equivalent_group(platform: str) -> None:
    # `Rules`' docstring says an adapter must handle exactly one of `Rule` / `Rules`. Rendering
    # both would deploy every rule twice; `_support` would also never report SHADOWED.
    adapter = get_adapter(platform)
    for group in EQUIVALENT_SHAPES:
        assert len([kind for kind in group if adapter.supports(kind)]) <= 1


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        pytest.param(Rule(name="style", content=""), "rule style", id="rule"),
        pytest.param(Rules(), "rules", id="rules"),
        pytest.param(Skill(name="format", content=""), "skill format", id="skill"),
        pytest.param(SlashCommand(name="lint", content=""), "slash command lint", id="command"),
        pytest.param(MCPServer(name="ctx7", raw={}), "mcp server ctx7", id="mcp"),
        pytest.param(Permissions(), "permissions", id="permissions"),
        pytest.param(
            Agent(name="reviewer", description="d", content=""), "agent reviewer", id="agent"
        ),
        pytest.param(Settings(raw={}), "settings", id="settings"),
        pytest.param(EventHooks(), "event hooks", id="event-hooks"),
    ],
)
def test_artifact_label_names_each_artifact_type(artifact: Artifact, expected: str) -> None:
    assert artifact_label(artifact) == expected
