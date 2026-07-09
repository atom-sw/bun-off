from pathlib import Path

import pytest

from boff.platform_layout import (
    ANTIGRAVITY_LAYOUT,
    CLAUDE_LAYOUT,
    OPENCODE_LAYOUT,
    PlatformLayout,
    opencode_instructions_glob_op,
    require_workspace_root,
)
from boff.types import MergeStrategy, Scope, ScopeKind
from tests.conftest import text_of


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        pytest.param(
            CLAUDE_LAYOUT,
            {
                "name": "claude",
                "binary": "claude",
                "config_root": ".claude",
                "settings_file": ".claude/settings.json",
                "mcp_file": ".mcp.json",
                "mcp_key": "mcpServers",
                "primary_filename": "CLAUDE.md",
                "rules_subdir": ".claude/rules",
            },
            id="claude",
        ),
        pytest.param(
            OPENCODE_LAYOUT,
            {
                "name": "opencode",
                "binary": "opencode",
                "config_root": ".opencode",
                "settings_file": "opencode.json",
                "mcp_file": "opencode.json",
                "mcp_key": "mcp",
                "primary_filename": "AGENTS.md",
                "rules_subdir": ".opencode/rules",
            },
            id="opencode",
        ),
        pytest.param(
            ANTIGRAVITY_LAYOUT,
            {
                "name": "antigravity",
                # Not derivable from `name`: the Antigravity CLI's executable is `agy`.
                "binary": "agy",
                "config_root": ".agents",
                # Antigravity keeps settings in a machine-global file and loads rules only
                # from its primary instructions file, so neither has a workspace surface.
                "settings_file": None,
                "mcp_file": ".agents/mcp_config.json",
                "mcp_key": "mcpServers",
                "primary_filename": "GEMINI.md",
                "rules_subdir": None,
            },
            id="antigravity",
        ),
    ],
)
def test_layout_fields(layout: PlatformLayout, expected: dict[str, str | None]) -> None:
    for field, value in expected.items():
        assert getattr(layout, field) == value
    assert layout.hooks_subdir == f"{expected['config_root']}/hooks"


def test_settings_path_joins_the_workspace_root() -> None:
    root = Path("/tmp/wk")
    settings_file = CLAUDE_LAYOUT.settings_file
    assert settings_file is not None
    assert CLAUDE_LAYOUT.settings_path(root) == root / settings_file


def test_settings_path_raises_when_the_platform_has_none() -> None:
    with pytest.raises(ValueError, match=ANTIGRAVITY_LAYOUT.name):
        ANTIGRAVITY_LAYOUT.settings_path(Path("/tmp/wk"))


def test_require_workspace_root_returns_root() -> None:
    root = Path("/tmp/wk")
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=root)
    assert require_workspace_root(scope) == root


def test_require_workspace_root_raises_when_unset() -> None:
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=None)
    with pytest.raises(ValueError, match="workspace_root"):
        require_workspace_root(scope)


def test_opencode_instructions_glob_op_registers_the_rules_glob() -> None:
    root = Path("/tmp/wk")
    rules_glob = OPENCODE_LAYOUT.rules_glob
    assert rules_glob is not None
    op = opencode_instructions_glob_op(root)
    assert op.target == OPENCODE_LAYOUT.settings_path(root)
    assert op.merge is MergeStrategy.MERGE
    assert op.description == "opencode instructions glob"
    assert rules_glob in text_of(op)
