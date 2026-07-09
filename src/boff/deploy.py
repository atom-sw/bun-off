"""Pure ``Manifest > list[Operation]`` planner and the deploy clear/forward/cleanup plan.

:func:`plan_units` is the primitive: one :class:`PlannedUnit` per (owner, artifact / plugin /
tool-file set), keeping the attribution ``boff check`` needs to report per artifact.
:func:`deploy_plan` is a fold over it that buckets ops by owner and drops empty units.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, NamedTuple, assert_never

from boff.adapters import get_adapter
from boff.artifacts import (
    EQUIVALENT_SHAPES,
    Agent,
    Artifact,
    EventHooks,
    MCPServer,
    Permissions,
    Rule,
    Rules,
    Settings,
    Skill,
    SlashCommand,
)
from boff.manifest import Manifest
from boff.sources import get_source
from boff.state import DeployState, reconcile, recorded_owners, tool_owner, without_owners
from boff.tool_installers import get_tool_installer
from boff.types import DeleteOperation, Operation, Scope

if TYPE_CHECKING:
    from boff.adapters.base import PlatformAdapter


class Support(StrEnum):
    """How an adapter treats one artifact type."""

    RENDERED = "rendered"
    """The adapter renders this artifact type."""
    DROPPED = "dropped"
    """The adapter registers a warn-and-skip renderer: the platform has no surface for it."""
    SHADOWED = "shadowed"
    """The adapter renders an equivalent shape instead (see ``EQUIVALENT_SHAPES``)."""
    UNSUPPORTED = "unsupported"
    """The adapter has no renderer at all."""


@dataclass(frozen=True)
class PlannedUnit:
    """The ops one artifact, plugin, or tool-file set produced, and who owns them."""

    owner: str
    """The platform name, or ``tool:<name>`` for a tool installer."""
    label: str
    """A human-readable identifier, such as ``rule style`` or ``plugin say-hi``."""
    ops: list[Operation] = field(default_factory=list[Operation])
    platform: str | None = None
    """The target platform, or None for a tool installer (which is platform-agnostic)."""
    support: Support = Support.RENDERED


def artifact_label(artifact: Artifact) -> str:
    """Return a short human-readable identifier for an artifact."""
    label: str
    match artifact:
        case Rule():
            label = f"rule {artifact.name}"
        case Rules():
            label = "rules"
        case Skill():
            label = f"skill {artifact.name}"
        case SlashCommand():
            label = f"slash command {artifact.name}"
        case MCPServer():
            label = f"mcp server {artifact.name}"
        case Permissions():
            label = "permissions"
        case Agent():
            label = f"agent {artifact.name}"
        case Settings():
            label = "settings"
        case EventHooks():
            label = "event hooks"
        case _:
            assert_never(artifact)
    return label


def _support(adapter: PlatformAdapter, kind: type[Any]) -> Support:
    """Classify how ``adapter`` treats artifacts of type ``kind``."""
    if adapter.supports(kind):
        return Support.DROPPED if adapter.drops(kind) else Support.RENDERED
    shadowed = any(
        kind in group and any(adapter.supports(other) for other in group - {kind})
        for group in EQUIVALENT_SHAPES
    )
    return Support.SHADOWED if shadowed else Support.UNSUPPORTED


def _artifact_unit(
    adapter: PlatformAdapter, artifact: Artifact, platform: str, scope: Scope
) -> PlannedUnit:
    """Plan one artifact for one platform, recording how the adapter treats its type."""
    support = _support(adapter, type(artifact))
    ops = (
        adapter.render(artifact, platform=platform, scope=scope)
        if support in {Support.RENDERED, Support.DROPPED}
        else []
    )
    return PlannedUnit(
        owner=platform,
        label=artifact_label(artifact),
        ops=ops,
        platform=platform,
        support=support,
    )


def plan_units(manifest: Manifest, platforms: Iterable[str], scope: Scope) -> list[PlannedUnit]:
    """Plan one unit per (owner, artifact / plugin / tool-file set), in deploy order.

    Order is platform-major artifacts, then plugins, then tool files: the same order
    :func:`deploy_plan` has always emitted, which ``state._owner_record`` relies on when it
    records merged key paths.
    """
    platform_list = list(platforms)
    units: list[PlannedUnit] = []

    for platform in platform_list:
        adapter = get_adapter(platform)
        for artifact in manifest.iter_artifacts():
            if artifact.is_available_on(platform):
                units.append(_artifact_unit(adapter, artifact, platform, scope))

    for plugin in manifest.plugins:
        for platform, install in plugin.install.items():
            if platform in platform_list:
                source = get_source(install.source)
                units.append(
                    PlannedUnit(
                        owner=platform,
                        label=f"plugin {plugin.name}",
                        ops=source.install(install.spec, platform=platform, scope=scope),
                        platform=platform,
                    )
                )

    for name, paths in manifest.tool_files.items():
        installer = get_tool_installer(name)
        units.append(
            PlannedUnit(
                owner=tool_owner(name),
                label=name,
                ops=installer.install_files(list(paths), scope=scope),
            )
        )

    return units


def fold_units(units: Iterable[PlannedUnit]) -> dict[str, list[Operation]]:
    """Bucket units' ops by owner, skipping units that emitted nothing."""
    by_owner: dict[str, list[Operation]] = {}
    for unit in units:
        if unit.ops:
            by_owner.setdefault(unit.owner, []).extend(unit.ops)
    return by_owner


def deploy_plan(
    manifest: Manifest, platforms: Iterable[str], scope: Scope
) -> dict[str, list[Operation]]:
    """Plan all operations, bucketed by owner.

    Owners are platform names (``claude``, ``opencode``, ...) for adapter-rendered and
    plugin ops, and ``tool:<name>`` for tool-installer files. The buckets let cleanup be
    computed per ``(scope, owner)``.
    """
    return fold_units(plan_units(manifest, platforms, scope))


def wipe_ops(scope: Scope, platforms: list[str]) -> list[Operation]:
    """Ops that delete each platform's native config roots wholesale (destructive)."""
    ops: list[Operation] = []
    boundary = scope.workspace_root
    for platform in platforms:
        for root in get_adapter(platform).native_roots(scope):
            ops.append(
                DeleteOperation(
                    target=root,
                    description=f"wipe {platform} config {root}",
                    prune_until=boundary,
                )
            )
    return ops


def purge_ops(prior: DeployState, scope: Scope, owners: list[str]) -> list[Operation]:
    """Ops that remove the recorded footprint of ``owners`` (non-destructive)."""
    ops, _ = reconcile(prior, {}, owners, scope)
    return ops


def build_clear_ops(
    prior: DeployState, scope: Scope, owners: list[str], *, wipe: bool
) -> list[Operation]:
    """Build the ops that clear ``owners``: a destructive wipe or a non-destructive purge."""
    return wipe_ops(scope, owners) if wipe else purge_ops(prior, scope, owners)


class DeployPlan(NamedTuple):
    """The ops for one deploy invocation, split into phases, plus the next state."""

    clear: list[Operation]
    forward: list[Operation]
    cleanup: list[Operation]
    next_state: DeployState


def plan_deploy(  # noqa: PLR0913  (six cohesive planning inputs; splitting would obscure)
    manifest: Manifest,
    platforms: list[str],
    scope: Scope,
    prior: DeployState,
    *,
    wipe: bool,
    clean: bool,
) -> DeployPlan:
    """Compute the clear, forward, and cleanup ops for a deploy.

    ``wipe`` deletes the targeted platforms' native roots; ``clean`` purges boff's whole
    recorded footprint for the scope. Cleared owners are treated as empty when reconciling
    the forward pass, so their footprint is not double-removed.
    """
    active_owners = platforms + [tool_owner(name) for name in manifest.tool_files]
    cleared_owners: list[str] = []
    clear_ops: list[Operation] = []
    if wipe:
        cleared_owners = list(platforms)
        clear_ops = build_clear_ops(prior, scope, cleared_owners, wipe=True)
    elif clean:
        cleared_owners = recorded_owners(prior, scope)
        clear_ops = build_clear_ops(prior, scope, cleared_owners, wipe=False)

    forward_by_owner = deploy_plan(manifest, platforms, scope)
    forward_ops = [op for ops in forward_by_owner.values() for op in ops]
    recon_prior = without_owners(prior, scope, cleared_owners)
    cleanup_ops, next_state = reconcile(recon_prior, forward_by_owner, active_owners, scope)
    return DeployPlan(clear_ops, forward_ops, cleanup_ops, next_state)
