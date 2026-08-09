"""OutputStyle artifact: a markdown file that modifies the assistant's system prompt."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputStyle:
    """A Claude Code output style, written as markdown.

    The body is a system-prompt modifier, not additional context: it replaces Claude Code's
    built-in coding instructions unless the frontmatter sets ``keep-coding-instructions: true``.
    Frontmatter is authored verbatim in the file (as for :class:`~boff.artifacts.skill.Skill`),
    because no platform other than Claude reads output styles and there is nothing to translate.
    """

    name: str
    content: str
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on
