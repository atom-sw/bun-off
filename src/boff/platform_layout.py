"""Single source of truth for each platform's on-disk config layout, in every scope.

Both the platform adapters (`boff.adapters`) and the context providers
(`boff.context`) need to know where a platform keeps its rules, skills, settings,
MCP config, and hooks. That knowledge lived as scattered string literals across four
modules; it now lives here, one frozen :class:`PlatformLayout` per platform.

A layout describes two scopes. The workspace scope is a set of paths relative to the
deploy root. The global scope is *not* the same tree rehomed under ``$HOME``: Claude's
instructions file moves inside its config root, OpenCode's config root *is* the base
directory, and Antigravity splits across two unrelated trees. So each platform resolves
its own :class:`ScopePaths`, and callers ask :meth:`PlatformLayout.paths` rather than
joining strings onto a root themselves.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from boff.errors import BoffError
from boff.jsonutil import dumps_json
from boff.types import FileOperation, MergeStrategy, Scope, ScopeKind


@dataclass(frozen=True)
class ScopePaths:
    """Absolute locations of one platform's config surfaces, resolved for one scope.

    A ``None`` field means the platform has no such surface *in this scope*, which is not
    always the same as having none at all: Claude has an MCP file in a workspace but no safe
    one at user level, and Antigravity reads slash commands in neither.
    """

    config_root: Path
    settings_file: Path | None
    mcp_file: Path | None
    rules_dir: Path | None
    hooks_dir: Path
    skills_dir: Path
    commands_dir: Path | None
    agents_dir: Path
    primary: Path
    instructions_entry: str | None
    native_roots: tuple[Path, ...]

    def require_settings(self, platform: str) -> Path:
        """Return the settings file, or raise if the platform has none in this scope."""
        if self.settings_file is None:
            raise BoffError(f"platform '{platform}' has no settings file in this scope")
        return self.settings_file

    def require_mcp(self, platform: str) -> Path:
        """Return the MCP config file, or raise if the platform has none in this scope."""
        if self.mcp_file is None:
            raise BoffError(f"platform '{platform}' has no MCP config file in this scope")
        return self.mcp_file


@dataclass(frozen=True)
class PlatformLayout:
    """Where one platform stores each kind of config, per scope.

    The ``str``-typed fields are workspace paths relative to the deploy root. ``settings_file``,
    ``rules_subdir``, and ``rules_glob`` are None when the platform has no such workspace
    surface. Antigravity keeps its settings in a machine-global file that a workspace-scoped
    deploy must not touch, and loads workspace rules only from ``primary_filename``.

    ``global_factory`` resolves the user-level paths. It is a callable rather than a stored
    :class:`ScopePaths` because it reads ``$HOME`` and ``$XDG_CONFIG_HOME`` at call time, so
    tests can point them at a temporary directory. None means the platform has no user-level
    surface at all.

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
    native_names: tuple[str, ...]
    global_factory: Callable[[], ScopePaths] | None = None

    def settings_path(self, root: Path) -> Path:
        """Return the workspace settings file, or raise if the platform has none."""
        if self.settings_file is None:
            raise ValueError(f"platform '{self.name}' has no workspace settings file")
        return root / self.settings_file

    def paths(self, scope: Scope) -> ScopePaths:
        """Resolve this platform's config surfaces for ``scope``.

        Raises :class:`BoffError` for a global scope on a platform with no user-level surface.
        """
        if scope.kind is ScopeKind.GLOBAL:
            if self.global_factory is None:
                raise BoffError(f"platform '{self.name}' has no global (user-level) config")
            return self.global_factory()
        return self._workspace_paths(require_workspace_root(scope))

    def _workspace_paths(self, root: Path) -> ScopePaths:
        """Resolve the workspace surfaces below ``root`` from this layout's relative names."""
        config_root = root / self.config_root
        return ScopePaths(
            config_root=config_root,
            settings_file=root / self.settings_file if self.settings_file else None,
            mcp_file=root / self.mcp_file,
            rules_dir=root / self.rules_subdir if self.rules_subdir else None,
            hooks_dir=root / self.hooks_subdir,
            skills_dir=config_root / "skills",
            commands_dir=config_root / "commands",
            agents_dir=config_root / "agents",
            primary=root / self.primary_filename,
            instructions_entry=self.rules_glob,
            native_roots=tuple(root / name for name in self.native_names),
        )


def _home() -> Path:
    """The user's home directory, read at call time so tests can relocate it."""
    return Path.home()


def xdg_config_home() -> Path:
    """``$XDG_CONFIG_HOME`` when set to an absolute path, else ``~/.config``."""
    raw = os.environ.get("XDG_CONFIG_HOME")
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
    return _home() / ".config"


def home_relative_entry(path: Path, pattern: str) -> str:
    """Spell ``path/pattern`` with a ``~/`` prefix when it sits under the home directory.

    OpenCode accepts both spellings in ``instructions``; the tilde form keeps a user-level
    config file portable across machines whose home directories differ.
    """
    home = _home()
    target = path / pattern
    try:
        return f"~/{target.relative_to(home).as_posix()}"
    except ValueError:
        return target.as_posix()


# Claude's user-level tree mirrors the workspace one: `~/.claude/rules/**/*.md` (nested category
# directories included), `skills/`, `agents/`, `commands/`, `output-styles/`, and
# `settings.json` all load. Two things differ. `CLAUDE.md` moves *inside* the config root, and
# there is no safe MCP target: user-scope servers live in `~/.claude.json`, an OAuth-bearing file
# Claude Code rewrites every session, so `mcp_file` is None and the adapter warns instead.
def _claude_global() -> ScopePaths:
    """Resolve Claude Code's user-level config surfaces."""
    config_root = _home() / ".claude"
    return ScopePaths(
        config_root=config_root,
        settings_file=config_root / "settings.json",
        mcp_file=None,
        rules_dir=config_root / "rules",
        hooks_dir=config_root / "hooks",
        skills_dir=config_root / "skills",
        commands_dir=config_root / "commands",
        agents_dir=config_root / "agents",
        primary=config_root / "CLAUDE.md",
        instructions_entry=None,
        native_roots=(config_root,),
    )


# OpenCode's config root *is* the base directory: agents/commands/skills sit directly under
# `~/.config/opencode/`, not under a nested `.opencode/`. `AGENTS.md` is deliberately absent from
# what the adapter writes -- OpenCode picks exactly one global instructions file from
# `[~/.config/opencode/AGENTS.md, ~/.claude/CLAUDE.md]`, first match wins, so creating it would
# silently stop OpenCode reading the user's `~/.claude/CLAUDE.md`.
def _opencode_global() -> ScopePaths:
    """Resolve OpenCode's user-level config surfaces."""
    config_root = xdg_config_home() / "opencode"
    settings = config_root / "opencode.json"
    rules_dir = config_root / "rules"
    return ScopePaths(
        config_root=config_root,
        settings_file=settings,
        mcp_file=settings,
        rules_dir=rules_dir,
        hooks_dir=config_root / "hooks",
        skills_dir=config_root / "skills",
        commands_dir=config_root / "commands",
        agents_dir=config_root / "agents",
        primary=config_root / "AGENTS.md",
        # A *relative* glob in the global config resolves against the project cwd, never against
        # the config directory, so the entry must be absolute (or `~/`-prefixed). OpenCode globs
        # only the last path segment of an absolute entry, so `**` would match nothing: the
        # global rules directory is therefore flat, behind a single `*.md` entry.
        instructions_entry=home_relative_entry(rules_dir, "*.md"),
        native_roots=(config_root,),
    )


# Antigravity splits its user-level config across two trees. `~/.gemini/config/` is the global
# customization root it scans at startup (skills, agents, plugins, mcp_config.json, hooks.json,
# and -- unlike the workspace -- a `rules/` directory that really does load). Settings stay in
# `~/.gemini/antigravity-cli/settings.json`, which is a different tree entirely.
def _antigravity_global() -> ScopePaths:
    """Resolve Antigravity's user-level config surfaces."""
    gemini = _home() / ".gemini"
    config_root = gemini / "config"
    return ScopePaths(
        config_root=config_root,
        settings_file=gemini / "antigravity-cli" / "settings.json",
        mcp_file=config_root / "mcp_config.json",
        rules_dir=config_root / "rules",
        hooks_dir=config_root / "hooks",
        skills_dir=config_root / "skills",
        commands_dir=None,
        agents_dir=config_root / "agents",
        primary=config_root / "GEMINI.md",
        instructions_entry=None,
        native_roots=(config_root,),
    )


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
    native_names=(".claude", ".mcp.json"),
    global_factory=_claude_global,
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
    native_names=(".opencode", "opencode.json"),
    global_factory=_opencode_global,
)

# Verified against a live `agy`: `.agents/rules/*.md` is never loaded (flat or nested, bare or
# inside a plugin), and neither are `@`-includes from the instructions file, so `rules_subdir`
# is None and the adapter inlines rules into `primary_filename`. GEMINI.md rather than AGENTS.md
# because AGENTS.md is also OpenCode's primary file: deploying both platforms into one workspace
# would otherwise inject every rule twice, and clobber a file users commonly hand-author.
# The *global* root behaves differently: `~/.gemini/config/rules/*.md` does load, provided each
# file carries `trigger: always_on` frontmatter (a bare file is silently skipped).
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
    # Overrides the default second entry: `mcp_file` sits inside `config_root` here, and
    # `GEMINI.md` is generated because it carries the rules.
    native_names=(".agents", "GEMINI.md"),
    global_factory=_antigravity_global,
)


def require_workspace_root(scope: Scope) -> Path:
    """Return the scope's workspace root or raise if it is unset."""
    if scope.workspace_root is None:
        raise ValueError("workspace_root must be set on the scope")
    return scope.workspace_root


def opencode_instructions_glob_op(paths: ScopePaths) -> FileOperation:
    """Build the MERGE op that registers OpenCode's rules glob in its settings file.

    OpenCode loads instructions from globs rather than per-file, so every rule write is
    accompanied by this idempotent registration. Emitted both when deploying rules
    (`boff.adapters.opencode`) and when materializing a migrated bundle (`boff.context.opencode`).

    Every rule emits an identical op, which matters: ``json_deep_merge`` replaces lists
    wholesale, so entries that differed per rule would clobber one another, last write winning.
    """
    return FileOperation(
        target=paths.require_settings(OPENCODE_LAYOUT.name),
        content=dumps_json({"instructions": [paths.instructions_entry]}),
        merge=MergeStrategy.MERGE,
        description="opencode instructions glob",
    )
