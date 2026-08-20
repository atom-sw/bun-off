import logging
from pathlib import Path

import pytest

from boff.sources.local import LocalSource, LocalSpec
from boff.types import Scope, ScopeKind
from tests.conftest import file_op


def test_local_source_requires_workspace_root() -> None:
    # A workspace deploy without a resolved root cannot place mirrored files.
    spec = LocalSpec(source="local", path=Path("/does/not/matter"))
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=None)
    with pytest.raises(ValueError, match="workspace_root"):
        LocalSource().install(spec, platform="claude", scope=scope)


def test_local_source_mirrors_tree(tmp_path: Path) -> None:
    src_root = tmp_path / "src-tree"
    (src_root / "sub").mkdir(parents=True)
    (src_root / "a.txt").write_text("alpha")
    (src_root / "sub" / "b.txt").write_text("beta")

    workspace = tmp_path / "wk"
    workspace.mkdir()
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=workspace)

    spec = LocalSpec(source="local", path=src_root)
    ops = LocalSource().install(spec, platform="claude", scope=scope)

    targets = sorted(file_op(op).target for op in ops)
    assert targets == [workspace / "a.txt", workspace / "sub" / "b.txt"]


def test_local_source_warns_and_skips_at_user_level(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # There is no user-level analogue of "copy this subtree to the deploy root", so the plugin
    # is skipped rather than aborting a deploy whose other artifacts are global by design.
    src_root = tmp_path / "src-tree"
    src_root.mkdir()
    (src_root / "a.txt").write_text("alpha")

    spec = LocalSpec(source="local", path=src_root)
    with caplog.at_level(logging.WARNING):
        ops = LocalSource().install(spec, platform="claude", scope=Scope(kind=ScopeKind.GLOBAL))

    assert ops == []
    assert "no user-level target" in caplog.text
