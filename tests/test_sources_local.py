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
