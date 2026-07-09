"""Error-path tests: every guarded parse/subprocess site fails cleanly.

Each case asserts a boff-specific exception (never a raw traceback) and, where
the failure reaches the CLI, a clean stderr message plus a non-zero exit code.
"""

import io
import json
import os
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from boff.adapters import get_adapter
from boff.cli import ExitCode, main
from boff.context.bundle import SCHEMA_VERSION, read_bundle
from boff.errors import BoffError, BundleError, HookError, ManifestError, RegistryError
from boff.executor import execute
from boff.hooks import HookContext, run_hooks
from boff.manifest import load_manifest
from boff.sources.local import LocalSource, LocalSpec
from boff.state import load_state
from boff.tool_installers import get_tool_installer
from boff.tool_installers.mise import MiseInstaller
from boff.types import (
    FileOperation,
    HookPhase,
    MergeStrategy,
    PruneKeysOperation,
    Scope,
    ScopeKind,
)

# --- registry lookups -------------------------------------------------------


def test_unknown_adapter_raises_registry_error() -> None:
    with pytest.raises(RegistryError, match="nope") as exc:
        get_adapter("nope")
    assert isinstance(exc.value, BoffError)


def test_unknown_tool_installer_raises_registry_error() -> None:
    with pytest.raises(RegistryError, match="nope"):
        get_tool_installer("nope")


def test_unknown_platform_deploy_exits_clean(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write_manifest: Callable[..., Path],
) -> None:
    monkeypatch.chdir(tmp_path)
    root = write_manifest(tmp_path / "m")
    rc = main(["deploy", str(root), "--platform", "nope"])
    err = capsys.readouterr().err
    assert rc == ExitCode.ERROR
    assert err.startswith("✗ ")
    assert "Traceback" not in err


# --- manifest parsing -------------------------------------------------------


def test_malformed_yaml_raises_manifest_error(tmp_path: Path) -> None:
    root = tmp_path / "m"
    root.mkdir()
    (root / "boff.yaml").write_text("meta: [unclosed\n")
    with pytest.raises(ManifestError, match="invalid YAML"):
        load_manifest(root)


def test_malformed_yaml_deploy_exits_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "m"
    root.mkdir()
    (root / "boff.yaml").write_text("meta: [unclosed\n")
    rc = main(["deploy", str(root), "--platform", "claude"])
    err = capsys.readouterr().err
    assert rc == ExitCode.ERROR
    assert err.startswith("✗ ")
    assert "Traceback" not in err


def test_malformed_raw_mcp_json_raises_manifest_error(
    tmp_path: Path, write_manifest: Callable[..., Path]
) -> None:
    root = write_manifest(tmp_path / "m", "mcp_servers:\n  - ctx7\n")
    raw = root / "mcp_servers" / "raw" / "claude"
    raw.mkdir(parents=True)
    (raw / "ctx7.json").write_text("{not json")
    with pytest.raises(ManifestError, match="invalid JSON in raw MCP"):
        load_manifest(root)


# --- state ------------------------------------------------------------------


def test_corrupt_state_file_raises_boff_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{corrupt")
    with pytest.raises(BoffError, match=str(path)):
        load_state(path)


# --- executor JSON targets --------------------------------------------------


def test_merge_into_malformed_json_raises_boff_error(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{ this is not json")
    op = FileOperation(
        target=target,
        content='{"a": 1}',
        merge=MergeStrategy.MERGE,
        description="merge onto malformed file",
    )
    with pytest.raises(BoffError, match="malformed JSON"):
        execute([op])


def test_prune_from_malformed_json_raises_boff_error(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{ this is not json")
    op = PruneKeysOperation(
        target=target, key_paths=[("a",)], description="prune from malformed file"
    )
    with pytest.raises(BoffError, match="malformed JSON"):
        execute([op])


# --- hook context -----------------------------------------------------------


def test_hook_context_from_stdin_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    with pytest.raises(HookError, match="malformed hook context"):
        HookContext.from_stdin()


def test_hook_context_from_stdin_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"phase": "pre_install"})))
    with pytest.raises(HookError, match="malformed hook context"):
        HookContext.from_stdin()


# --- hook subprocess --------------------------------------------------------


def _hook_ctx(root: Path) -> HookContext:
    return HookContext(
        phase=HookPhase.PRE_INSTALL,
        platforms=("claude",),
        scope=Scope(kind=ScopeKind.WORKSPACE, workspace_root=root),
        manifest_root=root,
        ops_count=0,
    )


def test_run_hooks_nonzero_exit_raises_hook_error(tmp_path: Path) -> None:
    hooks_dir = tmp_path / "bundle" / "hooks"
    hooks_dir.mkdir(parents=True)
    script = hooks_dir / "boom.py"
    script.write_text("import sys\nprint('kaboom', file=sys.stderr)\nsys.exit(3)\n")
    with pytest.raises(HookError, match="boom.py failed") as exc:
        run_hooks([script], _hook_ctx(tmp_path))
    assert "kaboom" in str(exc.value)


def test_run_hooks_shallow_path_raises_hook_error(tmp_path: Path) -> None:
    with pytest.raises(HookError, match="too shallow"):
        run_hooks([Path("/boom.py")], _hook_ctx(tmp_path))


def test_real_deploy_missing_hook_returns_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    write_manifest: Callable[..., Path],
) -> None:
    monkeypatch.chdir(tmp_path)
    root = write_manifest(tmp_path / "m", "hooks:\n  pre_install:\n    - script: nonexistent\n")
    rc = main(["deploy", str(root), "--platform", "claude"])
    err = capsys.readouterr().err
    assert rc == ExitCode.ERROR
    assert "missing hook script" in err
    assert "nonexistent.py" in err
    assert "Traceback" not in err


# --- context bundle ---------------------------------------------------------


def _write_raw_bundle(path: Path, manifest: dict[str, object]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        data = json.dumps(manifest).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))


def test_read_bundle_unsupported_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "b.tar.gz"
    _write_raw_bundle(path, {"schema": SCHEMA_VERSION + 99})
    with pytest.raises(BundleError, match="unsupported bundle schema"):
        read_bundle(path)


def test_read_bundle_not_an_archive_raises(tmp_path: Path) -> None:
    path = tmp_path / "b.tar.gz"
    path.write_bytes(b"definitely not a gzip tar")
    with pytest.raises(BundleError, match="corrupt context bundle"):
        read_bundle(path)


# --- source reads -----------------------------------------------------------


def test_mise_missing_source_file_raises_boff_error(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(BoffError, match="nope.toml"):
        MiseInstaller().install_files([missing], scope=make_scope(tmp_path / "ws"))


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses file permissions"
)
def test_local_source_unreadable_file_raises_boff_error(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    unreadable = src / "secret.txt"
    unreadable.write_text("x")
    unreadable.chmod(0)
    try:
        spec = LocalSpec(source="local", path=src)
        with pytest.raises(BoffError, match="secret.txt"):
            LocalSource().install(spec, platform="claude", scope=make_scope(tmp_path / "ws"))
    finally:
        unreadable.chmod(0o644)
