"""LocalSource: copy a local file tree into the deployment workspace."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from boff.errors import BoffError
from boff.sources.base import PluginSource, PluginSpec, installs_for
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ScopeKind

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalSpec:
    """Install spec for the ``local`` source: a single path on disk."""

    source: str
    path: Path


class LocalSource(PluginSource):
    """Mirror a local subtree, file-by-file, into the workspace root."""

    name: ClassVar[str] = "local"
    spec_class: ClassVar[type[PluginSpec]] = LocalSpec

    def _mirror(self, spec: LocalSpec, *, scope: Scope) -> list[Operation]:
        """Mirror the local subtree file-by-file into the workspace root.

        Workspace-only: the spec names a subtree to copy verbatim to the deploy root, and no
        user-level directory is an equivalent target. Mirroring an arbitrary tree into the home
        directory would write wherever the bundle author happened to point it.
        """
        if scope.kind is ScopeKind.GLOBAL:
            # Warn and skip rather than abort, so one bundle stays deployable in both scopes:
            # a manifest that mixes user-level rules with a workspace plugin is the normal case.
            _log.warning(
                "plugin source 'local' (%s) has no user-level target and is not deployed; "
                "mirroring an arbitrary subtree into the home directory would write wherever "
                "the bundle points it. Deploy this plugin to a workspace instead.",
                spec.path,
            )
            return []
        if scope.workspace_root is None:
            raise ValueError("local source requires workspace_root to be set on the scope")
        root = spec.path
        if not root.is_dir():
            raise ValueError(f"local source path is not a directory: {root}")
        ops: list[Operation] = []
        for src in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = src.relative_to(root)
            target = scope.workspace_root / rel
            try:
                content = src.read_bytes()
            except OSError as exc:
                raise BoffError(f"cannot read local source file {src}: {exc}") from exc
            ops.append(
                FileOperation(
                    target=target,
                    content=content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"local source {rel.as_posix()}",
                )
            )
        return ops

    @installs_for("claude")
    def _claude(self, spec: LocalSpec, *, scope: Scope) -> list[Operation]:
        """Install local plugin files for Claude."""
        return self._mirror(spec, scope=scope)

    @installs_for("opencode")
    def _opencode(self, spec: LocalSpec, *, scope: Scope) -> list[Operation]:
        """Install local plugin files for OpenCode."""
        return self._mirror(spec, scope=scope)

    @installs_for("antigravity")
    def _antigravity(self, spec: LocalSpec, *, scope: Scope) -> list[Operation]:
        """Install local plugin files for Antigravity."""
        return self._mirror(spec, scope=scope)
