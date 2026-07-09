"""ContextProvider base class and shared project-identity helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

from boff.context.ir import ContextBundle, InstructionsDoc, ProjectIdentity
from boff.platform_layout import PlatformLayout
from boff.platform_layout import require_workspace_root as workspace_root
from boff.types import FileOperation, MergeStrategy, Operation, Scope

__all__ = ["ContextProvider", "project_identity", "read_lenient", "workspace_root"]


def _git(root: Path, *args: str) -> str | None:
    """Run a read-only git command in ``root``: return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    out = result.stdout.strip()
    return out or None


def project_identity(root: Path) -> ProjectIdentity:
    """Derive a :class:`ProjectIdentity` from the project root on disk."""
    return ProjectIdentity(
        abs_path=str(root.resolve()),
        git_root_commit=_git(root, "rev-list", "--max-parents=0", "HEAD"),
        remote=_git(root, "config", "--get", "remote.origin.url"),
        branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
    )


def read_lenient(path: Path) -> str:
    """Read a text file as UTF-8, replacing any undecodable bytes."""
    return path.read_text(encoding="utf-8", errors="replace")


class ContextProvider:
    """Base class for per-platform context providers.

    A provider reads a project's context and plans off disk (:meth:`collect`)
    and emits operations to write them back (:meth:`materialize`). Subclasses
    register themselves in :mod:`boff.context` and set :attr:`layout`.
    """

    name: ClassVar[str] = ""
    layout: ClassVar[PlatformLayout]
    supports_memory: ClassVar[bool] = False

    @property
    def primary_filename(self) -> str:
        """The instructions file this provider reads and writes.

        Defaults to the platform's primary file. A provider overrides it when the *adapter*
        already owns that file, so that deploy and context never write the same path: a deploy
        would otherwise discard migrated context that only the bundle carries.
        """
        return self.layout.primary_filename

    def collect(self, *, scope: Scope, full: bool = False) -> ContextBundle:
        """Read this platform's context for the project into a neutral bundle."""
        del scope, full
        raise NotImplementedError

    def materialize(self, bundle: ContextBundle, *, scope: Scope) -> list[Operation]:
        """Plan the operations needed to write ``bundle`` onto this platform."""
        del bundle, scope
        raise NotImplementedError

    def _collect_instructions(self, root: Path) -> list[InstructionsDoc]:
        """Collect the primary instructions file, any config docs, and every rule under the rules dir."""
        docs: list[InstructionsDoc] = []
        primary = root / self.primary_filename
        if primary.is_file():
            docs.append(
                InstructionsDoc(
                    relative_path=self.primary_filename,
                    content=read_lenient(primary),
                    kind="primary",
                )
            )
        docs.extend(self._config_docs(root))
        if self.layout.rules_subdir is None:
            # The platform has no rules directory: its rules live inside the primary file.
            return docs
        rules_dir = root / self.layout.rules_subdir
        for path in sorted(rules_dir.rglob("*.md")):
            docs.append(
                InstructionsDoc(
                    relative_path=str(path.relative_to(root)),
                    content=read_lenient(path),
                    kind="rule",
                )
            )
        return docs

    def _config_docs(self, root: Path) -> list[InstructionsDoc]:
        """Return platform-specific config docs to collect between the primary file and rules."""
        del root
        return []

    def _instruction_op(self, doc: InstructionsDoc, root: Path) -> FileOperation:
        """Build the op that writes one collected instructions doc back to disk."""
        return FileOperation(
            target=root / doc.relative_path,
            content=doc.content,
            merge=MergeStrategy.OVERWRITE,
            description=f"{self.name} instructions {doc.relative_path}",
        )

    @staticmethod
    def _rewrite_session(content: str, source_abs: str, target_abs: str) -> str:
        """Re-key a session transcript from the source project path to the target path."""
        if source_abs and source_abs != target_abs:
            return content.replace(source_abs, target_abs)
        return content
