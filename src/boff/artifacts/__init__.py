"""Artifact types: declarative units a manifest can deploy."""

from typing import Any

from boff.artifacts.agent import Agent
from boff.artifacts.event_hooks import (
    EVENT_MAP,
    NORMALIZED_EVENTS,
    EventHook,
    EventHooks,
    EventMapping,
    NativeHook,
)
from boff.artifacts.mcp_server import MCPServer
from boff.artifacts.permissions import Action, PermissionRule, Permissions
from boff.artifacts.rule import Rule, Rules
from boff.artifacts.settings import RESERVED_KEYS, Settings
from boff.artifacts.skill import Skill
from boff.artifacts.slash_command import SlashCommand

type Artifact = (
    Rule | Rules | Skill | SlashCommand | MCPServer | Permissions | Agent | Settings | EventHooks
)
"""Every artifact type ``Manifest.iter_artifacts`` can yield.

The union is closed, so a ``match`` over it plus ``assert_never`` fails to type-check when a
tenth artifact type appears.
"""

# Artifact shapes describing the same manifest section in different forms. An adapter renders
# exactly one member of each group: Claude and OpenCode read a rules *directory* (`Rule`),
# Antigravity reads one instructions file (`Rules`). The member an adapter does not render is
# unused, not unsupported -- `boff check` must not report it as missing.
EQUIVALENT_SHAPES: tuple[frozenset[type[Any]], ...] = (frozenset({Rule, Rules}),)

__all__ = [
    "EQUIVALENT_SHAPES",
    "EVENT_MAP",
    "NORMALIZED_EVENTS",
    "RESERVED_KEYS",
    "Action",
    "Agent",
    "Artifact",
    "EventHook",
    "EventHooks",
    "EventMapping",
    "MCPServer",
    "NativeHook",
    "PermissionRule",
    "Permissions",
    "Rule",
    "Rules",
    "Settings",
    "Skill",
    "SlashCommand",
]
