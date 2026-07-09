"""Plugin source registry."""

from __future__ import annotations

from boff.registry import Registry
from boff.sources.base import PluginSource, PluginSpec, installs_for
from boff.sources.local import LocalSource

_SOURCES: Registry[PluginSource] = Registry("plugin source")


def register(source: PluginSource) -> None:
    """Register a plugin source under its ``name``."""
    _SOURCES.register(source.name, source)


def get_source(name: str) -> PluginSource:
    """Look up the source registered under ``name``."""
    return _SOURCES.get(name)


register(LocalSource())


__all__ = ["LocalSource", "PluginSource", "PluginSpec", "get_source", "installs_for", "register"]
