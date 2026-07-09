import json
from pathlib import Path

import pytest

from boff.cli import main
from boff.context.claude import encode_project_dir


def test_export_then_import_rekeys_across_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("HOME", str(home))

    (source / "CLAUDE.md").write_text("# rules")
    session_dir = home / ".claude" / "projects" / encode_project_dir(str(source.resolve()))
    session_dir.mkdir(parents=True)
    transcript = {"type": "user", "cwd": str(source.resolve()), "message": {"content": "go"}}
    (session_dir / "ses-1.jsonl").write_text(json.dumps(transcript) + "\n")

    out = tmp_path / "bundle.tar.gz"
    rc = main(
        [
            "context",
            "export",
            "--platform",
            "claude",
            "--full",
            "--root",
            str(source),
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()

    # Import into a different project path on the same (faked) machine.
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["context", "import", str(out), "--into", str(dest)]) == 0

    assert (dest / "CLAUDE.md").read_text() == "# rules"
    dest_session = home / ".claude" / "projects" / encode_project_dir(str(dest.resolve()))
    restored = (dest_session / "ses-1.jsonl").read_text()
    assert str(dest.resolve()) in restored
    assert str(source.resolve()) not in restored


def test_migrate_dry_run_emits_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# rules")

    rc = main(
        [
            "context",
            "migrate",
            "--from",
            "claude",
            "--to",
            "opencode",
            "--into",
            str(project),
            "--dry-run",
        ]
    )
    assert rc == 0
    # Dry run does not write to the target platform.
    assert not (project / "AGENTS.md").exists()


def test_migrate_apply_writes_target_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# rules")

    rc = main(
        [
            "context",
            "migrate",
            "--from",
            "claude",
            "--to",
            "opencode",
            "--into",
            str(project),
        ]
    )
    assert rc == 0
    # A real apply materializes the target platform's primary file and the handoff rule.
    assert (project / "AGENTS.md").read_text() == "# rules"
    rules = list((project / ".opencode" / "rules").rglob("migrated-context.md"))
    assert len(rules) == 1
