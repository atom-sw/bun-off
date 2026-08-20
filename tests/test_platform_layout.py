from pathlib import Path

import pytest

from boff.errors import BoffError
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
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=root)
    op = opencode_instructions_glob_op(OPENCODE_LAYOUT.paths(scope))
    assert op.target == OPENCODE_LAYOUT.settings_path(root)
    assert op.merge is MergeStrategy.MERGE
    assert op.description == "opencode instructions glob"
    assert rules_glob in text_of(op)


# --- scope resolution ------------------------------------------------------------------------


@pytest.mark.parametrize("layout", [CLAUDE_LAYOUT, OPENCODE_LAYOUT, ANTIGRAVITY_LAYOUT])
def test_workspace_paths_sit_below_the_deploy_root(layout: PlatformLayout) -> None:
    root = Path("/ws")
    paths = layout.paths(Scope(kind=ScopeKind.WORKSPACE, workspace_root=root))
    surfaces = [
        paths.config_root,
        paths.mcp_file,
        paths.hooks_dir,
        paths.skills_dir,
        paths.agents_dir,
        paths.primary,
        *paths.native_roots,
    ]
    for surface in surfaces:
        assert surface is not None
        assert surface.is_relative_to(root)


@pytest.mark.parametrize("layout", [CLAUDE_LAYOUT, OPENCODE_LAYOUT, ANTIGRAVITY_LAYOUT])
def test_global_paths_sit_below_the_home_directory(
    layout: PlatformLayout, global_scope: Scope, fake_home: Path
) -> None:
    paths = layout.paths(global_scope)
    surfaces = [
        paths.config_root,
        paths.settings_file,
        paths.hooks_dir,
        paths.skills_dir,
        paths.agents_dir,
        *paths.native_roots,
    ]
    for surface in surfaces:
        assert surface is not None
        assert surface.is_relative_to(fake_home)


def test_claude_global_mirrors_the_workspace_tree_but_moves_its_primary_inside(
    global_scope: Scope, fake_home: Path
) -> None:
    paths = CLAUDE_LAYOUT.paths(global_scope)
    config_root = fake_home / ".claude"
    assert paths.config_root == config_root
    assert paths.rules_dir == config_root / "rules"
    assert paths.settings_file == config_root / "settings.json"
    # Unlike the workspace, where CLAUDE.md sits beside `.claude/` rather than inside it.
    assert paths.primary == config_root / "CLAUDE.md"


def test_claude_has_no_user_level_mcp_file(global_scope: Scope) -> None:
    # `~/.claude.json` holds credentials and is rewritten by every session, so boff never
    # merges into it; the adapter warns instead.
    assert CLAUDE_LAYOUT.paths(global_scope).mcp_file is None
    with pytest.raises(BoffError, match="no MCP config file"):
        CLAUDE_LAYOUT.paths(global_scope).require_mcp("claude")


def test_opencode_global_config_root_is_the_base_directory(
    global_scope: Scope, fake_home: Path
) -> None:
    config_root = fake_home / ".config" / "opencode"
    paths = OPENCODE_LAYOUT.paths(global_scope)
    assert paths.config_root == config_root
    # No nested `.opencode/`: agents, commands and skills sit directly under the base.
    assert paths.agents_dir == config_root / "agents"
    assert paths.settings_file == paths.mcp_file == config_root / "opencode.json"


def test_opencode_global_instructions_entry_is_tilde_prefixed_and_flat(
    global_scope: Scope,
) -> None:
    entry = OPENCODE_LAYOUT.paths(global_scope).instructions_entry
    assert entry == "~/.config/opencode/rules/*.md"
    # OpenCode globs only the last segment of an absolute entry, so `**` would match nothing.
    assert "**" not in entry


def test_opencode_global_honors_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    elsewhere = fake_home / "custom-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(elsewhere))
    paths = OPENCODE_LAYOUT.paths(Scope(kind=ScopeKind.GLOBAL))
    assert paths.config_root == elsewhere / "opencode"


def test_antigravity_global_splits_across_two_trees(global_scope: Scope, fake_home: Path) -> None:
    paths = ANTIGRAVITY_LAYOUT.paths(global_scope)
    gemini = fake_home / ".gemini"
    # Customizations live in the config root agy scans at startup...
    assert paths.config_root == gemini / "config"
    assert paths.skills_dir == gemini / "config" / "skills"
    assert paths.mcp_file == gemini / "config" / "mcp_config.json"
    # ...but settings live in an unrelated tree.
    assert paths.settings_file == gemini / "antigravity-cli" / "settings.json"


def test_antigravity_reads_rules_globally_but_not_in_a_workspace(global_scope: Scope) -> None:
    assert ANTIGRAVITY_LAYOUT.paths(global_scope).rules_dir is not None
    workspace = Scope(kind=ScopeKind.WORKSPACE, workspace_root=Path("/ws"))
    assert ANTIGRAVITY_LAYOUT.paths(workspace).rules_dir is None


def test_antigravity_reads_slash_commands_in_neither_scope(global_scope: Scope) -> None:
    assert ANTIGRAVITY_LAYOUT.paths(global_scope).commands_dir is None


def test_paths_raises_for_a_platform_without_a_global_surface() -> None:
    layout = PlatformLayout(
        name="nowhere",
        binary="nowhere",
        config_root=".nowhere",
        settings_file=None,
        mcp_file=".nowhere/mcp.json",
        mcp_key="mcpServers",
        rules_subdir=None,
        hooks_subdir=".nowhere/hooks",
        primary_filename="NOWHERE.md",
        rules_glob=None,
        native_names=(".nowhere",),
    )
    with pytest.raises(BoffError, match="no global"):
        layout.paths(Scope(kind=ScopeKind.GLOBAL))
