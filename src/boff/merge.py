"""Merge resolved manifests for ``extends``: child overrides parent (last-wins)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from boff.artifacts import EventHook, EventHooks, PermissionRule, Permissions, Settings
from boff.jsonutil import json_deep_merge
from boff.manifest import Manifest
from boff.types import Named

_log = logging.getLogger(__name__)


def merge_manifests(parents: list[Manifest], child: Manifest) -> Manifest:
    """Merge ``parents`` (in order) under ``child``; later definitions win.

    Named artifacts override by name (warning on each shadow). Permission rules, lifecycle
    hooks, and tool-installer file lists (such as ``mise:``) concatenate with de-duplication.
    Settings deep-merge per platform. ``meta`` is the child's own (identity is not inherited);
    ``root`` is the child's.
    """
    chain = [*parents, child]
    return Manifest(
        root=child.root,
        meta=child.meta,
        extends=child.extends,
        rules=_merge_named(chain, lambda m: m.rules, "rules"),
        skills=_merge_named(chain, lambda m: m.skills, "skills"),
        slash_commands=_merge_named(chain, lambda m: m.slash_commands, "slash_commands"),
        mcp_servers=_merge_named(chain, lambda m: m.mcp_servers, "mcp_servers"),
        plugins=_merge_named(chain, lambda m: m.plugins, "plugins"),
        tool_files=_merge_tool_files(chain),
        pre_install=_merge_hook_paths(chain, lambda m: m.pre_install),
        post_install=_merge_hook_paths(chain, lambda m: m.post_install),
        permissions=_merge_permissions(chain),
        agents=_merge_named(chain, lambda m: m.agents, "agents"),
        settings=_merge_settings(chain),
        event_hooks=_merge_event_hooks(chain),
    )


def _merge_named[T: Named](
    chain: list[Manifest], get: Callable[[Manifest], tuple[T, ...]], label: str
) -> tuple[T, ...]:
    """Merge name-keyed artifact tuples; later entries override earlier ones by ``name``.

    ``get`` selects the manifest field to merge and ``label`` names it in the shadow warning.
    """
    out: dict[str, T] = {}
    for manifest in chain:
        for item in get(manifest):
            if item.name in out:
                _log.warning(
                    "extends: %s '%s' from %s overrides an inherited definition",
                    label,
                    item.name,
                    manifest.root,
                )
            out[item.name] = item
    return tuple(out.values())


def _merge_tool_files(chain: list[Manifest]) -> dict[str, tuple[Path, ...]]:
    """Union each installer's file list across the chain, deduping by path.

    Parent paths precede child paths (chain order), so an installer sees the full
    inherited toolchain plus the child's additions. Dedup is by resolved path.
    """
    out: dict[str, list[Path]] = {}
    for manifest in chain:
        for tool, paths in manifest.tool_files.items():
            bucket = out.setdefault(tool, [])
            for path in paths:
                if path not in bucket:
                    bucket.append(path)
    return {tool: tuple(paths) for tool, paths in out.items()}


def _merge_hook_paths(
    chain: list[Manifest], get: Callable[[Manifest], tuple[Path, ...]]
) -> tuple[Path, ...]:
    """Merge hook script paths across the chain by name (file stem), last definer wins.

    ``get`` selects the lifecycle phase's field. An inherited-only hook keeps the parent's
    script path; a hook the child redefines by the same name resolves to the child's script.
    Order follows first appearance, matching the previous concatenate-and-dedup behavior.
    """
    out: dict[str, Path] = {}
    for manifest in chain:
        for path in get(manifest):
            out[path.stem] = path
    return tuple(out.values())


def _merge_permissions(chain: list[Manifest]) -> Permissions | None:
    """Concatenate permission rules across the chain, dropping duplicate rules."""
    rules: dict[PermissionRule, None] = {}
    for manifest in chain:
        if manifest.permissions is not None:
            for rule in manifest.permissions.rules:
                rules[rule] = None
    return Permissions(rules=tuple(rules)) if rules else None


def _merge_settings(chain: list[Manifest]) -> Settings | None:
    """Deep-merge each platform's settings block across the chain, later blocks winning."""
    merged: dict[str, dict[str, Any]] = {}
    for manifest in chain:
        if manifest.settings is None:
            continue
        for platform, block in manifest.settings.raw.items():
            existing = merged.get(platform, {})
            merged[platform] = json_deep_merge(existing, block)
    return Settings(raw=merged, available_on=frozenset(merged)) if merged else None


def _merge_event_hooks(chain: list[Manifest]) -> EventHooks | None:
    """Merge event hooks by name across the chain, the last definer winning (warns on shadow)."""
    hooks: dict[str, EventHook] = {}
    for manifest in chain:
        if manifest.event_hooks is None:
            continue
        for hook in manifest.event_hooks.hooks:
            if hook.name in hooks:
                _log.warning(
                    "extends: event hook '%s' from %s overrides an inherited definition",
                    hook.name,
                    manifest.root,
                )
            hooks[hook.name] = hook
    return EventHooks(hooks=tuple(hooks.values())) if hooks else None
