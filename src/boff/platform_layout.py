"""Single source of truth for each platform's on-disk config layout.

Both the platform adapters (`boff.adapters`) and the context providers
(`boff.context`) need to know where a platform keeps its rules, skills, settings,
MCP config, and hooks. That knowledge lived as scattered string literals across four
modules; it now lives here, one frozen :class:`PlatformLayout` per platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from boff.jsonutil import dumps_json
from boff.types import FileOperation, MergeStrategy, Scope


@dataclass(frozen=True)
class PlatformLayout:
    """Where one platform stores each kind of config, relative to the workspace root.

    ``settings_file``, ``rules_subdir``, and ``rules_glob`` are None when the platform has no
    such surface. Antigravity keeps its settings in a machine-global file that a
    workspace-scoped deploy must not touch, and loads rules only from ``primary_filename``.

    ``binary`` is the platform CLI's executable name as found on PATH. It is not derivable from
    ``name``: Antigravity's binary is ``agy``.
    """

    name: str
    binary: str
    config_root: str
    settings_file: str | None
    mcp_file: str
    mcp_key: str
    rules_subdir: str | None
    hooks_subdir: str
    primary_filename: str
    rules_glob: str | None

    def settings_path(self, root: Path) -> Path:
        """Return the workspace settings file, or raise if the platform has none."""
        if self.settings_file is None:
            raise ValueError(f"platform '{self.name}' has no workspace settings file")
        return root / self.settings_file


CLAUDE_LAYOUT = PlatformLayout(
    name="claude",
    binary="claude",
    config_root=".claude",
    settings_file=".claude/settings.json",
    mcp_file=".mcp.json",
    mcp_key="mcpServers",
    rules_subdir=".claude/rules",
    hooks_subdir=".claude/hooks",
    primary_filename="CLAUDE.md",
    rules_glob=".claude/rules/**/*.md",
)

OPENCODE_LAYOUT = PlatformLayout(
    name="opencode",
    binary="opencode",
    config_root=".opencode",
    settings_file="opencode.json",
    mcp_file="opencode.json",
    mcp_key="mcp",
    rules_subdir=".opencode/rules",
    hooks_subdir=".opencode/hooks",
    primary_filename="AGENTS.md",
    rules_glob=".opencode/rules/**/*.md",
)

# Verified against a live `agy`: `.agents/rules/*.md` is never loaded (flat or nested, bare or
# inside a plugin), and neither are `@`-includes from the instructions file, so `rules_subdir`
# is None and the adapter inlines rules into `primary_filename`. GEMINI.md rather than AGENTS.md
# because AGENTS.md is also OpenCode's primary file: deploying both platforms into one workspace
# would otherwise inject every rule twice, and clobber a file users commonly hand-author.
ANTIGRAVITY_LAYOUT = PlatformLayout(
    name="antigravity",
    binary="agy",
    config_root=".agents",
    settings_file=None,
    mcp_file=".agents/mcp_config.json",
    mcp_key="mcpServers",
    rules_subdir=None,
    hooks_subdir=".agents/hooks",
    primary_filename="GEMINI.md",
    rules_glob=None,
)


def require_workspace_root(scope: Scope) -> Path:
    """Return the scope's workspace root or raise if it is unset."""
    if scope.workspace_root is None:
        raise ValueError("workspace_root must be set on the scope")
    return scope.workspace_root


def opencode_instructions_glob_op(root: Path) -> FileOperation:
    """Build the MERGE op that registers OpenCode's rules glob in ``opencode.json``.

    OpenCode loads instructions globally from a glob rather than per-file, so every rule
    write is accompanied by this idempotent registration. Emitted both when deploying rules
    (`boff.adapters.opencode`) and when materializing a migrated bundle (`boff.context.opencode`).
    """
    return FileOperation(
        target=OPENCODE_LAYOUT.settings_path(root),
        content=dumps_json({"instructions": [OPENCODE_LAYOUT.rules_glob]}),
        merge=MergeStrategy.MERGE,
        description="opencode instructions glob",
    )
