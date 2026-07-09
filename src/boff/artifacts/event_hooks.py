"""Event-hook artifact: normalized side-effect hooks deployed to both platforms.

Authors target a small closed set of normalized events (:data:`NORMALIZED_EVENTS`) with a
shell body. bun-off owns the platform glue (a Claude stdin-dispatcher and a generated
OpenCode plugin) so the same script runs on both, reading a normalized environment-variable
contract: ``BOFF_EVENT``, ``BOFF_TOOL``, ``BOFF_FILE``, ``BOFF_COMMAND``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeHook:
    """One platform's native binding for a normalized event.

    ``matcher`` carries the tool filter for platforms whose hook config groups handlers behind
    a matcher expression (Claude, Antigravity). ``tools`` carries the mapped tool names for
    OpenCode, whose generated plugin tests ``input.tool`` in JavaScript instead.
    """

    event: str
    matcher: str | None = None
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventMapping:
    """How one normalized event maps onto each platform's native hook.

    A platform absent from ``natives`` has no native equivalent for the event: its adapter
    skips such hooks with a warning rather than inventing a mapping.
    """

    natives: dict[str, NativeHook]

    def native_for(self, platform: str) -> NativeHook | None:
        """Return the platform's native binding, or None if the event does not exist there."""
        return self.natives.get(platform)


# Antigravity tool names, observed from live `agy` hook payloads. `write_to_file` is the edit
# tool; the other alternatives are file-mutating step types that never matched in testing but
# cost nothing to name, since an alternative that never fires is inert.
_AG_EDIT_TOOLS = "write_to_file|edit_notebook|propose_code|file_change"

# Closed, extensible set of normalized side-effect events. Adding one is a single entry here;
# adding a platform means giving it a key in each event it supports. Antigravity has no
# session lifecycle event at all, so it is absent from `session_start` and `session_end`.
EVENT_MAP: dict[str, EventMapping] = {
    "after_edit": EventMapping(
        {
            "claude": NativeHook("PostToolUse", matcher="Edit|Write"),
            "opencode": NativeHook("tool.execute.after", tools=("edit", "write")),
            "antigravity": NativeHook("PostToolUse", matcher=_AG_EDIT_TOOLS),
        }
    ),
    "after_bash": EventMapping(
        {
            "claude": NativeHook("PostToolUse", matcher="Bash"),
            "opencode": NativeHook("tool.execute.after", tools=("bash",)),
            "antigravity": NativeHook("PostToolUse", matcher="run_command"),
        }
    ),
    "on_finish": EventMapping(
        {
            "claude": NativeHook("Stop"),
            "opencode": NativeHook("session.idle"),
            "antigravity": NativeHook("Stop"),
        }
    ),
    "session_start": EventMapping(
        {
            "claude": NativeHook("SessionStart"),
            "opencode": NativeHook("session.start"),
        }
    ),
    "session_end": EventMapping(
        {
            "claude": NativeHook("SessionEnd"),
            "opencode": NativeHook("session.deleted"),
        }
    ),
}

NORMALIZED_EVENTS: frozenset[str] = frozenset(EVENT_MAP)


@dataclass(frozen=True)
class EventHook:
    """One normalized hook: a shell body bound to a normalized event."""

    name: str
    event: str
    command: str | None = None
    script: str | None = None
    script_content: str | None = None
    timeout: int | None = None
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if this hook targets the given platform."""
        return not self.available_on or platform in self.available_on

    @property
    def mapping(self) -> EventMapping:
        """Return the platform mapping for this hook's normalized event."""
        return EVENT_MAP[self.event]

    def native_for(self, platform: str) -> NativeHook | None:
        """Return the platform's native binding, or None if the event does not exist there."""
        return self.mapping.native_for(platform)


@dataclass(frozen=True)
class EventHooks:
    """Aggregate of every event hook, rendered as platform glue per platform."""

    hooks: tuple[EventHook, ...] = ()
    available_on: frozenset[str] = frozenset()

    def is_available_on(self, platform: str) -> bool:
        """Return True if the event-hook block targets the given platform."""
        return not self.available_on or platform in self.available_on

    def hooks_for(self, platform: str) -> tuple[EventHook, ...]:
        """Return the hooks that target the given platform, preserving authored order."""
        return tuple(hook for hook in self.hooks if hook.is_available_on(platform))
