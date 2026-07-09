from collections.abc import Callable
from pathlib import Path

from boff.context.antigravity import AntigravityContextProvider
from boff.context.ir import ContextBundle, InstructionsDoc, ProjectIdentity
from boff.types import FileOperation, Scope

# The adapter owns GEMINI.md (the layout's primary); the context provider owns AGENTS.md.
GENERATED = "GEMINI.md"
PRIMARY = "AGENTS.md"


def test_context_primary_differs_from_the_file_the_adapter_owns() -> None:
    provider = AntigravityContextProvider()
    assert provider.primary_filename == PRIMARY
    assert provider.layout.primary_filename == GENERATED


def test_collect_treats_agents_md_as_primary_and_the_generated_gemini_md_as_config(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    primary_body, generated_body = "# hand-authored", "# boff rules"
    (project / PRIMARY).write_text(primary_body)
    (project / GENERATED).write_text(generated_body)

    bundle = AntigravityContextProvider().collect(scope=make_scope(project))

    docs = {doc.relative_path: doc for doc in bundle.instructions}
    assert docs[PRIMARY].kind == "primary"
    assert docs[PRIMARY].content == primary_body
    # GEMINI.md is deploy-generated: it rides along in a bundle but `migrate` drops config docs.
    assert docs[GENERATED].kind == "config"
    assert docs[GENERATED].content == generated_body


def test_collect_ignores_an_inert_rules_directory(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / PRIMARY).write_text("body")
    rule = project / ".agents" / "rules" / "style.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("agy never reads this")

    bundle = AntigravityContextProvider().collect(scope=make_scope(project))

    assert all(doc.kind != "rule" for doc in bundle.instructions)


def test_collect_never_gathers_sessions_even_when_full(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    bundle = AntigravityContextProvider().collect(scope=make_scope(project), full=True)
    assert bundle.sessions == []
    assert bundle.plans == []


def test_materialize_writes_each_instruction_doc(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    bundle = ContextBundle(
        platform="antigravity",
        project=ProjectIdentity(abs_path="/src/proj"),
        instructions=[InstructionsDoc(relative_path=PRIMARY, content="body", kind="primary")],
    )
    root = tmp_path / "proj"
    root.mkdir()

    ops = AntigravityContextProvider().materialize(bundle, scope=make_scope(root))

    assert {op.target for op in ops if isinstance(op, FileOperation)} == {root / PRIMARY}
