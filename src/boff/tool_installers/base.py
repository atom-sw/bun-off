"""Base class for whole-file tool installers (mise, direnv, etc.)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from boff.types import Operation, Scope


class ToolInstaller:
    """Receives a list of source files and emits operations to install them."""

    name: ClassVar[str] = ""

    def install_files(self, files: list[Path], *, scope: Scope) -> list[Operation]:
        """Return operations that install ``files`` for the given scope."""
        del files, scope
        raise NotImplementedError
