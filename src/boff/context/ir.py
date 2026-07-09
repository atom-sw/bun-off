"""Platform-neutral intermediate representation for portable context and plans.

A :class:`ContextBundle` captures everything bun-off knows how to move between
machines (export/import) or between frameworks (migrate). Providers collect a
bundle from one platform's on-disk state and materialize it back onto another.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectIdentity:
    """Identity used to re-key a project's state on a new machine.

    ``abs_path`` is the project's absolute path on the source machine.
    ``git_root_commit`` is the repository's root-commit hash: it is stable
    across clones and is OpenCode's project key.
    """

    abs_path: str
    git_root_commit: str | None = None
    remote: str | None = None
    branch: str | None = None


@dataclass(frozen=True)
class InstructionsDoc:
    """A context-instructions file (primary doc or an individual rule).

    ``relative_path`` is relative to the project root, so same-platform import
    restores it verbatim. ``kind`` is ``"primary"`` (CLAUDE.md / AGENTS.md) or
    ``"rule"``; migration uses it to remap files to the target platform.
    """

    relative_path: str
    content: str
    kind: str = "rule"


@dataclass(frozen=True)
class Plan:
    """A planning document. Markdown on every supported platform."""

    name: str
    content: str
    origin_path: str = ""


@dataclass(frozen=True)
class MemoryDoc:
    """A persistent-memory file. ``relative_path`` is relative to the memory dir."""

    relative_path: str
    content: str


@dataclass(frozen=True)
class TodoList:
    """A todo list, preserved as its raw on-disk payload."""

    name: str
    content: str


@dataclass(frozen=True)
class SessionRecord:
    """A raw conversation transcript, preserved verbatim for exact resume.

    Populated only when exporting with ``full=True``. ``relative_path`` is
    relative to the platform's per-project session directory.
    """

    relative_path: str
    content: str


@dataclass(frozen=True)
class HandoffDigest:
    """A deterministic, human-readable summary used to seed a target platform."""

    text: str


@dataclass(frozen=True)
class ContextBundle:
    """Everything portable about a project's context and plans on one platform."""

    platform: str
    project: ProjectIdentity
    instructions: list[InstructionsDoc] = field(default_factory=list[InstructionsDoc])
    plans: list[Plan] = field(default_factory=list[Plan])
    memory: list[MemoryDoc] = field(default_factory=list[MemoryDoc])
    todos: list[TodoList] = field(default_factory=list[TodoList])
    sessions: list[SessionRecord] = field(default_factory=list[SessionRecord])
    summary: HandoffDigest | None = None
