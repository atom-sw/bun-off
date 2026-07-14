"""Deploy-state tracking: records what boff owns so re-deploys can clean up.

A single ``.boff/state.json`` per project root records, for every ``(scope, owner)``,
the files boff created and the JSON key paths it injected into merged files. On the
next deploy :func:`reconcile` diffs the recorded state against the new operations and
emits :class:`DeleteOperation` / :class:`PruneKeysOperation` ops to remove what boff no
longer produces, leaving user-authored files and keys intact.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from boff.errors import BoffError
from boff.jsonutil import as_json_object, dumps_json
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    Scope,
    ScopeKind,
)

KeyPath = tuple[str, ...]

# boff's per-project state lives at ``<root>/.boff/state.json``.
STATE_DIR = ".boff"
STATE_FILENAME = "state.json"

START_MARKER = "# BEGIN boff-managed"
END_MARKER = "# END boff-managed"


@dataclass
class OwnerRecord:
    """What one owner (a platform or ``tool:<name>``) wrote during a deploy."""

    files: list[str] = field(default_factory=list[str])
    merged: dict[str, list[KeyPath]] = field(default_factory=dict[str, list[KeyPath]])


@dataclass
class DeployState:
    """Boff's recorded footprint, keyed by scope then owner."""

    version: int = 1
    scopes: dict[str, dict[str, OwnerRecord]] = field(
        default_factory=dict[str, dict[str, OwnerRecord]]
    )


def leaf_paths(obj: Any, prefix: KeyPath = ()) -> list[KeyPath]:
    """Enumerate leaf key paths of ``obj``, recursing into dicts only.

    Mirrors the executor's deep-merge semantics: dicts recurse, while lists and
    scalars are owned wholesale at their key path.
    """
    obj_object = as_json_object(obj)
    if obj_object:
        paths: list[KeyPath] = []
        for key, value in obj_object.items():
            paths.extend(leaf_paths(value, (*prefix, str(key))))
        return paths
    return [prefix] if prefix else []


def scope_key(scope: Scope) -> str:
    """Stable key for a scope within a state file."""
    return str(scope.kind)


def tool_owner(name: str) -> str:
    """Owner key for a tool installer's files: ``tool:<name>``."""
    return f"tool:{name}"


def recorded_owners(state: DeployState, scope: Scope) -> list[str]:
    """Owner keys boff has recorded for ``scope``."""
    return list(state.scopes.get(scope_key(scope), {}))


def without_owners(state: DeployState, scope: Scope, owners: list[str]) -> DeployState:
    """Return ``state`` with ``owners`` dropped from ``scope`` (their footprint is gone)."""
    key = scope_key(scope)
    remaining = {o: rec for o, rec in state.scopes.get(key, {}).items() if o not in owners}
    return DeployState(version=state.version, scopes={**state.scopes, key: remaining})


def _scope_root(scope: Scope) -> Path:
    """Resolve the root directory for a given scope, defaulting to the user's home."""
    return scope.workspace_root if scope.workspace_root is not None else Path.home()


def state_path(scope: Scope) -> Path:
    """Location of the state file for ``scope``: ``<root>/.boff/state.json``."""
    return _scope_root(scope) / STATE_DIR / STATE_FILENAME


def _rel(target: Path, root: Path) -> str:
    """Convert an absolute target path to a string relative to the given root."""
    return Path(os.path.relpath(target, root)).as_posix()


def _abs(root: Path, rel: str) -> Path:
    """Resolve a relative path string against the given root directory."""
    return root / rel


def _owner_record(ops: list[Operation], root: Path) -> OwnerRecord:
    """Summarize the files and merged-key provenance produced by one owner's ops."""
    record = OwnerRecord()
    for op in ops:
        if not isinstance(op, FileOperation):
            continue
        rel = _rel(op.target, root)
        if op.merge is MergeStrategy.MERGE:
            if isinstance(op.content, bytes):
                continue
            leaves = record.merged.setdefault(rel, [])
            for path in leaf_paths(json.loads(op.content)):
                if path not in leaves:
                    leaves.append(path)
        elif rel not in record.files:
            record.files.append(rel)
    return record


def reconcile(
    prior: DeployState,
    forward_by_owner: dict[str, list[Operation]],
    active_owners: list[str],
    scope: Scope,
) -> tuple[list[Operation], DeployState]:
    """Diff prior state against the new ops; return cleanup ops and the next state.

    For each active owner: delete files boff created but no longer produces, and prune
    merged keys boff injected but no longer produces. Owners absent from
    ``active_owners`` are carried over unchanged.
    """
    root = _scope_root(scope)
    key = scope_key(scope)
    prior_scope = prior.scopes.get(key, {})
    next_scope: dict[str, OwnerRecord] = dict(prior_scope)
    ops: list[Operation] = []

    for owner in active_owners:
        new_record = _owner_record(forward_by_owner.get(owner, []), root)
        old_record = prior_scope.get(owner)
        if old_record is not None:
            new_files = set(new_record.files)
            for rel in old_record.files:
                if rel not in new_files:
                    ops.append(
                        DeleteOperation(
                            target=_abs(root, rel),
                            description=f"remove orphaned {owner} file {rel}",
                            prune_until=root,
                        )
                    )
            for target, old_leaves in old_record.merged.items():
                new_leaves = set(new_record.merged.get(target, []))
                prune = [p for p in old_leaves if p not in new_leaves]
                if prune:
                    ops.append(
                        PruneKeysOperation(
                            target=_abs(root, target),
                            key_paths=prune,
                            description=f"prune orphaned {owner} keys from {target}",
                        )
                    )
        next_scope[owner] = new_record

    next_state = DeployState(version=prior.version, scopes={**prior.scopes, key: next_scope})
    return ops, next_state


def load_state(path: Path) -> DeployState:
    """Read a state file, returning empty state if it does not exist."""
    if not path.is_file():
        return DeployState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BoffError(
            f"corrupt state file {path}: {exc}. Fix or remove it to re-deploy."
        ) from exc
    scopes: dict[str, dict[str, OwnerRecord]] = {}
    for skey, owners in raw.get("scopes", {}).items():
        scopes[skey] = {
            owner: OwnerRecord(
                files=list(rec.get("files", [])),
                merged={
                    target: [tuple(p) for p in paths]
                    for target, paths in rec.get("merged", {}).items()
                },
            )
            for owner, rec in owners.items()
        }
    return DeployState(version=raw.get("version", 1), scopes=scopes)


def _write_state_gitignore(state_dir: Path) -> None:
    """Write the self-ignore ``.gitignore`` that keeps ``.boff`` out of version control."""
    (state_dir / ".gitignore").write_text("*\n", encoding="utf-8")


def save_state(state: DeployState, path: Path) -> None:
    """Write ``state`` to ``path``.

    Side effect: also writes ``<state_dir>/.gitignore`` so the ``.boff`` directory
    self-ignores from git (via :func:`_write_state_gitignore`).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_state_gitignore(path.parent)
    payload = {
        "version": state.version,
        "scopes": {
            skey: {
                owner: {
                    "files": rec.files,
                    "merged": {
                        target: [list(p) for p in paths] for target, paths in rec.merged.items()
                    },
                }
                for owner, rec in owners.items()
            }
            for skey, owners in state.scopes.items()
        },
    }
    path.write_text(dumps_json(payload), encoding="utf-8")


def _inside_work_tree(root: Path) -> bool:
    """Return True if ``root`` or any ancestor is a git work tree.

    A git work tree is marked by ``.git`` (a directory in a normal clone, or a
    file in a worktree or submodule), so this accepts either shape at any level.
    """
    return any((p / ".git").exists() for p in (root, *root.parents))


def update_workspace_gitignore(state: DeployState, scope: Scope) -> None:
    """Update the workspace .gitignore with a managed block of boff's owned files.

    Gathers the exact file paths tracked by boff across all owners in ``scope``,
    and inserts or replaces a block demarcated by ``# BEGIN boff-managed`` and
    ``# END boff-managed`` in the workspace directory's ``.gitignore``. Does
    nothing unless the workspace directory or an ancestor is a git work tree.

    The ``.gitignore`` is written in the workspace directory itself (the deploy
    root), which may be a subfolder nested below the repository root. Its entries
    are anchored (``/<path>``) to that directory, so git's nested-``.gitignore``
    semantics apply them to the deployed files regardless of how deep the
    subfolder sits.
    """
    if scope.kind != ScopeKind.WORKSPACE or scope.workspace_root is None:
        return
    root = scope.workspace_root
    if not _inside_work_tree(root):
        return

    skey = scope_key(scope)
    scope_data = state.scopes.get(skey, {})
    files: set[str] = set()
    for owner_rec in scope_data.values():
        files.update(owner_rec.files)

    gitignore_path = root / ".gitignore"
    content = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""

    lines = content.splitlines()
    out: list[str] = []
    in_block = False

    for line in lines:
        if line.strip() == START_MARKER:
            in_block = True
            continue
        if line.strip() == END_MARKER:
            in_block = False
            continue
        if not in_block:
            out.append(line)

    if files:
        if out and out[-1].strip():
            out.append("")
        out.append(START_MARKER)
        for f in sorted(files):
            out.append(f"/{f}")
        out.append(END_MARKER)

    new_content = "\n".join(out) + "\n" if out else ""

    if new_content != content:
        if new_content or gitignore_path.exists():
            gitignore_path.write_text(new_content, encoding="utf-8")
