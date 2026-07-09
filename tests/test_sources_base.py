from dataclasses import dataclass
from pathlib import Path

import pytest

from boff.errors import BoffError
from boff.sources.base import PluginSource
from boff.sources.local import LocalSource, LocalSpec
from boff.types import FileOperation, Scope, ScopeKind

SCOPE = Scope(kind=ScopeKind.WORKSPACE, workspace_root=Path("/tmp/wk"))


@dataclass(frozen=True)
class _ForeignSpec:
    """A spec belonging to some other source: satisfies PluginSpec, but not LocalSource's."""

    source: str


def test_subclass_without_spec_class_is_rejected_at_definition() -> None:
    # Built with type() rather than a class statement: __init_subclass__ raises, so the class
    # object never comes into existence and cannot be named.
    with pytest.raises(BoffError, match="must set 'spec_class'"):
        type("_NoSpec", (PluginSource,), {"name": "nospec"})


def test_install_rejects_a_spec_from_another_source() -> None:
    with pytest.raises(BoffError, match="expects a LocalSpec install spec, got _ForeignSpec"):
        LocalSource().install(_ForeignSpec(source="other"), platform="claude", scope=SCOPE)


def test_install_rejects_an_unsupported_platform() -> None:
    # A BoffError, not a KeyError: `main` catches the former and prints it, and a leaked
    # non-BoffError is a bug that reaches the user as a traceback.
    spec = LocalSpec(source="local", path=Path("/does/not/matter"))
    with pytest.raises(BoffError, match="cannot install for platform 'nonexistent'"):
        LocalSource().install(spec, platform="nonexistent", scope=SCOPE)


@pytest.mark.parametrize("platform", ["claude", "opencode", "antigravity"], ids=str)
def test_local_source_installs_for_all_platforms(tmp_path: Path, platform: str) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    greeting = "hello from the plugin\n"
    (plugin_dir / "greeting.txt").write_text(greeting)
    spec = LocalSpec(source="local", path=plugin_dir)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scope = Scope(kind=ScopeKind.WORKSPACE, workspace_root=workspace)
    ops = LocalSource().install(spec, platform=platform, scope=scope)
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, FileOperation)
    assert op.target == workspace / "greeting.txt"
    assert op.content == greeting.encode()
