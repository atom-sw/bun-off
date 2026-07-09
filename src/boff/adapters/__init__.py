"""Platform adapter registry."""

from __future__ import annotations

from boff.adapters.antigravity import AntigravityAdapter
from boff.adapters.base import PlatformAdapter, renders
from boff.adapters.claude import ClaudeAdapter
from boff.adapters.opencode import OpenCodeAdapter
from boff.registry import Registry

_ADAPTERS: Registry[PlatformAdapter] = Registry("platform adapter")


def register(adapter: PlatformAdapter) -> None:
    """Register a platform adapter under its ``name``."""
    _ADAPTERS.register(adapter.name, adapter)


def get_adapter(name: str) -> PlatformAdapter:
    """Look up the adapter registered under ``name``."""
    return _ADAPTERS.get(name)


def adapter_names() -> list[str]:
    """Return the names of every registered platform adapter."""
    return _ADAPTERS.names()


register(ClaudeAdapter())
register(OpenCodeAdapter())
register(AntigravityAdapter())


__all__ = [
    "AntigravityAdapter",
    "ClaudeAdapter",
    "OpenCodeAdapter",
    "PlatformAdapter",
    "adapter_names",
    "get_adapter",
    "register",
    "renders",
]
