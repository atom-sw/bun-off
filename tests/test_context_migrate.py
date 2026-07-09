from collections.abc import Callable
from pathlib import Path

import pytest

from boff.artifacts import Rule
from boff.context import get_provider, provider_names
from boff.context.ir import (
    ContextBundle,
    HandoffDigest,
    InstructionsDoc,
    MemoryDoc,
    Plan,
    ProjectIdentity,
)
from boff.context.migrate import migrate
from boff.manifest import Manifest, ManifestMeta
from boff.types import FileOperation, MergeStrategy, Operation, Scope

_HANDOFF_MARKER = "prior conversation summary"


def _claude_bundle() -> ContextBundle:
    return ContextBundle(
        platform="claude",
        project=ProjectIdentity(abs_path="/home/x/proj"),
        instructions=[
            InstructionsDoc(relative_path="CLAUDE.md", content="primary body", kind="primary"),
            InstructionsDoc(
                relative_path=".claude/rules/dev/style.md", content="rule body", kind="rule"
            ),
        ],
        plans=[Plan(name="foo", content="# plan")],
        memory=[MemoryDoc(relative_path="MEMORY.md", content="remembered fact")],
        summary=HandoffDigest(text=_HANDOFF_MARKER),
    )


def _overwrite_targets(ops: list[Operation]) -> set[Path]:
    """Return the paths ``ops`` claim wholesale. MERGE targets are shared by key, not by file."""
    return {
        op.target
        for op in ops
        if isinstance(op, FileOperation) and op.merge is MergeStrategy.OVERWRITE
    }


@pytest.mark.parametrize("platform", sorted(provider_names()))
def test_migrated_handoff_never_lands_on_a_file_deploy_overwrites(
    platform: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_scope: Callable[[Path], Scope],
    deploy_ops: Callable[[Manifest, str, Scope], list[Operation]],
) -> None:
    """Deploy and context must not contend for a file.

    Migrate legitimately rewrites rule files that deploy also owns: both derive from the same
    manifest. The handoff digest is different -- it exists only in the context bundle, so a deploy
    that overwrote its file would silently discard the migrated context and folded-in memory.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    scope = make_scope(root)

    manifest = Manifest(
        root=tmp_path,
        meta=ManifestMeta(name="t", description="d"),
        rules=(Rule(name="style", content="body"),),
    )
    deploy_targets = _overwrite_targets(deploy_ops(manifest, platform, scope))

    target = get_provider(platform)
    ops = target.materialize(migrate(_claude_bundle(), target), scope=scope)
    carrying_handoff = [
        op for op in ops if isinstance(op, FileOperation) and _HANDOFF_MARKER in str(op.content)
    ]

    assert carrying_handoff, "migrate must write the handoff digest somewhere"
    for op in carrying_handoff:
        assert op.target not in deploy_targets, (
            f"{platform}: deploy overwrites {op.target}, which carries the migrated handoff"
        )


def test_migrate_claude_to_opencode() -> None:
    target = get_provider("opencode")
    out = migrate(_claude_bundle(), target)

    assert out.platform == "opencode"
    rels = [doc.relative_path for doc in out.instructions]
    assert "AGENTS.md" in rels
    assert ".opencode/rules/dev/style.md" in rels

    handoff = next(d for d in out.instructions if d.relative_path.endswith("migrated-context.md"))
    assert handoff.relative_path.startswith(".opencode/rules/")
    # OpenCode has no memory store, so memory is folded into the handoff doc.
    assert "remembered fact" in handoff.content
    assert "prior conversation summary" in handoff.content
    assert [plan.name for plan in out.plans] == ["foo"]


def test_migrate_claude_to_antigravity_inlines_rules_into_the_primary_file() -> None:
    source = _claude_bundle()
    target = get_provider("antigravity")
    out = migrate(source, target)

    assert out.platform == target.name
    # Antigravity loads rules only from an instructions file, so nothing else is written. That
    # file is AGENTS.md, not the layout's GEMINI.md, which the adapter owns.
    assert [doc.relative_path for doc in out.instructions] == [target.primary_filename]
    assert target.primary_filename != target.layout.primary_filename

    primary = out.instructions[0].content
    rule = next(d for d in source.instructions if d.kind == "rule")
    assert next(d for d in source.instructions if d.kind == "primary").content in primary
    assert rule.content in primary
    assert "remembered fact" in primary
    assert _HANDOFF_MARKER in primary


def test_migrate_opencode_to_claude_renames_primary() -> None:
    source = ContextBundle(
        platform="opencode",
        project=ProjectIdentity(abs_path="/home/x/proj"),
        instructions=[
            InstructionsDoc(relative_path="AGENTS.md", content="body", kind="primary"),
            InstructionsDoc(relative_path="opencode.json", content="{}", kind="config"),
        ],
        summary=HandoffDigest(text="summary"),
    )
    out = migrate(source, get_provider("claude"))

    rels = [doc.relative_path for doc in out.instructions]
    assert "CLAUDE.md" in rels
    assert "opencode.json" not in rels  # config docs are dropped on migrate
    assert any(r.startswith(".claude/rules/") and r.endswith("migrated-context.md") for r in rels)
