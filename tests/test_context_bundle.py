from pathlib import Path

from boff.context.bundle import read_bundle, sanitize_text, write_bundle
from boff.context.ir import (
    ContextBundle,
    HandoffDigest,
    InstructionsDoc,
    MemoryDoc,
    Plan,
    ProjectIdentity,
    SessionRecord,
)


def _bundle() -> ContextBundle:
    return ContextBundle(
        platform="claude",
        project=ProjectIdentity(abs_path="/home/x/proj", git_root_commit="abc", branch="main"),
        instructions=[InstructionsDoc(relative_path="CLAUDE.md", content="rules", kind="primary")],
        plans=[Plan(name="foo", content="# plan", origin_path="/x/foo.md")],
        memory=[MemoryDoc(relative_path="MEMORY.md", content="idx")],
        sessions=[SessionRecord(relative_path="ses-1.jsonl", content='{"a":1}')],
        summary=HandoffDigest(text="handoff"),
    )


def test_round_trip_preserves_bundle(tmp_path: Path) -> None:
    out = tmp_path / "b.tar.gz"
    write_bundle(_bundle(), out)
    restored = read_bundle(out)

    assert restored.platform == "claude"
    assert restored.project.abs_path == "/home/x/proj"
    assert restored.project.git_root_commit == "abc"
    assert restored.instructions[0].relative_path == "CLAUDE.md"
    assert restored.instructions[0].kind == "primary"
    assert restored.plans[0].name == "foo"
    assert restored.memory[0].content == "idx"
    assert restored.sessions[0].relative_path == "ses-1.jsonl"
    assert restored.summary is not None
    assert restored.summary.text == "handoff"


def test_sanitize_redacts_secrets() -> None:
    text = "token=abcdef0123456789 and key sk-ABCDEF0123456789xyz ok"
    cleaned = sanitize_text(text)
    assert "abcdef0123456789" not in cleaned
    assert "sk-ABCDEF0123456789xyz" not in cleaned
    assert "[REDACTED]" in cleaned


def test_sanitized_bundle_drops_abs_path(tmp_path: Path) -> None:
    out = tmp_path / "b.tar.gz"
    write_bundle(_bundle(), out, sanitize=True)
    restored = read_bundle(out)
    assert not restored.project.abs_path
