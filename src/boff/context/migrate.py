"""Cross-platform context handoff.

:func:`migrate` reshapes a bundle collected from one platform so the target
platform's provider can materialize it: the primary instructions file is
renamed (CLAUDE.md <> AGENTS.md), rules are re-rooted, plans are carried over,
and anything the target cannot store natively (memory, prior conversation) is
folded into a single auto-loaded handoff document.
"""

from __future__ import annotations

from pathlib import Path

from boff.context.base import ContextProvider
from boff.context.ir import ContextBundle, HandoffDigest, InstructionsDoc, Plan

_HANDOFF_RELATIVE = "handoff/migrated-context.md"


def _remap_rule_path(relative_path: str, target_rules_subdir: str) -> str:
    """Remap a rule path to the target rules subdirectory."""
    marker = "rules/"
    idx = relative_path.find(marker)
    tail = relative_path[idx + len(marker) :] if idx != -1 else relative_path
    return f"{target_rules_subdir}/{tail}"


def _handoff_text(source: ContextBundle, target: ContextProvider) -> str:
    """Generate context handoff text between providers."""
    parts: list[str] = []
    if source.summary is not None:
        parts.append(source.summary.text)
    if source.memory and not target.supports_memory:
        parts.append(f"## Memory carried over from {source.platform}")
        for mem in source.memory:
            parts.append(f"### {mem.relative_path}\n\n{mem.content.strip()}")
    return "\n\n".join(parts).strip() + "\n"


def _inlined_primary(
    primary: str | None, rules: list[InstructionsDoc], handoff: str, target: ContextProvider
) -> list[InstructionsDoc]:
    """Fold rules and the handoff into one primary doc, for targets with no rules directory."""
    parts = [primary.strip()] if primary and primary.strip() else []
    parts.extend(f"## {Path(doc.relative_path).stem}\n\n{doc.content.strip()}" for doc in rules)
    parts.append(f"## Migrated context\n\n{handoff.strip()}")
    return [
        InstructionsDoc(
            relative_path=target.primary_filename,
            content="\n\n".join(parts) + "\n",
            kind="primary",
        )
    ]


def migrate(source: ContextBundle, target: ContextProvider) -> ContextBundle:
    """Return a bundle shaped for ``target`` from ``source``'s collected context."""
    handoff = _handoff_text(source, target)
    rules_subdir = target.layout.rules_subdir

    primary: str | None = None
    rule_docs: list[InstructionsDoc] = []
    for doc in source.instructions:
        if doc.kind == "primary":
            primary = doc.content
        elif doc.kind == "rule":
            rule_docs.append(doc)
        # kind == "config" and any other platform-specific docs are dropped.

    instructions: list[InstructionsDoc] = []
    if rules_subdir is None:
        # The target loads rules only from its primary file: inline them rather than write
        # files it would never read.
        instructions = _inlined_primary(primary, rule_docs, handoff, target)
    else:
        if primary is not None:
            instructions.append(
                InstructionsDoc(
                    relative_path=target.primary_filename, content=primary, kind="primary"
                )
            )
        instructions.extend(
            InstructionsDoc(
                relative_path=_remap_rule_path(doc.relative_path, rules_subdir),
                content=doc.content,
                kind="rule",
            )
            for doc in rule_docs
        )
        instructions.append(
            InstructionsDoc(
                relative_path=f"{rules_subdir}/{_HANDOFF_RELATIVE}",
                content=handoff,
                kind="rule",
            )
        )

    plans = [Plan(name=plan.name, content=plan.content, origin_path="") for plan in source.plans]

    return ContextBundle(
        platform=target.name,
        project=source.project,
        instructions=instructions,
        plans=plans,
        summary=HandoffDigest(text=handoff),
    )
