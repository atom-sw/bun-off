"""Foundation types: ops emitted by adapters/sources, scope, and enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class Named(Protocol):
    """Anything the manifest merges by name: every artifact type plus ``Plugin``.

    ``name`` is a read-only property so that frozen dataclasses satisfy the protocol.
    """

    @property
    def name(self) -> str: ...


class MergeStrategy(StrEnum):
    """How a :class:`FileOperation` combines with any existing file at its target."""

    OVERWRITE = "overwrite"
    MERGE = "merge"


class ScopeKind(StrEnum):
    """Whether a deploy targets the current workspace or the user's global config."""

    WORKSPACE = "workspace"
    GLOBAL = "global"


class HookPhase(StrEnum):
    """Which deploy-lifecycle phase a hook runs in: before or after applying ops."""

    PRE_INSTALL = "pre_install"
    POST_INSTALL = "post_install"


@dataclass(frozen=True)
class Scope:
    """The deploy target: a scope kind plus the workspace root it applies to."""

    kind: ScopeKind
    workspace_root: Path | None = None


@dataclass(frozen=True)
class FileOperation:
    """Write ``content`` to ``target``, combining with any existing file per ``merge``."""

    target: Path
    content: str | bytes
    merge: MergeStrategy = MergeStrategy.OVERWRITE
    description: str = ""


@dataclass(frozen=True)
class ShellAction:
    """Run ``argv`` as a subprocess in ``cwd`` with ``env`` overlaid on the environment."""

    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict[str, str])
    description: str = ""


@dataclass(frozen=True)
class DeleteOperation:
    """Remove the file or tree at ``target``.

    After removal, empty parent directories are pruned upward, stopping below
    ``prune_until`` (which is never itself removed). When ``prune_until`` is None,
    parent directories are left in place.
    """

    target: Path
    description: str = ""
    prune_until: Path | None = None


@dataclass(frozen=True)
class PruneKeysOperation:
    """Remove ``key_paths`` from the JSON file at ``target``, preserving other keys."""

    target: Path
    key_paths: list[tuple[str, ...]]
    description: str = ""


type Operation = FileOperation | ShellAction | DeleteOperation | PruneKeysOperation
