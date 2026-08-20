"""PlatformAdapter base class, ``@renders`` dispatch, and layout-driven shared renderers.

The renderers whose only per-platform difference is the on-disk layout and the platform name
live here (`_skill`, `_slash_command`, `_settings`, `_mcp_server`), driven by each subclass's
:attr:`PlatformAdapter.layout`. Subclasses keep only the genuinely divergent renderers.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, ClassVar, Protocol

import yaml

from boff.artifacts import SKILL_FILENAME, EventHook, MCPServer, Settings, Skill, SlashCommand
from boff.errors import BoffError
from boff.jsonutil import dumps_json
from boff.platform_layout import PlatformLayout, ScopePaths
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ScopeKind

_RENDERS_ATTR = "_renders_for"
_DROPS_ATTR = "_drops_artifact"


class RenderMethod[A](Protocol):
    """Signature of a ``@renders`` method: the unbound renderer for one artifact type.

    ``adapter`` and ``artifact`` are positional-only so that a method's ``self`` parameter
    matches. Typing the decorator against this protocol makes the checker verify that a
    renderer's artifact parameter agrees with the type it claims to render.
    """

    def __call__(
        self, adapter: Any, artifact: A, /, *, platform: str, scope: Scope
    ) -> list[Operation]: ...


def renders[A](
    artifact_type: type[A], *, drops: bool | Collection[ScopeKind] = False
) -> Callable[[RenderMethod[A]], RenderMethod[A]]:
    """Mark an adapter method as handling a single artifact type.

    ``drops`` declares that the renderer exists only to warn: the platform has no surface for
    this artifact and the method emits nothing. :meth:`PlatformAdapter.supports` stays True, so
    deploy behaviour is unchanged. ``boff check`` reads the flag to tell a deliberate drop apart
    from a renderer that simply had nothing to emit for this manifest.

    Pass ``True`` to drop in every scope, or a collection of :class:`ScopeKind` to drop only in
    those. A surface can exist in one scope and not the other in either direction: Antigravity's
    settings have only a user-level home, while Claude's MCP config has only a workspace one.
    """
    kinds = frozenset(ScopeKind) if drops is True else frozenset(drops or ())

    def decorator(func: RenderMethod[A]) -> RenderMethod[A]:
        setattr(func, _RENDERS_ATTR, artifact_type)
        setattr(func, _DROPS_ATTR, kinds)
        return func

    return decorator


def require_rules_dir(paths: ScopePaths, platform: str) -> Path:
    """Return the scope's rules directory, or raise if the platform reads none."""
    if paths.rules_dir is None:
        raise BoffError(f"platform '{platform}' has no rules directory in this scope")
    return paths.rules_dir


def frontmatter_block(data: dict[str, object]) -> str:
    """Render an ordered mapping as a YAML frontmatter block."""
    body = yaml.safe_dump(data, sort_keys=False).strip()
    return f"---\n{body}\n---\n\n"


class PlatformAdapter:
    """Base class for platform adapters.

    Subclasses set :attr:`name` and :attr:`layout` and declare per-artifact rendering methods
    decorated with ``@renders(<ArtifactType>)``. ``__init_subclass__`` collects them (including
    the shared renderers defined here) into ``_renderers`` so ``render`` can dispatch on type.
    """

    name: ClassVar[str] = ""
    layout: ClassVar[PlatformLayout]
    _renderers: ClassVar[dict[type[Any], RenderMethod[Any]]] = {}
    _drops: ClassVar[dict[type[Any], frozenset[ScopeKind]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        renderers: dict[type[Any], RenderMethod[Any]] = {}
        drops: dict[type[Any], frozenset[ScopeKind]] = {}
        # Base to derived, so a subclass renderer (and its drops flag) overrides an inherited one.
        for klass in reversed(cls.__mro__):
            for value in vars(klass).values():
                artifact_type = getattr(value, _RENDERS_ATTR, None)
                if artifact_type is not None:
                    renderers[artifact_type] = value
                    drops[artifact_type] = getattr(value, _DROPS_ATTR, frozenset())
        cls._renderers = renderers
        cls._drops = drops

    def supports(self, artifact_type: type[Any]) -> bool:
        """Return True if this adapter has a renderer for ``artifact_type``."""
        return artifact_type in type(self)._renderers

    def drops(self, artifact_type: type[Any], scope: Scope) -> bool:
        """Return True if the renderer for ``artifact_type`` only warns and skips in ``scope``."""
        return scope.kind in type(self)._drops.get(artifact_type, frozenset())

    def render(self, artifact: Any, *, platform: str, scope: Scope) -> list[Operation]:
        """Dispatch ``artifact`` to the appropriate ``@renders`` method."""
        try:
            renderer = type(self)._renderers[type(artifact)]
        except KeyError as exc:
            raise KeyError(
                f"adapter '{self.name}' cannot render artifact type '{type(artifact).__name__}'"
            ) from exc
        return renderer(self, artifact, platform=platform, scope=scope)

    def native_roots(self, scope: Scope) -> list[Path]:
        """Return this platform's native config files/directories, for ``--wipe``."""
        return list(self.layout.paths(scope).native_roots)

    def _hook_script_ops(
        self, hooks: tuple[EventHook, ...], hooks_dir: Path
    ) -> list[FileOperation]:
        """Write each event hook's script to the platform's hooks directory."""
        return [
            FileOperation(
                target=hooks_dir / hook.name,
                content=hook.script_content or "",
                merge=MergeStrategy.OVERWRITE,
                description=f"{self.name} event hook {hook.name}",
            )
            for hook in hooks
        ]

    @renders(Skill)
    def _skill(self, artifact: Skill, *, platform: str, scope: Scope) -> list[Operation]:
        """Write a skill, and any supporting files, under ``<config_root>/skills/<name>/``."""
        del platform
        # Every platform discovers skills as <name>/SKILL.md; a flat <name>.md is not loaded.
        skill_root = self.layout.paths(scope).skills_dir / artifact.name
        ops: list[Operation] = [
            FileOperation(
                target=skill_root / SKILL_FILENAME,
                content=artifact.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"{self.name} skill {artifact.name}",
            )
        ]
        # Supporting files land beside SKILL.md so its relative links resolve as authored.
        ops.extend(
            FileOperation(
                target=skill_root / extra.path,
                content=extra.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"{self.name} skill {artifact.name}/{extra.path}",
            )
            for extra in artifact.files
        )
        return ops

    @renders(SlashCommand)
    def _slash_command(
        self, artifact: SlashCommand, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Write a slash command to ``<config_root>/commands/<name>.md``."""
        del platform
        commands_dir = self.layout.paths(scope).commands_dir
        if commands_dir is None:
            raise BoffError(f"platform '{self.name}' has no commands directory in this scope")
        target = commands_dir / f"{artifact.name}.md"
        return [
            FileOperation(
                target=target,
                content=artifact.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"{self.name} slash command {artifact.name}",
            )
        ]

    @renders(MCPServer)
    def _mcp_server(self, artifact: MCPServer, *, platform: str, scope: Scope) -> list[Operation]:
        """Merge the server's raw config into the platform's MCP file under its MCP key."""
        del platform
        if self.name not in artifact.raw:
            raise ValueError(
                f"MCPServer '{artifact.name}' has no '{self.name}' entry in raw config"
            )
        payload = {self.layout.mcp_key: {artifact.name: artifact.raw[self.name]}}
        return [
            FileOperation(
                target=self.layout.paths(scope).require_mcp(self.name),
                content=dumps_json(payload),
                merge=MergeStrategy.MERGE,
                description=f"{self.name} mcp server {artifact.name}",
            )
        ]

    @renders(Settings)
    def _settings(self, artifact: Settings, *, platform: str, scope: Scope) -> list[Operation]:
        """Merge the verbatim settings block into the platform's settings file."""
        del platform
        block = artifact.raw_for(self.name)
        if not block:
            return []
        return [
            FileOperation(
                target=self.layout.paths(scope).require_settings(self.name),
                content=dumps_json(block),
                merge=MergeStrategy.MERGE,
                description=f"{self.name} settings",
            )
        ]
