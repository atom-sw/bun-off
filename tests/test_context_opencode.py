from collections.abc import Callable
from pathlib import Path

import pytest

from boff.context.ir import (
    ContextBundle,
    InstructionsDoc,
    Plan,
    ProjectIdentity,
)
from boff.context.opencode import OpenCodeContextProvider
from boff.types import FileOperation, Scope


def test_collect_gathers_agents_config_rules_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_scope: Callable[[Path], Scope]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("# agents")
    (project / "opencode.json").write_text('{"model": "x"}')
    rule = project / ".opencode" / "rules" / "style.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("rule body")
    plan = project / ".opencode" / "plans" / "foo.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# plan")

    bundle = OpenCodeContextProvider().collect(scope=make_scope(project), full=False)

    kinds = {doc.relative_path: doc.kind for doc in bundle.instructions}
    assert kinds["AGENTS.md"] == "primary"
    assert kinds["opencode.json"] == "config"
    assert kinds[".opencode/rules/style.md"] == "rule"
    assert [plan.name for plan in bundle.plans] == ["foo"]


def test_materialize_writes_files_and_glob(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    bundle = ContextBundle(
        platform="opencode",
        project=ProjectIdentity(abs_path="/src/proj"),
        instructions=[
            InstructionsDoc(relative_path="AGENTS.md", content="agents", kind="primary"),
            InstructionsDoc(relative_path=".opencode/rules/style.md", content="rule", kind="rule"),
        ],
        plans=[Plan(name="foo", content="# plan")],
    )
    root = tmp_path / "proj"
    root.mkdir()
    ops = OpenCodeContextProvider().materialize(bundle, scope=make_scope(root))

    targets = {op.target for op in ops if isinstance(op, FileOperation)}
    assert root / "AGENTS.md" in targets
    assert root / ".opencode" / "rules" / "style.md" in targets
    assert root / ".opencode" / "plans" / "foo.md" in targets
    assert root / "opencode.json" in targets  # instructions glob emitted for rules
