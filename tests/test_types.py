from pathlib import Path

from boff.types import FileOperation, MergeStrategy, Scope, ScopeKind, ShellAction


def test_fileop_defaults() -> None:
    op = FileOperation(target=Path("/tmp/foo"), content="hello")
    assert op.merge is MergeStrategy.OVERWRITE
    assert not op.description


def test_shellaction_defaults() -> None:
    action = ShellAction(argv=["echo", "hi"])
    assert action.env == {}
    assert action.cwd is None


def test_strenum_compares_to_string() -> None:
    assert MergeStrategy.OVERWRITE == "overwrite"
    assert ScopeKind.WORKSPACE == "workspace"


def test_scope_workspace_construction() -> None:
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=Path("/tmp/wk"))
    assert scope.workspace_root == Path("/tmp/wk")
