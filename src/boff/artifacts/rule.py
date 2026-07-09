"""Rule artifacts: one markdown rule file, and the whole rule set as a single unit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    """A platform rule, written as markdown."""

    name: str
    content: str
    category: str | None = None
    globs: tuple[str, ...] = ()
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on


@dataclass(frozen=True)
class Rules:
    """Every rule as one artifact, for platforms that load rules from a single file.

    Claude and OpenCode read a rules *directory*, so their adapters render each :class:`Rule`
    on its own and ignore this aggregate. Antigravity loads rules only from its primary
    instructions file, so its adapter renders this aggregate and ignores the individual rules.
    An adapter must handle exactly one of the two, which :meth:`PlatformAdapter.supports`
    enforces by dispatching on artifact type.
    """

    rules: tuple[Rule, ...] = ()
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if the rule set targets the given platform."""
        return not self.available_on or platform in self.available_on

    def rules_for(self, platform: str) -> tuple[Rule, ...]:
        """Return the rules that target the given platform, preserving authored order."""
        return tuple(rule for rule in self.rules if rule.is_available_on(platform))
