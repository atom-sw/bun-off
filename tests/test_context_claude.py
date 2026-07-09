import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from boff.context.claude import ClaudeContextProvider, encode_project_dir
from boff.types import FileOperation, Scope


def test_encode_project_dir() -> None:
    assert encode_project_dir("/home/caf/projects/bun-off-dev") == "-home-caf-projects-bun-off-dev"
    assert encode_project_dir("/a/b_c.d") == "-a-b-c-d"


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    session_dir = home / ".claude" / "projects" / encode_project_dir(str(project.resolve()))
    session_dir.mkdir(parents=True)
    plans_dir = home / ".claude" / "plans"
    plans_dir.mkdir(parents=True)
    return project, session_dir, plans_dir


def _write_transcript(session_dir: Path, name: str, lines: list[dict[str, Any]]) -> None:
    text = "\n".join(json.dumps(line) for line in lines) + "\n"
    (session_dir / name).write_text(text)


def test_collect_gathers_instructions_plans_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_scope: Callable[[Path], Scope]
) -> None:
    project, session_dir, plans_dir = _setup(tmp_path, monkeypatch)
    (project / "CLAUDE.md").write_text("# project rules")
    rule = project / ".claude" / "rules" / "dev" / "style.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("style rule")
    (session_dir / "memory").mkdir()
    (session_dir / "memory" / "MEMORY.md").write_text("# index")
    (plans_dir / "foo.md").write_text("# the plan")

    plan_path = f"{plans_dir}/foo.md"
    project_file = f"{project.resolve()}/src/x.py"
    _write_transcript(
        session_dir,
        "ses-1.jsonl",
        [
            {"type": "user", "message": {"content": "Build the X feature"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": plan_path}}
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": project_file}}
                    ]
                },
            },
        ],
    )

    bundle = ClaudeContextProvider().collect(scope=make_scope(project), full=False)

    rels = {doc.relative_path: doc.kind for doc in bundle.instructions}
    assert rels["CLAUDE.md"] == "primary"
    assert rels[".claude/rules/dev/style.md"] == "rule"
    assert [plan.name for plan in bundle.plans] == ["foo"]
    assert [mem.relative_path for mem in bundle.memory] == ["MEMORY.md"]
    assert bundle.sessions == []
    assert bundle.summary is not None
    assert "Build the X feature" in bundle.summary.text
    assert "src/x.py" in bundle.summary.text
    assert "foo" in bundle.summary.text


def test_collect_full_includes_sessions_but_not_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_scope: Callable[[Path], Scope]
) -> None:
    project, session_dir, _ = _setup(tmp_path, monkeypatch)
    _write_transcript(session_dir, "ses-1.jsonl", [{"type": "user", "message": {"content": "hi"}}])
    sub = session_dir / "ses-1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text('{"type":"user"}\n')
    (session_dir / "memory").mkdir()
    (session_dir / "memory" / "MEMORY.md").write_text("idx")

    bundle = ClaudeContextProvider().collect(scope=make_scope(project), full=True)

    rels = {s.relative_path for s in bundle.sessions}
    assert "ses-1.jsonl" in rels
    assert "ses-1/subagents/agent-1.jsonl" in rels
    assert not any(r.startswith("memory") for r in rels)


def test_materialize_rekeys_to_target_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_scope: Callable[[Path], Scope]
) -> None:
    project, session_dir, _ = _setup(tmp_path, monkeypatch)
    source_abs = f"{project.resolve()}/src/x.py"
    _write_transcript(
        session_dir,
        "ses-1.jsonl",
        [{"type": "user", "cwd": str(project.resolve()), "message": {"content": source_abs}}],
    )
    bundle = ClaudeContextProvider().collect(scope=make_scope(project), full=True)

    target = tmp_path / "dest"
    target.mkdir()
    ops = ClaudeContextProvider().materialize(bundle, scope=make_scope(target))

    target_encoded = encode_project_dir(str(target.resolve()))
    session_ops = [
        op for op in ops if isinstance(op, FileOperation) and op.target.name == "ses-1.jsonl"
    ]
    assert len(session_ops) == 1
    op = session_ops[0]
    assert target_encoded in str(op.target)
    assert isinstance(op.content, str)
    assert str(target.resolve()) in op.content
    assert str(project.resolve()) not in op.content
