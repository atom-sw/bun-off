"""MCPServer artifact: verbatim per-platform MCP server config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MCPServer:
    """An MCP server entry with one verbatim JSON config block per platform."""

    name: str
    raw: dict[str, dict[str, Any]]
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on
