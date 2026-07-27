"""Deploy-state tracking: records what boff owns so re-deploys can clean up.

A single ``.boff/state.json`` per project root records, for every ``(scope, owner)``,
the files boff created and the JSON key paths it injected into merged files. On the
next deploy :func:`reconcile` diffs the recorded state against the new operations and
emits :class:`DeleteOperation` / :class:`PruneKeysOperation` ops to remove what boff no
longer produces, leaving user-authored files and keys intact.

The file also records the *stack*: the ordered manifest references last deployed to each
scope. That record is what lets ``boff deploy --add`` / ``--remove`` mutate a deployment
without giving up the invariant that every deploy is authoritative: they rewrite the
reference list, then re-merge and re-deploy the whole stack.
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

# Schema version of the state file. 2 added the `stacks` block; a version-1 file loads as a
# version-2 state with no recorded stack, and is rewritten as version 2 on the next save.
STATE_VERSION = 2

# Prefix marking an owner key as a tool installer rather than a platform.
TOOL_PREFIX = "tool:"

START_MARKER = "# BEGIN boff-managed"
END_MARKER = "# END boff-managed"


@dataclass
class OwnerRecord:
    """What one owner (a platform or ``tool:<name>``) wrote during a deploy."""

    files: list[str] = field(default_factory=list[str])
    merged: dict[str, list[KeyPath]] = field(default_factory=dict[str, list[KeyPath]])


@dataclass
class DeployState:
    """Boff's recorded footprint, keyed by scope then owner, plus each scope's stack."""

    version: int = STATE_VERSION
    scopes: dict[str, dict[str, OwnerRecord]] = field(
        default_factory=dict[str, dict[str, OwnerRecord]]
    )
    stacks: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


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
    return f"{TOOL_PREFIX}{name}"


def recorded_owners(state: DeployState, scope: Scope) -> list[str]:
    """Owner keys boff has recorded for ``scope``."""
    return list(state.scopes.get(scope_key(scope), {}))


def recorded_platforms(state: DeployState, scope: Scope) -> list[str]:
    """Platform owners recorded for ``scope``, excluding the ``tool:<name>`` installers.

    This is what ``--platform`` falls back to, so re-deploying a workspace need not restate
    the platforms it was deployed to.
    """
    return [o for o in recorded_owners(state, scope) if not o.startswith(TOOL_PREFIX)]


def recorded_stack(state: DeployState, scope: Scope) -> list[str]:
    """The ordered manifest references last deployed to ``scope``."""
    return list(state.stacks.get(scope_key(scope), []))


def with_stack(state: DeployState, scope: Scope, refs: list[str]) -> DeployState:
    """Return ``state`` with ``scope``'s recorded stack replaced by ``refs``."""
    return _replace_scope(state, scope, stack=list(refs))


def without_owners(state: DeployState, scope: Scope, owners: list[str]) -> DeployState:
    """Return ``state`` with ``owners`` dropped from ``scope`` (their footprint is gone)."""
    key = scope_key(scope)
    remaining = {o: rec for o, rec in state.scopes.get(key, {}).items() if o not in owners}
    return _replace_scope(state, scope, owners=remaining)


def _replace_scope(
    state: DeployState,
    scope: Scope,
    *,
    owners: dict[str, OwnerRecord] | None = None,
    stack: list[str] | None = None,
) -> DeployState:
    """Return a copy of ``state`` with ``scope``'s owner records and/or stack replaced.

    Whichever argument is omitted is carried over unchanged, so neither caller can drop the
    other's half of the scope's record by accident.
    """
    key = scope_key(scope)
    return DeployState(
        version=state.version,
        scopes={**state.scopes, key: state.scopes.get(key, {}) if owners is None else owners},
        stacks={**state.stacks, key: state.stacks.get(key, []) if stack is None else stack},
    )


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

    next_state = _replace_scope(prior, scope, owners=next_scope)
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
    found = raw.get("version", 1)
    if found > STATE_VERSION:
        raise BoffError(
            f"state file {path} has schema version {found}, but this boff understands "
            f"version {STATE_VERSION}. Upgrade boff, or remove the file to re-deploy."
        )
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
    # A version-1 file has no `stacks` block: it loads with no recorded stack, which is what
    # `--add` / `--remove` report on, and is rewritten as version 2 by the next `save_state`.
    stacks = {skey: [str(ref) for ref in refs] for skey, refs in raw.get("stacks", {}).items()}
    return DeployState(version=STATE_VERSION, scopes=scopes, stacks=stacks)


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
        "version": STATE_VERSION,
        "stacks": {skey: list(refs) for skey, refs in state.stacks.items() if refs},
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


def _partition_merge_targets(
    scope_data: dict[str, OwnerRecord], root: Path
) -> tuple[set[str], set[str]]:
    """Split boff's MERGE targets into (fully owned, partially owned).

    A MERGE target is *fully owned* when every JSON leaf currently in the file on
    disk was written by boff, and *partially owned* when the file also holds keys
    boff never wrote. Compares ``leaf_paths`` of the deployed file against the
    key-paths recorded across all owners in ``scope_data``. A missing or
    unparseable file is neither: it is skipped rather than raising.
    """
    owned: dict[str, set[KeyPath]] = {}
    for rec in scope_data.values():
        for target, leaves in rec.merged.items():
            owned.setdefault(target, set()).update(leaves)

    full: set[str] = set()
    partial: set[str] = set()
    for target, owned_leaves in owned.items():
        try:
            parsed = json.loads((root / target).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        file_leaves = set(leaf_paths(parsed))
        if not file_leaves:
            continue
        if file_leaves <= owned_leaves:
            full.add(target)
        else:
            partial.add(target)
    return full, partial


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

    OVERWRITE files boff created are always listed. A MERGE target (``.mcp.json``,
    ``.claude/settings.json``, ``opencode.json``) is listed only when boff owns
    every key in it; a target that also holds user-authored keys is written
    commented-out, so ignoring the whole file (and hiding those keys from git) is
    an opt-in rather than a silent side effect.
    """
    if scope.kind != ScopeKind.WORKSPACE or scope.workspace_root is None:
        return
    root = scope.workspace_root
    if not _inside_work_tree(root):
        return

    skey = scope_key(scope)
    scope_data = state.scopes.get(skey, {})
    overwrite: set[str] = set()
    for owner_rec in scope_data.values():
        overwrite.update(owner_rec.files)

    full, partial = _partition_merge_targets(scope_data, root)
    active = sorted((overwrite | full) - partial)
    commented = sorted(partial - overwrite)

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

    if active or commented:
        if out and out[-1].strip():
            out.append("")
        out.append(START_MARKER)
        out.extend(f"/{f}" for f in active)
        if commented:
            out.append("#")
            out.append(
                "# These files also contain settings you edited, so boff leaves them tracked."
            )
            out.append("# Uncomment a line to ignore the whole file (including your own keys):")
            out.extend(f"#/{f}" for f in commented)
        out.append(END_MARKER)

    new_content = "\n".join(out) + "\n" if out else ""

    if new_content != content:
        if new_content or gitignore_path.exists():
            gitignore_path.write_text(new_content, encoding="utf-8")
