"""Settings artifact: verbatim per-platform settings block for native settings files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Keys a dedicated artifact already writes to each platform's settings target. The manifest
# loader rejects these in a passthrough block so the modeled concept stays canonical.
RESERVED_KEYS: dict[str, frozenset[str]] = {
    "claude": frozenset({"permissions", "mcpServers"}),
    "opencode": frozenset({"permission", "mcp", "instructions"}),
}


@dataclass(frozen=True)
class Settings:
    """A verbatim per-platform settings block, deep-merged into native settings files."""

    raw: dict[str, dict[str, Any]]
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on

    def raw_for(self, platform: str) -> dict[str, Any] | None:
        """Return the verbatim settings block for the platform, or None if absent."""
        return self.raw.get(platform)
