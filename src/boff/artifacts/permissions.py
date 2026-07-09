"""Permission artifact: platform-neutral tool-use rules deployed to native settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["allow", "ask", "deny"]

CANONICAL_TOOLS: frozenset[str] = frozenset(
    {
        "bash",
        "read",
        "edit",
        "write",
        "glob",
        "grep",
        "webfetch",
        "websearch",
        "agent",
        "mcp",
        "lsp",
        "skill",
        "question",
        "external_directory",
        "doom_loop",
    }
)


@dataclass(frozen=True)
class PermissionRule:
    """A tool-use permission: an action applied to a tool, optionally scoped by a pattern."""

    tool: str
    action: Action
    pattern: str | None = None
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this rule targets the given platform."""
        return not self.available_on or platform in self.available_on


@dataclass(frozen=True)
class Permissions:
    """Aggregate of every permission rule, rendered as a single config block per platform."""

    rules: tuple[PermissionRule, ...] = ()
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if the permission block targets the given platform."""
        return not self.available_on or platform in self.available_on

    def rules_for(self, platform: str) -> tuple[PermissionRule, ...]:
        """Return the rules that target the given platform, preserving authored order."""
        return tuple(rule for rule in self.rules if rule.is_available_on(platform))
