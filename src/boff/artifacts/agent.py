"""Agent artifact: a focused worker with optional per-agent permission scoping."""

from __future__ import annotations

from dataclasses import dataclass

from boff.artifacts.permissions import PermissionRule


@dataclass(frozen=True)
class Agent:
    """A subagent definition: a system-prompt body plus per-agent metadata and permissions."""

    name: str
    content: str
    description: str
    model: str | dict[str, str] | None = None
    mode: str | None = None
    permissions: tuple[PermissionRule, ...] = ()
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this agent targets the given platform."""
        return not self.available_on or platform in self.available_on

    def model_for(self, platform: str) -> str | None:
        """Return the model id for the platform, or None to inherit the platform default.

        A bare string applies verbatim to every platform; a per-platform map applies its
        matching value, and a platform absent from the map inherits the platform default.
        """
        if self.model is None or isinstance(self.model, str):
            return self.model
        return self.model.get(platform)

    def permissions_for(self, platform: str) -> tuple[PermissionRule, ...]:
        """Return the permission rules that target the given platform, in authored order."""
        return tuple(rule for rule in self.permissions if rule.is_available_on(platform))
