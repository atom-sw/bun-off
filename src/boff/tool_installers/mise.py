"""Mise tool installer: writes a boff-owned config into mise's conf.d directory."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from boff.errors import BoffError
from boff.platform_layout import xdg_config_home
from boff.tool_installers.base import ToolInstaller
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ScopeKind

_CONF_D_NAME = Path("mise") / "conf.d"
_CONF_D_REL = Path(".config") / _CONF_D_NAME


class MiseInstaller(ToolInstaller):
    """Write each listed mise file into ``.config/mise/conf.d/`` as its own drop-in.

    Each source becomes a separate ``boff-<index>.toml`` so mise merges them
    natively (a single concatenated file would collide on repeated ``[tools]``
    headers). For workspace scope the files land inside the workspace root,
    leaving any existing ``mise.toml`` untouched; for global scope they land in the
    user's config directory (``$XDG_CONFIG_HOME/mise/conf.d/``, else
    ``~/.config/mise/conf.d/``).
    """

    name: ClassVar[str] = "mise"

    def install_files(self, files: list[Path], *, scope: Scope) -> list[Operation]:
        if scope.kind is ScopeKind.GLOBAL:
            conf_d = xdg_config_home() / _CONF_D_NAME
        else:
            if scope.workspace_root is None:
                raise ValueError("mise installer requires workspace_root for workspace scope")
            conf_d = scope.workspace_root / _CONF_D_REL
        return [
            FileOperation(
                target=conf_d / f"boff-{index}.toml",
                content=_read_mise_file(src).rstrip() + b"\n",
                merge=MergeStrategy.OVERWRITE,
                description=f"mise install boff-{index}.toml",
            )
            for index, src in enumerate(files)
        ]


def _read_mise_file(src: Path) -> bytes:
    """Read the contents of a mise configuration file."""
    try:
        return src.read_bytes()
    except OSError as exc:
        raise BoffError(f"cannot read mise config file {src}: {exc}") from exc
