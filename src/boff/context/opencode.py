"""OpenCode context provider.

OpenCode keys a project by its git root-commit hash, which is stable across
clones and machines, so its stored sessions are portable by key. Context lives
in ``AGENTS.md`` and ``.opencode/``; plans are markdown under ``.opencode/plans/``.
Raw sessions move through the official ``opencode export`` / ``opencode import``
commands to avoid coupling to OpenCode's internal storage format.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from boff.context.base import (
    ContextProvider,
    project_identity,
    read_lenient,
    workspace_root,
)
from boff.context.digest import DigestFacts, render_digest
from boff.context.ir import (
    ContextBundle,
    HandoffDigest,
    InstructionsDoc,
    Plan,
    SessionRecord,
)
from boff.platform_layout import (
    OPENCODE_LAYOUT,
    PlatformLayout,
    opencode_instructions_glob_op,
)
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ShellAction

_MAX_TITLES = 6
_IMPORT_STAGE = ".opencode/.boff-import"


class OpenCodeContextProvider(ContextProvider):
    """Collect and materialize OpenCode's project context and plans."""

    name: ClassVar[str] = "opencode"
    layout: ClassVar[PlatformLayout] = OPENCODE_LAYOUT
    supports_memory: ClassVar[bool] = False

    def _storage_dir(self) -> Path:
        """Resolve the OpenCode storage directory."""
        return Path.home() / ".local" / "share" / "opencode" / "storage"

    def collect(self, *, scope: Scope, full: bool = False) -> ContextBundle:
        """Read OpenCode's project state into a neutral bundle."""
        root = workspace_root(scope)
        identity = project_identity(root)

        instructions = self._collect_instructions(root)
        plans = self._collect_plans(root)

        facts = DigestFacts(
            project_path=identity.abs_path,
            branch=identity.branch,
            user_goals=self._session_titles(identity.git_root_commit),
            plans=[plan.name for plan in plans],
        )

        sessions: list[SessionRecord] = []
        if full:
            sessions = self._export_sessions(identity.git_root_commit)

        return ContextBundle(
            platform=self.name,
            project=identity,
            instructions=instructions,
            plans=plans,
            sessions=sessions,
            summary=HandoffDigest(text=render_digest(facts)),
        )

    def _config_docs(self, root: Path) -> list[InstructionsDoc]:
        """Collect OpenCode's ``opencode.json`` config alongside the instructions."""
        config = self.layout.settings_path(root)
        if not config.is_file():
            return []
        return [
            InstructionsDoc(
                relative_path=str(config.relative_to(root)),
                content=read_lenient(config),
                kind="config",
            )
        ]

    def _collect_plans(self, root: Path) -> list[Plan]:
        """Collect plan files from ``.opencode/plans``."""
        plans_dir = root / ".opencode" / "plans"
        plans: list[Plan] = []
        for path in sorted(plans_dir.glob("*.md")):
            plans.append(
                Plan(
                    name=path.stem,
                    content=read_lenient(path),
                    origin_path=str(path),
                )
            )
        return plans

    def _session_titles(self, project_id: str | None) -> list[str]:
        """Retrieve session titles for an OpenCode project."""
        if not project_id:
            return []
        session_dir = self._storage_dir() / "session" / project_id
        if not session_dir.is_dir():
            return []
        records: list[tuple[int, str]] = []
        for path in session_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            title = data.get("title") or data.get("slug")
            updated = data.get("time", {}).get("updated", 0)
            if isinstance(title, str):
                records.append((updated, title))
        records.sort(reverse=True)
        return [title for _, title in records[:_MAX_TITLES]]

    def _export_sessions(self, project_id: str | None) -> list[SessionRecord]:
        """Export session records for an OpenCode project."""
        if not project_id or shutil.which(self.layout.binary) is None:
            return []
        session_dir = self._storage_dir() / "session" / project_id
        if not session_dir.is_dir():
            return []
        records: list[SessionRecord] = []
        for path in sorted(session_dir.glob("*.json")):
            session_id = path.stem
            try:
                result = subprocess.run(
                    [self.layout.binary, "export", session_id],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
            records.append(SessionRecord(relative_path=f"{session_id}.json", content=result.stdout))
        return records

    def materialize(self, bundle: ContextBundle, *, scope: Scope) -> list[Operation]:
        """Plan operations to write ``bundle`` onto this machine's OpenCode state."""
        root = workspace_root(scope)
        target_abs = str(root.resolve())
        source_abs = bundle.project.abs_path

        rules_subdir = self.layout.rules_subdir or ""
        ops: list[Operation] = []
        has_rules = False
        for doc in bundle.instructions:
            if rules_subdir and doc.relative_path.startswith(rules_subdir):
                has_rules = True
            ops.append(self._instruction_op(doc, root))
        if has_rules:
            ops.append(opencode_instructions_glob_op(root))
        for plan in bundle.plans:
            ops.append(
                FileOperation(
                    target=root / ".opencode" / "plans" / f"{plan.name}.md",
                    content=plan.content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"opencode plan {plan.name}",
                )
            )
        for session in bundle.sessions:
            content = self._rewrite_session(session.content, source_abs, target_abs)
            stage = root / _IMPORT_STAGE / session.relative_path
            ops.append(
                FileOperation(
                    target=stage,
                    content=content,
                    merge=MergeStrategy.OVERWRITE,
                    description=f"opencode staged session {session.relative_path}",
                )
            )
            ops.append(
                ShellAction(
                    argv=[self.layout.binary, "import", str(stage)],
                    description=f"opencode import {session.relative_path}",
                )
            )
        return ops
