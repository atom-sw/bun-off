"""Context portability: export, import, and migrate context and plans.

This subsystem reads runtime state *from* a platform (a separate concern from
the declarative ``deploy`` path) so a project's context and plans can move
between machines or between coding assistants.
"""

from __future__ import annotations

from boff.context.antigravity import AntigravityContextProvider
from boff.context.base import ContextProvider
from boff.context.claude import ClaudeContextProvider
from boff.context.opencode import OpenCodeContextProvider
from boff.registry import Registry

_PROVIDERS: Registry[ContextProvider] = Registry("context provider")


def register(provider: ContextProvider) -> None:
    """Register a context provider under its ``name``."""
    _PROVIDERS.register(provider.name, provider)


def get_provider(name: str) -> ContextProvider:
    """Look up the context provider registered under ``name``."""
    return _PROVIDERS.get(name)


def provider_names() -> list[str]:
    """Return the names of all registered context providers."""
    return _PROVIDERS.names()


register(ClaudeContextProvider())
register(OpenCodeContextProvider())
register(AntigravityContextProvider())


__all__ = [
    "AntigravityContextProvider",
    "ClaudeContextProvider",
    "ContextProvider",
    "OpenCodeContextProvider",
    "get_provider",
    "provider_names",
    "register",
    "AntigravityContextProvider",
]
