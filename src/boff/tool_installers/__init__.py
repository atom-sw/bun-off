"""Tool installer registry."""

from __future__ import annotations

from boff.registry import Registry
from boff.tool_installers.base import ToolInstaller
from boff.tool_installers.mise import MiseInstaller

_INSTALLERS: Registry[ToolInstaller] = Registry("tool installer")


def register(installer: ToolInstaller) -> None:
    """Register a tool installer under its ``name``."""
    _INSTALLERS.register(installer.name, installer)


def get_tool_installer(name: str) -> ToolInstaller:
    """Look up the tool installer registered under ``name``."""
    return _INSTALLERS.get(name)


register(MiseInstaller())


__all__ = ["MiseInstaller", "ToolInstaller", "get_tool_installer", "register"]
