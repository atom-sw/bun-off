"""Antigravity CLI context provider.

Antigravity keys a project by an opaque id in a machine-local registry, and stores each
conversation in a SQLite database under ``~/.gemini/antigravity-cli/`` with no documented
export command. Sessions are therefore not portable and never collected: this provider carries
the instructions layer only.

Antigravity loads *and merges* two instructions files, which lets deploy and context each own one:

- ``GEMINI.md`` is the adapter's (:attr:`PlatformLayout.primary_filename`). ``boff deploy``
  regenerates it from the manifest's rules and ``boff clean`` deletes it.
- ``AGENTS.md`` is this provider's, so a migrated handoff survives a later deploy. ``boff deploy``
  never writes it; ``migrate`` and ``import`` do, exactly as they overwrite ``CLAUDE.md`` on Claude.

``GEMINI.md`` is therefore collected as a ``config`` doc: it rides along in an export bundle and is
restored on import, but ``migrate`` drops it, since its content is boff-generated and re-derivable
by deploying the manifest rather than portable context.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from boff.context.base import (
    ContextProvider,
    project_identity,
    read_lenient,
    workspace_root,
)
from boff.context.digest import DigestFacts, render_digest
from boff.context.ir import ContextBundle, HandoffDigest, InstructionsDoc
from boff.platform_layout import ANTIGRAVITY_LAYOUT, PlatformLayout
from boff.types import Operation, Scope

# The cross-tool instructions file, which the adapter never writes. See the module docstring.
_CONTEXT_INSTRUCTIONS = "AGENTS.md"


class AntigravityContextProvider(ContextProvider):
    """Collect and materialize Antigravity's project instructions."""

    name: ClassVar[str] = "antigravity"
    layout: ClassVar[PlatformLayout] = ANTIGRAVITY_LAYOUT
    supports_memory: ClassVar[bool] = False

    @property
    def primary_filename(self) -> str:
        """Use ``AGENTS.md``: the adapter owns the layout's ``GEMINI.md``."""
        return _CONTEXT_INSTRUCTIONS

    def collect(self, *, scope: Scope, full: bool = False) -> ContextBundle:
        """Read Antigravity's project instructions into a neutral bundle.

        ``full`` is accepted for interface parity: Antigravity exposes no session export, so
        there is nothing extra to collect.
        """
        del full
        root = workspace_root(scope)
        identity = project_identity(root)
        instructions = self._collect_instructions(root)
        facts = DigestFacts(
            project_path=identity.abs_path,
            branch=identity.branch,
            user_goals=[],
            plans=[],
        )
        return ContextBundle(
            platform=self.name,
            project=identity,
            instructions=instructions,
            plans=[],
            sessions=[],
            summary=HandoffDigest(text=render_digest(facts)),
        )

    def _config_docs(self, root: Path) -> list[InstructionsDoc]:
        """Collect the deploy-generated ``GEMINI.md`` alongside the ``AGENTS.md`` primary."""
        generated = root / self.layout.primary_filename
        if not generated.is_file():
            return []
        return [
            InstructionsDoc(
                relative_path=self.layout.primary_filename,
                content=read_lenient(generated),
                kind="config",
            )
        ]

    def materialize(self, bundle: ContextBundle, *, scope: Scope) -> list[Operation]:
        """Plan operations to write ``bundle``'s instructions into this workspace."""
        root = workspace_root(scope)
        return [self._instruction_op(doc, root) for doc in bundle.instructions]
