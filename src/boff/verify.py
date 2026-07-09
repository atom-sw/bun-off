"""Compare a planned deploy against what is actually on disk: the engine behind ``boff check``.

Verification is read-only. It never writes a file, never touches ``.boff/state.json``, and never
updates the workspace ``.gitignore``.

Two things make this more than "does each op's content match its target":

* **Several ops write one file.** ``.claude/settings.json`` receives one MERGE op per permissions
  block, settings block, and event-hook set. Checking each op against the file in isolation
  reports false drift the moment a later op overrides one of an earlier op's keys. So the ops are
  first folded, per target, into a single :data:`Expectation`, replayed in execution order with
  :func:`boff.executor._apply_file`'s semantics.
* **An empty op list is not a dropped artifact.** A renderer legitimately emits nothing when the
  manifest gave it nothing for this platform. Only a renderer declared ``@renders(..., drops=True)``
  means "this platform has no surface for this artifact"; :class:`boff.deploy.Support` carries that
  distinction here.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never

from boff.adapters import get_adapter
from boff.deploy import PlannedUnit, Support, fold_units
from boff.jsonutil import as_json_object, dumps_json, json_deep_merge
from boff.state import DeployState, leaf_paths, reconcile
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    Scope,
    ShellAction,
)

type KeyPath = tuple[str, ...]

_MISSING = object()
"""Sentinel for "this key path is absent", distinct from a stored ``None``."""


class Status(StrEnum):
    """The verdict for one artifact, target, or orphaned footprint."""

    OK = "ok"
    MISSING = "missing"
    DRIFTED = "drifted"
    STALE = "stale"
    DROPPED = "dropped"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"


FAILING: frozenset[Status] = frozenset({Status.MISSING, Status.DRIFTED, Status.STALE})
"""Statuses that make ``boff check`` exit non-zero."""


@dataclass(frozen=True)
class Finding:
    """One line of a check report."""

    status: Status
    owner: str
    label: str
    target: Path | None = None
    detail: str = ""


@dataclass(frozen=True)
class CheckReport:
    """Every finding from one ``boff check`` run."""

    findings: list[Finding]

    @property
    def failed(self) -> bool:
        """Return True if any finding warrants a non-zero exit."""
        return any(finding.status in FAILING for finding in self.findings)

    def counts(self) -> dict[Status, int]:
        """Tally findings per status, in :class:`Status` declaration order."""
        return {
            status: total
            for status in Status
            if (total := sum(f.status is status for f in self.findings))
        }


@dataclass(frozen=True)
class _Exact:
    """The target's content is fully determined by the plan: compare it whole."""

    content: str | bytes
    writer: FileOperation
    """The op that produced ``content``; the ops it superseded need no comparison."""


@dataclass(frozen=True)
class _Owned:
    """The target is only ever MERGE'd: boff owns leaf key paths, the user owns the rest."""

    value: dict[str, Any]


type Expectation = _Exact | _Owned


def probe(platforms: Iterable[str]) -> dict[str, str | None]:
    """Map each platform to the resolved path of its CLI binary, or None if not on PATH.

    Raises :class:`boff.errors.RegistryError` for an unregistered platform name.
    """
    return {p: shutil.which(get_adapter(p).layout.binary) for p in platforms}


def fold_expectations(ops: Iterable[Operation]) -> dict[Path, Expectation]:
    """Replay the file ops per target, yielding what each target should contain.

    ``ops`` must arrive in execution order. An OVERWRITE destroys whatever preceded it on that
    target, so only the ops from the last OVERWRITE onward determine the result. A target that
    never sees an OVERWRITE is a shared file: boff owns the keys it merged in and nothing else.
    """
    by_target: dict[Path, list[FileOperation]] = {}
    for op in ops:
        if isinstance(op, FileOperation):
            by_target.setdefault(op.target, []).append(op)
    return {target: _fold_target(target_ops) for target, target_ops in by_target.items()}


def verify(
    units: Sequence[PlannedUnit],
    prior: DeployState,
    scope: Scope,
    active_owners: Sequence[str],
) -> CheckReport:
    """Check every planned unit against the workspace, plus the footprint boff should have removed.

    ``active_owners`` mirrors ``plan_deploy``: the target platforms plus ``tool:<name>`` per tool
    installer in the manifest. Owners outside it keep their recorded footprint and report nothing.
    """
    forward_by_owner = fold_units(units)
    flat = [op for owner_ops in forward_by_owner.values() for op in owner_ops]
    expectations = fold_expectations(flat)

    findings = [f for unit in units for f in _unit_findings(unit, expectations)]
    findings.extend(_stale_findings(prior, forward_by_owner, active_owners, scope))
    return CheckReport(_dedupe(findings))


def _fold_target(ops: list[FileOperation]) -> Expectation:
    """Fold one target's ops, in execution order, into a single expectation."""
    last = _last_overwrite(ops)
    if last is None:
        # MERGE-only: whatever the user put in this file survives underneath boff's keys.
        owned: Any = {}
        for op in ops:
            owned = json_deep_merge(owned, json.loads(op.content))
        return _Owned(as_json_object(owned) or {})

    tail = ops[last:]
    if len(tail) == 1:
        return _Exact(tail[0].content, writer=tail[0])
    # Defensive: no target mixes strategies today, but an OVERWRITE followed by MERGEs would
    # leave exactly the JSON the executor writes back via `dumps_json`.
    merged: Any = json.loads(tail[0].content)
    for op in tail[1:]:
        merged = json_deep_merge(merged, json.loads(op.content))
    return _Exact(dumps_json(merged), writer=tail[0])


def _last_overwrite(ops: list[FileOperation]) -> int | None:
    """Index of the last OVERWRITE op, or None when every op merges."""
    for index in reversed(range(len(ops))):
        if ops[index].merge is MergeStrategy.OVERWRITE:
            return index
    return None


def _unit_findings(unit: PlannedUnit, expectations: dict[Path, Expectation]) -> list[Finding]:
    """Report on one planned unit: its support verdict, or its ops against disk."""
    match unit.support:
        case Support.DROPPED:
            return [_finding(unit, Status.DROPPED, detail="the platform has no target for it")]
        case Support.UNSUPPORTED:
            return [_finding(unit, Status.UNSUPPORTED)]
        case Support.SHADOWED:
            # The adapter renders an equivalent shape of the same manifest section instead.
            return []
        case Support.RENDERED:
            return [f for op in unit.ops for f in _op_findings(unit, op, expectations)]
        case _:
            assert_never(unit.support)


def _op_findings(
    unit: PlannedUnit, op: Operation, expectations: dict[Path, Expectation]
) -> list[Finding]:
    """Report on one operation of a unit."""
    match op:
        case FileOperation():
            return _file_findings(unit, op, expectations[op.target])
        case ShellAction() | DeleteOperation() | PruneKeysOperation():
            return [_finding(unit, Status.UNVERIFIABLE, detail=op.description)]
        case _:
            assert_never(op)


def _file_findings(unit: PlannedUnit, op: FileOperation, expect: Expectation) -> list[Finding]:
    """Compare one file operation's target against the folded expectation for that target."""
    if isinstance(expect, _Exact):
        if op.merge is MergeStrategy.OVERWRITE and op is not expect.writer:
            return []  # a later OVERWRITE replaced this op's content wholesale
        return _exact_findings(unit, op.target, expect.content)
    return _owned_findings(unit, op, expect)


def _exact_findings(unit: PlannedUnit, target: Path, content: str | bytes) -> list[Finding]:
    """Compare a whole file against the content the plan would write."""
    try:
        # `read_text` mirrors the executor's `write_text`, deliberately down to the encoding:
        # forcing UTF-8 here would report false drift wherever the executor wrote with a
        # different locale encoding. Both also translate newlines, so content holding a literal
        # \r would report permanent drift. No producer emits one today.
        actual = (
            target.read_bytes() if isinstance(content, bytes) else target.read_text()  # noqa: PLW1514
        )
    except FileNotFoundError:
        return [_finding(unit, Status.MISSING, target=target)]
    except OSError:
        return [_finding(unit, Status.MISSING, target=target, detail="not a readable file")]
    except UnicodeDecodeError:
        return [_finding(unit, Status.DRIFTED, target=target, detail="not valid UTF-8 text")]
    if actual != content:
        return [_finding(unit, Status.DRIFTED, target=target, detail="content differs")]
    return [_finding(unit, Status.OK, target=target)]


def _owned_findings(unit: PlannedUnit, op: FileOperation, expect: _Owned) -> list[Finding]:
    """Compare only the key paths boff merged into a shared JSON file.

    Keys the user added by hand are never reported: boff owns the leaves it wrote, nothing else.
    """
    target = op.target
    try:
        disk = as_json_object(json.loads(target.read_text()))
    except FileNotFoundError:
        return [_finding(unit, Status.MISSING, target=target)]
    except OSError:
        return [_finding(unit, Status.MISSING, target=target, detail="not a readable file")]
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A report, not an abort: `boff check` describes the workspace, it does not fix it.
        return [_finding(unit, Status.DRIFTED, target=target, detail="not valid JSON")]
    if disk is None:
        return [_finding(unit, Status.DRIFTED, target=target, detail="not a JSON object")]

    drifts: list[Finding] = []
    for path in leaf_paths(json.loads(op.content)):
        wanted = _resolve(expect.value, path)
        if wanted is _MISSING:
            continue  # a later op in this plan replaced the subtree holding this key
        found = _resolve(disk, path)
        dotted = ".".join(path)
        if found is _MISSING:
            drifts.append(
                _finding(unit, Status.DRIFTED, target=target, detail=f"missing key {dotted}")
            )
        elif found != wanted:
            drifts.append(
                _finding(
                    unit,
                    Status.DRIFTED,
                    target=target,
                    detail=f"key {dotted}: expected {wanted!r}, found {found!r}",
                )
            )
    return drifts or [_finding(unit, Status.OK, target=target)]


def _stale_findings(
    prior: DeployState,
    forward_by_owner: dict[str, list[Operation]],
    active_owners: Sequence[str],
    scope: Scope,
) -> list[Finding]:
    """Report the footprint a re-deploy would clean up and that is still on disk.

    Reconciling one owner at a time keeps each finding attributed. ``reconcile`` is pure, so the
    ``next_state`` it returns is discarded rather than saved.
    """
    findings: list[Finding] = []
    for owner in active_owners:
        cleanup, _ = reconcile(prior, forward_by_owner, [owner], scope)
        for op in cleanup:
            findings.extend(_cleanup_finding(owner, op))
    return findings


def _cleanup_finding(owner: str, op: Operation) -> list[Finding]:
    """Turn one cleanup op into a stale finding, but only if the workspace still shows it.

    The executor no-ops on an already-absent delete target or prune key, so reporting those
    would make ``check`` fail on a workspace where nothing is actually wrong.
    """
    match op:
        case DeleteOperation():
            if op.target.exists() or op.target.is_symlink():
                return [
                    Finding(
                        status=Status.STALE,
                        owner=owner,
                        label="orphaned file",
                        target=op.target,
                        detail="left by a previous deploy of another manifest",
                    )
                ]
        case PruneKeysOperation():
            surviving = _surviving_keys(op)
            if surviving:
                return [
                    Finding(
                        status=Status.STALE,
                        owner=owner,
                        label="orphaned keys",
                        target=op.target,
                        detail=", ".join(".".join(path) for path in surviving),
                    )
                ]
        case FileOperation() | ShellAction():
            pass
        case _:
            assert_never(op)
    return []


def _surviving_keys(op: PruneKeysOperation) -> list[KeyPath]:
    """Return the key paths a prune would remove that are still present on disk."""
    try:
        disk = as_json_object(json.loads(op.target.read_text()))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if disk is None:
        return []
    return [tuple(path) for path in op.key_paths if _resolve(disk, tuple(path)) is not _MISSING]


def _resolve(data: dict[str, Any], path: KeyPath) -> Any:
    """Return the value at ``path``, or the ``_MISSING`` sentinel when any segment is absent."""
    current: Any = data
    for segment in path:
        current_object = as_json_object(current)
        if current_object is None or segment not in current_object:
            return _MISSING
        current = current_object[segment]
    return current


def _finding(
    unit: PlannedUnit, status: Status, *, target: Path | None = None, detail: str = ""
) -> Finding:
    """Build a finding attributed to ``unit``."""
    return Finding(status=status, owner=unit.owner, label=unit.label, target=target, detail=detail)


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """Collapse findings that say the same thing about the same target, keeping the first.

    OpenCode re-emits its ``instructions`` glob registration once per rule, so a single drifted
    key in ``opencode.json`` otherwise surfaces once per rule under a different label. The key
    drifted, not each rule: report it once, attributed to the first unit that claimed it.
    """
    seen: dict[tuple[Status, str, Path | None, str], Finding] = {}
    for finding in findings:
        seen.setdefault((finding.status, finding.owner, finding.target, finding.detail), finding)
    return list(seen.values())
