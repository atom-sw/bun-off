"""Skill artifact: a markdown skill file delivered to a coding platform."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from boff.jsonutil import as_json_object

SKILL_FILENAME = "SKILL.md"
"""The entry file of a skill, both in a manifest folder and once deployed.

Every supported platform discovers a skill as ``<name>/SKILL.md``, so the manifest loader and
the renderers agree on the one name.
"""

EXCLUDED_DIRS = frozenset(
    {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".ipynb_checkpoints"}
)
"""Directories a skill folder collects but never means to ship, skipped at any depth."""

EXCLUDED_GLOBS = ("*.py[co]", ".DS_Store", "Thumbs.db", "*.sw[po]", "*~", "*.orig", "*.rej")
"""Filename patterns of build and editor droppings, matched against the file's own name."""


REQUIRED_FRONTMATTER = ("name", "description")
"""Frontmatter keys every platform needs to discover a skill.

A ``SKILL.md`` missing either is not a degraded skill, it is an inert one: Claude Code,
OpenCode, and Antigravity all skip such a file without complaint, so a deploy would report
success over a skill that never loads. The manifest loader rejects it instead.
"""


def parse_frontmatter(content: str) -> dict[str, Any] | None:
    """Return the YAML frontmatter block of ``content``, or None if it carries none.

    A frontmatter block opens on the very first line with ``---`` and closes on the next line
    that is exactly ``---``. Returns an empty mapping for a block that is empty or does not
    parse to a mapping, which callers treat the same as missing keys.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return as_json_object(yaml.safe_load("\n".join(lines[1:index]))) or {}
    # An opening fence with no closing one is not a frontmatter block.
    return None


def ships(path: PurePosixPath) -> bool:
    """Return True if a path under a skill folder belongs in the deployed skill.

    Tooling run inside a skill folder leaves artifacts behind: running a template's tests once
    writes ``templates/__pycache__/*.pyc``. Those are invisible when a bundle is fetched from
    git, since they are untracked, so a local manifest path would otherwise copy them into
    every user's skills directory without the author ever seeing them.
    """
    if EXCLUDED_DIRS.intersection(path.parts[:-1]):
        return False
    return not any(path.match(pattern) for pattern in EXCLUDED_GLOBS)


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
