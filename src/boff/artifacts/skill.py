"""Skill artifact: a markdown skill file delivered to a coding platform."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """A platform skill, written as markdown."""

    name: str
    content: str
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on
