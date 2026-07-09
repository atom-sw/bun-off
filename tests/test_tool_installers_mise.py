from pathlib import Path

import pytest

from boff.tool_installers.base import ToolInstaller
from boff.tool_installers.mise import MiseInstaller
from boff.types import FileOperation, Scope, ScopeKind

_CONF_D = Path(".config") / "mise" / "conf.d"


def test_base_installer_install_files_is_abstract() -> None:
    # The base class defines the contract but installs nothing on its own.
    with pytest.raises(NotImplementedError):
        ToolInstaller().install_files([], scope=Scope(kind=ScopeKind.GLOBAL))


def test_mise_installer_workspace_target(tmp_path: Path) -> None:
    mise_file = tmp_path / "mise.toml"
    mise_file.write_text("[tools]\npython = '3.12'\n")
    workspace = tmp_path / "wk"
    workspace.mkdir()

    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=workspace)
    ops = MiseInstaller().install_files([mise_file], scope=scope)

    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == workspace / _CONF_D / "boff-0.toml"


def test_mise_installer_global_target(tmp_path: Path) -> None:
    mise_file = tmp_path / "mise.toml"
    mise_file.write_text("[tools]\nnode = '22'\n")

    scope = Scope(kind=ScopeKind.GLOBAL)
    ops = MiseInstaller().install_files([mise_file], scope=scope)

    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == Path.home() / _CONF_D / "boff-0.toml"


def test_mise_installer_one_file_per_source(tmp_path: Path) -> None:
    f1 = tmp_path / "a.toml"
    f1.write_text("[tools]\npython = '3.12'\n")
    f2 = tmp_path / "b.toml"
    f2.write_text("[tools]\nnode = '22'\n")
    workspace = tmp_path / "wk"
    workspace.mkdir()

    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=workspace)
    ops = MiseInstaller().install_files([f1, f2], scope=scope)

    assert len(ops) == 2
    first, second = ops
    assert isinstance(first, FileOperation)
    assert isinstance(second, FileOperation)
    assert first.target == workspace / _CONF_D / "boff-0.toml"
    assert second.target == workspace / _CONF_D / "boff-1.toml"
    # Each drop-in keeps its own [tools] table, so mise merges them without collision.
    assert isinstance(first.content, bytes) and b"python" in first.content
    assert isinstance(second.content, bytes) and b"node" in second.content


def test_mise_installer_workspace_scope_requires_root() -> None:
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=None)
    with pytest.raises(ValueError, match="workspace_root"):
        MiseInstaller().install_files([], scope=scope)
