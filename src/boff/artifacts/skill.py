"""Skill artifact: a markdown skill file delivered to a coding platform."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

SKILL_FILENAME = "SKILL.md"
"""The entry file of a skill, both in a manifest folder and once deployed.

Every supported platform discovers a skill as ``<name>/SKILL.md``, so the manifest loader and
the renderers agree on the one name.
"""


@dataclass(frozen=True)
class SkillFile:
    """A supporting file shipped alongside a skill's ``SKILL.md``.

    The bytes are copied verbatim: a supporting file is read by the assistant at run time,
    not parsed by the platform, so it may hold anything the skill's author wants to ship.
    """

    path: PurePosixPath
    """Location under the skill's own directory. Never ``SKILL.md`` itself."""

    content: bytes


@dataclass(frozen=True)
class Skill:
    """A platform skill, written as markdown.

    ``content`` is the ``SKILL.md`` body. ``files`` holds the supporting files of a
    directory-form skill, and is empty for one written as a single markdown file.
    """

    name: str
    content: str
    available_on: frozenset[str] = frozenset()
    files: tuple[SkillFile, ...] = ()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this artifact targets the given platform."""
        return not self.available_on or platform in self.available_on
