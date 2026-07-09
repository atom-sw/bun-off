import json
from pathlib import Path

import pytest

from boff import verify as verify_module
from boff.cli import ExitCode, main
from tests.conftest import REMOTE_NAME, REMOTE_SUBDIR


def test_deploy_dry_run_prints_op_count(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["deploy", str(sample_manifest), "--platform", "claude", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "planned" in out
    assert "operation(s)" in out


def test_deploy_dry_run_reports_missing_hook_script(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    (manifest_dir / "boff.yaml").write_text(
        "meta:\n  name: t\n  description: d\nhooks:\n  pre_install:\n    - script: nonexistent\n"
    )
    rc = main(["deploy", str(manifest_dir), "--platform", "claude", "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "missing hook script" in err
    assert "nonexistent.py" in err


def test_deploy_applies_files(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["deploy", str(sample_manifest), "--platform", "claude"])
    capsys.readouterr()
    assert rc == 0
    assert (tmp_path / ".claude" / "rules" / "dev-essentials" / "style.md").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def test_deploy_runs_pre_and_post_install_hooks(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A real (non-dry) deploy runs the manifest's pre/post hooks to success; each prints a
    # confirmation line that run_hooks forwards to stdout.
    monkeypatch.chdir(tmp_path)
    rc = main(["deploy", str(sample_manifest), "--platform", "claude"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[pre_install]" in out
    assert "[post_install]" in out


def _make_stack(root: Path, rule_name: str, settings: dict[str, object]) -> Path:
    """Create a minimal manifest folder with one rule and a claude settings block."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "rules").mkdir(exist_ok=True)
    (root / "rules" / f"{rule_name}.md").write_text(f"# {rule_name}\n")
    (root / "boff.yaml").write_text(
        f"meta:\n  name: {rule_name}\n  description: {rule_name}\n"
        f"rules:\n  - {rule_name}\nsettings:\n  claude: {json.dumps(settings)}\n"
    )
    return root


def test_deploy_switch_cleans_orphans_and_prunes_keys(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    # A hand-authored settings key boff never wrote must survive every deploy.
    settings_file = project / ".claude" / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({"model": "opus"}))

    design = _make_stack(tmp_path / "design", "design_rule", {"outputStyle": "Explanatory"})
    maintenance = _make_stack(tmp_path / "maint", "maint_rule", {"cleanupPeriodDays": 7})

    assert main(["deploy", str(design), "--platform", "claude"]) == 0
    assert (project / ".claude" / "rules" / "design_rule.md").is_file()
    assert json.loads(settings_file.read_text())["outputStyle"] == "Explanatory"

    assert main(["deploy", str(maintenance), "--platform", "claude"]) == 0
    capsys.readouterr()

    assert not (project / ".claude" / "rules" / "design_rule.md").exists()  # orphan removed
    assert (project / ".claude" / "rules" / "maint_rule.md").is_file()
    merged = json.loads(settings_file.read_text())
    assert "outputStyle" not in merged  # boff key dropped between stacks is pruned
    assert merged["cleanupPeriodDays"] == 7
    assert merged["model"] == "opus"  # user-authored key preserved
    assert (project / ".boff" / "state.json").is_file()
    assert (project / ".boff" / ".gitignore").read_text() == "*\n"


def test_clean_removes_boff_footprint_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    design = _make_stack(tmp_path / "design", "design_rule", {"outputStyle": "Explanatory"})
    assert main(["deploy", str(design), "--platform", "claude"]) == 0

    hand = project / ".claude" / "notes.md"
    hand.write_text("mine")

    assert main(["clean"]) == 0
    assert not (project / ".claude" / "rules" / "design_rule.md").exists()
    assert hand.read_text() == "mine"  # non-boff file preserved


def test_wipe_removes_everything(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    design = _make_stack(tmp_path / "design", "design_rule", {"outputStyle": "Explanatory"})
    assert main(["deploy", str(design), "--platform", "claude"]) == 0
    (project / ".claude" / "notes.md").write_text("mine")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert main(["clean", "--wipe", "--platform", "claude"]) == 0
    assert not (project / ".claude").exists()  # whole native root removed
    assert project.is_dir()  # project root preserved


def test_wipe_aborts_without_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    design = _make_stack(tmp_path / "design", "design_rule", {"outputStyle": "Explanatory"})
    assert main(["deploy", str(design), "--platform", "claude"]) == 0
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    rc = main(["clean", "--wipe", "--platform", "claude"])
    assert rc == 1
    assert (project / ".claude" / "rules" / "design_rule.md").is_file()  # nothing deleted


def test_wipe_without_platform_is_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # --wipe targets a platform's native root, so it requires an explicit --platform.
    monkeypatch.chdir(tmp_path)
    rc = main(["clean", "--wipe"])
    err = capsys.readouterr().err
    assert rc == ExitCode.USAGE
    assert "--wipe requires --platform" in err


CLAUDE_BINARY = "/usr/bin/claude"


@pytest.fixture
def binary_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend every platform CLI is installed, so `check`'s probe never depends on the host."""

    def _which(_name: str) -> str:
        return CLAUDE_BINARY

    monkeypatch.setattr(verify_module.shutil, "which", _which)


@pytest.fixture
def binary_off_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend no platform CLI is installed."""

    def _which(_name: str) -> None:
        return None

    monkeypatch.setattr(verify_module.shutil, "which", _which)


@pytest.mark.usefixtures("binary_on_path")
def test_check_passes_after_deploy(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del deployed_workspace
    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert out.strip().endswith(" ok")  # the tally, with nothing to report above it
    assert "missing" not in out and "drifted" not in out


@pytest.mark.usefixtures("binary_on_path")
def test_check_lists_ok_artifacts_only_when_verbose(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del deployed_workspace
    assert main(["check", str(sample_manifest), "--platform", "claude"]) == ExitCode.OK
    quiet = capsys.readouterr().out

    assert main(["-v", "check", str(sample_manifest), "--platform", "claude"]) == ExitCode.OK
    verbose = capsys.readouterr().out

    # A clean workspace prints only the manifest header and the tally: no per-artifact lines,
    # and hence no owner heading either.
    assert "rule style" not in quiet
    assert "claude" not in quiet
    assert "ok        rule style" in verbose
    assert f"claude  (found: {CLAUDE_BINARY})" in verbose


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_drift_when_a_rule_is_edited(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule = deployed_workspace / ".claude" / "rules" / "dev-essentials" / "style.md"
    rule.write_text(rule.read_text() + "\nhand-edited\n")

    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.ERROR
    assert "drifted   rule style" in out


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_missing_when_a_file_is_deleted(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (deployed_workspace / ".claude" / "agents" / "reviewer.md").unlink()

    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.ERROR
    assert "missing   agent reviewer" in out


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_drift_when_a_settings_key_is_edited(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Three MERGE ops share .claude/settings.json. Verifying each op's content in isolation
    # would report false drift, so the plan is folded per target before comparing.
    settings = deployed_workspace / ".claude" / "settings.json"
    data = json.loads(settings.read_text())
    data["permissions"]["allow"] = ["Bash(rm:*)"]
    settings.write_text(json.dumps(data))

    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.ERROR
    assert "key permissions.allow" in out


@pytest.mark.usefixtures("binary_on_path")
def test_check_ignores_user_added_settings_keys(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = deployed_workspace / ".claude" / "settings.json"
    data = json.loads(settings.read_text())
    data["env"] = {"FOO": "1"}
    settings.write_text(json.dumps(data))

    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    assert rc == ExitCode.OK
    assert "env" not in capsys.readouterr().out


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_stale_after_a_rule_leaves_the_manifest(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    design = _make_stack(tmp_path / "design", "design_rule", {"outputStyle": "Explanatory"})
    assert main(["deploy", str(design), "--platform", "claude"]) == ExitCode.OK
    maintenance = _make_stack(tmp_path / "maint", "maint_rule", {"cleanupPeriodDays": 7})
    capsys.readouterr()

    # The maintenance stack was never deployed, so design's rule is an orphan boff would remove.
    rc = main(["check", str(maintenance), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.ERROR
    assert "stale     orphaned file" in out
    assert "design_rule.md" in out


@pytest.mark.usefixtures("binary_on_path")
def test_check_never_writes_state(sample_manifest: Path, deployed_workspace: Path) -> None:
    state = deployed_workspace / ".boff" / "state.json"
    before = state.read_bytes()

    assert main(["check", str(sample_manifest), "--platform", "claude"]) == ExitCode.OK
    assert state.read_bytes() == before


@pytest.mark.usefixtures("binary_off_path")
def test_check_refuses_to_run_without_the_platform_cli(
    sample_manifest: Path, deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del deployed_workspace
    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    assert rc == ExitCode.ERROR
    assert "platform CLI not found on PATH: claude" in capsys.readouterr().err


@pytest.mark.usefixtures("binary_off_path")
def test_check_no_probe_skips_the_binary_check(
    sample_manifest: Path, deployed_workspace: Path
) -> None:
    del deployed_workspace
    assert (
        main(["check", str(sample_manifest), "--platform", "claude", "--no-probe"]) == ExitCode.OK
    )


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_artifacts_the_platform_drops(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Antigravity has no workspace target for slash commands. That is a report, not a failure.
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    stack = tmp_path / "stack"
    (stack / "rules").mkdir(parents=True)
    (stack / "rules" / "style.md").write_text("# style\n")
    (stack / "slash_commands").mkdir()
    (stack / "slash_commands" / "lint.md").write_text("# lint\n")
    (stack / "boff.yaml").write_text(
        "meta:\n  name: t\n  description: d\nrules:\n  - style\nslash_commands:\n  - lint\n"
    )
    assert main(["deploy", str(stack), "--platform", "antigravity"]) == ExitCode.OK
    capsys.readouterr()

    rc = main(["check", str(stack), "--platform", "antigravity"])

    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert "dropped   slash command lint" in out


def test_check_rejects_an_unknown_platform(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["check", str(sample_manifest), "--platform", "bogus"])

    assert rc == ExitCode.ERROR
    assert "no platform adapter registered for 'bogus'" in capsys.readouterr().err


@pytest.mark.usefixtures("binary_on_path")
def test_check_warns_when_the_workspace_was_never_deployed(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["check", str(sample_manifest), "--platform", "claude"])

    captured = capsys.readouterr()
    assert rc == ExitCode.ERROR
    assert "has this workspace been deployed?" in captured.err
    assert "missing" in captured.out


def test_deploy_resolves_a_git_url_positional(
    bare_repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    rc = main(
        ["deploy", f"file://{bare_repo}/{REMOTE_SUBDIR}@main", "--platform", "claude", "--dry-run"]
    )

    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert REMOTE_NAME in out  # the meta header proves the URL resolved to the remote manifest


def test_deploy_unresolvable_url_exits_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    rc = main(["deploy", "https://example.com/org", "--platform", "claude", "--dry-run"])

    err = capsys.readouterr().err
    assert rc == ExitCode.ERROR
    assert "cannot tell where the repository ends" in err
    assert "Traceback" not in err
