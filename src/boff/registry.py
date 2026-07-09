"""Generic name-keyed registry shared by the adapter, source, installer, and provider registries.

Each of those registries maps a name (``claude``, ``local``, ``mise``, ...) to a single
instance and looks it up by name. The ordered ``manifest_sources`` registry is intentionally
separate: it dispatches by prefix matching, not by exact name.
"""

from __future__ import annotations

from boff.errors import RegistryError


class Registry[T]:
    """A name-keyed registry: register items by name, look them up, and list the names."""

    def __init__(self, kind: str) -> None:
        """Create an empty registry; ``kind`` names the entry type for error messages."""
        self._kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        """Register ``item`` under ``name`` (last registration wins)."""
        self._items[name] = item

    def get(self, name: str) -> T:
        """Return the item registered under ``name``, or raise :class:`RegistryError`."""
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(self.names()) or "none"
            raise RegistryError(
                f"no {self._kind} registered for {name!r} (known: {known})"
            ) from None

    def names(self) -> list[str]:
        """Return the registered names, sorted."""
        return sorted(self._items)
