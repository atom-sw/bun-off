import json
from collections.abc import Callable
from pathlib import Path

import pytest

from boff import verify as verify_module
from boff.cli import ExitCode, main
from tests.conftest import META, REMOTE_NAME, REMOTE_SUBDIR, SKILL_SUPPORT

# The directory-form skill built by the `skill_bundle` fixture.
SKILL_NAME = "s"


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


def test_deploying_an_output_style_writes_it_verbatim(
    sample_manifest: Path, deployed_workspace: Path
) -> None:
    # Claude parses output-style frontmatter with a strict schema, so a byte-for-byte copy is
    # the contract: any rewriting by boff would make the style fail to load.
    source = sample_manifest / "output_styles" / "tutor.md"
    deployed = deployed_workspace / ".claude" / "output-styles" / "tutor.md"
    assert deployed.read_text() == source.read_text()


def test_removing_an_output_style_deletes_it_and_prunes_the_directory(
    deployed_workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    styles = deployed_workspace / ".claude" / "output-styles"
    assert (styles / "tutor.md").is_file()
    bare = deployed_workspace / "bare"
    (bare / "rules").mkdir(parents=True)
    (bare / "rules" / "style.md").write_text("# style\n")
    (bare / "boff.yaml").write_text(META + "rules:\n  - style\n")

    assert main(["deploy", str(bare), "--platform", "claude"]) == ExitCode.OK
    capsys.readouterr()

    assert not (styles / "tutor.md").exists()
    assert not styles.exists()


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_an_output_style_dropped_on_opencode(
    sample_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # OpenCode has no output-style surface. That is a report, not a failure.
    monkeypatch.chdir(tmp_path)
    assert main(["deploy", str(sample_manifest), "--platform", "opencode"]) == ExitCode.OK
    capsys.readouterr()

    rc = main(["check", str(sample_manifest), "--platform", "opencode"])

    out = capsys.readouterr().out
    assert rc == ExitCode.OK
    assert "dropped   output style tutor" in out
    assert not (tmp_path / ".opencode" / "output-styles").exists()


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


# --- Multi-manifest stacks: `deploy A B`, `--add`, `--remove` -------------------------------


def _bundle(root: Path, name: str, rules: dict[str, str]) -> Path:
    """Create a manifest folder named ``name`` whose ``rules`` map rule name to body."""
    (root / "rules").mkdir(parents=True, exist_ok=True)
    for rule, body in rules.items():
        (root / "rules" / f"{rule}.md").write_text(body)
    listing = "".join(f"  - {rule}\n" for rule in rules)
    (root / "boff.yaml").write_text(
        f"meta:\n  name: {name}\n  description: {name}\nrules:\n{listing}"
    )
    return root


def _rule_file(project: Path, name: str) -> Path:
    """Where the claude adapter writes rule ``name`` in ``project``."""
    return project / ".claude" / "rules" / f"{name}.md"


def _recorded_stack(project: Path) -> list[str]:
    """The manifest references recorded in ``project``'s deploy state."""
    state = json.loads((project / ".boff" / "state.json").read_text())
    return state["stacks"]["workspace"]


def _meta_name(letter: str) -> str:
    """The ``meta.name`` of a test bundle: distinctive enough to grep for in CLI output."""
    return f"bundle-{letter}"


@pytest.fixture
def stacked_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, Path, Path, Path]:
    """A workspace with bundles a and b deployed to claude; returns (project, a, b, c).

    Bundle c is created but not deployed, so a test can add it. All three define the rule
    ``shared`` with a distinct body, which is what makes last-wins observable.
    """
    project = tmp_path / "project"
    project.mkdir()
    bundles = {
        letter: _bundle(
            tmp_path / letter,
            _meta_name(letter),
            {"shared": f"from-{letter}", f"only_{letter}": letter},
        )
        for letter in ("a", "b", "c")
    }
    monkeypatch.chdir(project)
    assert main(["deploy", str(bundles["a"]), str(bundles["b"]), "--platform", "claude"]) == 0
    capsys.readouterr()
    return project, bundles["a"], bundles["b"], bundles["c"]


def test_deploy_merges_several_manifests_with_the_last_winning(
    stacked_workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, _, bundle_b, _ = stacked_workspace

    assert _rule_file(project, "only_a").is_file()
    assert _rule_file(project, "only_b").is_file()
    assert (
        _rule_file(project, "shared").read_text() == (bundle_b / "rules" / "shared.md").read_text()
    )


def test_the_same_relative_reference_resolves_per_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Manifest references are resolved through a memo, so the same relative spelling used from
    # two directories must not collapse onto whichever one was resolved first.
    ref = "../bundle"
    for letter in ("a", "b"):
        workspace = tmp_path / letter
        _bundle(workspace / "bundle", _meta_name(letter), {f"only_{letter}": letter})
        (workspace / "project").mkdir()
        monkeypatch.chdir(workspace / "project")
        assert main(["deploy", ref, "--platform", "claude"]) == 0
        capsys.readouterr()
        assert _rule_file(workspace / "project", f"only_{letter}").is_file()


def test_deploy_records_the_stack_in_state(
    stacked_workspace: tuple[Path, Path, Path, Path],
) -> None:
    project, bundle_a, bundle_b, _ = stacked_workspace
    assert _recorded_stack(project) == [str(bundle_a), str(bundle_b)]


def test_deploy_warns_when_two_manifests_define_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = "shared"
    project = tmp_path / "project"
    project.mkdir()
    first = _bundle(tmp_path / "first", "first", {shared: "one"})
    second = _bundle(tmp_path / "second", "second", {shared: "two"})
    monkeypatch.chdir(project)

    assert main(["deploy", str(first), str(second), "--platform", "claude"]) == 0

    err = capsys.readouterr().err
    assert f"rules '{shared}'" in err
    assert "overrides an earlier definition" in err


def test_deploy_add_appends_to_the_recorded_stack(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    project, bundle_a, bundle_b, bundle_c = stacked_workspace

    # No --platform: it falls back to the platform already recorded for this directory.
    assert main(["deploy", "--add", str(bundle_c)]) == 0
    capsys.readouterr()

    assert _recorded_stack(project) == [str(bundle_a), str(bundle_b), str(bundle_c)]
    for rule in ("only_a", "only_b", "only_c"):
        assert _rule_file(project, rule).is_file()
    assert (
        _rule_file(project, "shared").read_text() == (bundle_c / "rules" / "shared.md").read_text()
    )


def test_deploy_remove_drops_a_manifest_and_reclaims_its_files(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    project, bundle_a, bundle_b, _ = stacked_workspace

    assert main(["deploy", "--remove", str(bundle_b)]) == 0
    capsys.readouterr()

    assert _recorded_stack(project) == [str(bundle_a)]
    assert not _rule_file(project, "only_b").exists()
    assert _rule_file(project, "only_a").is_file()
    # `shared` reverts to bundle a's body now that b no longer overrides it.
    assert (
        _rule_file(project, "shared").read_text() == (bundle_a / "rules" / "shared.md").read_text()
    )


def test_deploy_add_of_an_already_deployed_manifest_moves_it_last(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    project, bundle_a, bundle_b, _ = stacked_workspace

    assert main(["deploy", "--add", str(bundle_a)]) == 0

    err = capsys.readouterr().err
    assert "already deployed" in err
    assert _recorded_stack(project) == [str(bundle_b), str(bundle_a)]
    assert (
        _rule_file(project, "shared").read_text() == (bundle_a / "rules" / "shared.md").read_text()
    )


def test_deploy_with_no_arguments_redeploys_the_recorded_stack(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    project, bundle_a, bundle_b, _ = stacked_workspace
    _rule_file(project, "only_a").unlink()

    assert main(["deploy"]) == 0
    capsys.readouterr()

    assert _rule_file(project, "only_a").is_file()
    assert _recorded_stack(project) == [str(bundle_a), str(bundle_b)]


def test_check_with_no_manifest_verifies_the_recorded_stack(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle_a, bundle_b, _ = stacked_workspace

    assert main(["check", "--no-probe"]) == ExitCode.OK

    # The stack's metadata headers name every member, in merge order.
    out = capsys.readouterr().out
    assert _meta_name(bundle_a.name) in out
    assert _meta_name(bundle_b.name) in out


def test_check_reports_drift_against_the_recorded_stack(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    project, _, _, _ = stacked_workspace
    _rule_file(project, "only_a").unlink()

    assert main(["check", "--no-probe"]) == ExitCode.ERROR
    assert "missing" in capsys.readouterr().out


def test_clean_forgets_the_recorded_stack(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, _, bundle_c = stacked_workspace

    assert main(["clean"]) == 0
    capsys.readouterr()

    # With the stack forgotten, --add has nothing to build on rather than resurrecting a and b.
    assert main(["deploy", "--add", str(bundle_c)]) == ExitCode.USAGE
    assert "no deployed stack recorded" in capsys.readouterr().err


def test_single_manifest_deploy_is_unaffected_by_stack_merging(
    sample_manifest: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A one-manifest stack must bypass merge_manifests, which is not the identity on a single
    # manifest: it recomputes Settings.available_on and rebuilds EventHooks.
    monkeypatch.chdir(tmp_path)

    assert main(["deploy", str(sample_manifest), "--platform", "claude"]) == 0
    capsys.readouterr()

    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert (tmp_path / ".claude" / "hooks").is_dir()
    assert _recorded_stack(tmp_path) == [str(sample_manifest)]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(["deploy", "--add", "x"], "no deployed stack recorded", id="add-no-stack"),
        pytest.param(
            ["deploy", "--remove", "x"], "no deployed stack recorded", id="remove-no-stack"
        ),
        pytest.param(["deploy"], "no deployed stack recorded", id="bare-deploy-no-stack"),
        pytest.param(["check"], "no deployed stack recorded", id="bare-check-no-stack"),
    ],
)
def test_stack_arguments_without_a_recorded_stack_are_usage_errors(
    argv: list[str],
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(argv) == ExitCode.USAGE
    assert expected in capsys.readouterr().err


def test_deploy_without_platform_or_recorded_state_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = _bundle(tmp_path / "b", "b", {"r": "body"})
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    assert main(["deploy", str(bundle)]) == ExitCode.USAGE
    assert "no --platform given" in capsys.readouterr().err


def test_mixing_manifest_arguments_with_add_is_a_usage_error(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle_a, _, bundle_c = stacked_workspace

    assert main(["deploy", str(bundle_a), "--add", str(bundle_c)]) == ExitCode.USAGE
    assert "use one or the other" in capsys.readouterr().err


def test_removing_a_manifest_not_in_the_stack_is_a_usage_error(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, _, bundle_c = stacked_workspace

    assert main(["deploy", "--remove", str(bundle_c)]) == ExitCode.USAGE
    assert "is not in the deployed stack" in capsys.readouterr().err


def test_removing_every_manifest_is_a_usage_error(
    stacked_workspace: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle_a, bundle_b, _ = stacked_workspace

    rc = main(["deploy", "--remove", str(bundle_a), "--remove", str(bundle_b)])

    assert rc == ExitCode.USAGE
    assert "boff clean" in capsys.readouterr().err


@pytest.fixture
def skill_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_manifest: Callable[..., Path],
    write_skill_dir: Callable[..., Path],
) -> tuple[Path, Path]:
    """A bundle holding one directory-form skill, plus a fresh workspace as cwd."""
    bundle = tmp_path / "bundle"
    write_skill_dir(bundle, SKILL_NAME)
    write_manifest(bundle, f"skills:\n  - {SKILL_NAME}\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    return bundle, workspace


def test_deploying_a_directory_skill_writes_its_supporting_files(
    skill_bundle: tuple[Path, Path],
) -> None:
    bundle, workspace = skill_bundle

    assert main(["deploy", str(bundle), "--platform", "claude"]) == ExitCode.OK

    deployed = workspace / ".claude" / "skills" / SKILL_NAME
    assert (deployed / "SKILL.md").read_text() == (
        bundle / "skills" / SKILL_NAME / "SKILL.md"
    ).read_text()
    assert {rel: (deployed / rel).read_text() for rel in SKILL_SUPPORT} == SKILL_SUPPORT


def test_redeploying_without_a_supporting_file_removes_it(
    skill_bundle: tuple[Path, Path],
) -> None:
    bundle, workspace = skill_bundle
    dropped = next(iter(SKILL_SUPPORT))
    assert main(["deploy", str(bundle), "--platform", "claude"]) == ExitCode.OK

    (bundle / "skills" / SKILL_NAME / dropped).unlink()
    assert main(["deploy", str(bundle), "--platform", "claude"]) == ExitCode.OK

    deployed = workspace / ".claude" / "skills" / SKILL_NAME
    assert not (deployed / dropped).exists()
    # The emptied subdirectory is pruned too, so no orphaned `references/` is left behind.
    assert not (deployed / dropped).parent.exists()
    assert (deployed / "SKILL.md").is_file()


@pytest.mark.usefixtures("binary_on_path")
def test_check_reports_drift_when_a_supporting_file_is_edited(
    skill_bundle: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, workspace = skill_bundle
    edited = next(iter(SKILL_SUPPORT))
    assert main(["deploy", str(bundle), "--platform", "claude"]) == ExitCode.OK
    capsys.readouterr()

    target = workspace / ".claude" / "skills" / SKILL_NAME / edited
    target.write_text(target.read_text() + "hand-edited\n")
    rc = main(["check", str(bundle), "--platform", "claude"])

    out = capsys.readouterr().out
    assert rc == ExitCode.ERROR
    # The finding must name the supporting file, not just the skill it belongs to.
    assert f"drifted   skill {SKILL_NAME}" in out
    assert str(target.relative_to(workspace)) in out
