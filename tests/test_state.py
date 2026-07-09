import json
from pathlib import Path
from typing import Any

import pytest

from boff.state import (
    DeployState,
    OwnerRecord,
    leaf_paths,
    load_state,
    reconcile,
    save_state,
    state_path,
)
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    Scope,
    ScopeKind,
)


def _overwrite(target: Path) -> FileOperation:
    return FileOperation(target=target, content="x", merge=MergeStrategy.OVERWRITE)


def _merge(target: Path, payload: dict[str, Any]) -> FileOperation:
    return FileOperation(target=target, content=json.dumps(payload), merge=MergeStrategy.MERGE)


def test_leaf_paths_recurses_dicts_only() -> None:
    obj = {"a": 1, "b": {"c": 2, "d": 3}, "x": [1, 2]}
    assert leaf_paths(obj) == [("a",), ("b", "c"), ("b", "d"), ("x",)]


def test_leaf_paths_empty_dict_yields_nothing() -> None:
    assert leaf_paths({}) == []


def test_reconcile_deletes_orphaned_files(tmp_path: Path, workspace_scope: Scope) -> None:
    scope = workspace_scope
    prior = DeployState(scopes={"workspace": {"claude": OwnerRecord(files=["a.md", "b.md"])}})
    forward: dict[str, list[Operation]] = {"claude": [_overwrite(tmp_path / "a.md")]}

    ops, next_state = reconcile(prior, forward, ["claude"], scope)

    deletes = [op for op in ops if isinstance(op, DeleteOperation)]
    assert [op.target for op in deletes] == [tmp_path / "b.md"]
    assert deletes[0].prune_until == tmp_path
    assert next_state.scopes["workspace"]["claude"].files == ["a.md"]


def test_reconcile_prunes_orphaned_merge_keys(tmp_path: Path, workspace_scope: Scope) -> None:
    scope = workspace_scope
    settings = tmp_path / ".claude" / "settings.json"
    prior = DeployState(
        scopes={
            "workspace": {
                "claude": OwnerRecord(
                    merged={".claude/settings.json": [("permissions", "allow"), ("model",)]}
                )
            }
        }
    )
    forward: dict[str, list[Operation]] = {
        "claude": [_merge(settings, {"permissions": {"allow": ["Read"]}})]
    }

    ops, _ = reconcile(prior, forward, ["claude"], scope)

    prunes = [op for op in ops if isinstance(op, PruneKeysOperation)]
    assert len(prunes) == 1
    assert prunes[0].target == settings
    assert prunes[0].key_paths == [("model",)]


def test_reconcile_preserves_inactive_owners(tmp_path: Path, workspace_scope: Scope) -> None:
    scope = workspace_scope
    prior = DeployState(
        scopes={
            "workspace": {
                "claude": OwnerRecord(files=["a.md"]),
                "opencode": OwnerRecord(files=["b.md"]),
            }
        }
    )
    forward: dict[str, list[Operation]] = {"claude": [_overwrite(tmp_path / "a.md")]}

    ops, next_state = reconcile(prior, forward, ["claude"], scope)

    assert ops == []
    assert next_state.scopes["workspace"]["opencode"].files == ["b.md"]


def test_reconcile_cleans_active_owner_that_drops_everything(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    scope = workspace_scope
    prior = DeployState(scopes={"workspace": {"claude": OwnerRecord(files=["a.md"])}})

    ops, next_state = reconcile(prior, {}, ["claude"], scope)

    deletes = [op for op in ops if isinstance(op, DeleteOperation)]
    assert [op.target for op in deletes] == [tmp_path / "a.md"]
    assert next_state.scopes["workspace"]["claude"].files == []


def test_state_round_trip(workspace_scope: Scope) -> None:
    scope = workspace_scope
    state = DeployState(
        scopes={
            "workspace": {
                "claude": OwnerRecord(
                    files=["x.md"], merged={".claude/settings.json": [("model",)]}
                )
            }
        }
    )
    path = state_path(scope)
    save_state(state, path)

    assert (path.parent / ".gitignore").read_text() == "*\n"
    loaded = load_state(path)
    rec = loaded.scopes["workspace"]["claude"]
    assert rec.files == ["x.md"]
    assert rec.merged == {".claude/settings.json": [("model",)]}


def test_load_state_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.json").scopes == {}


def test_state_path_for_global_scope_roots_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A global scope has no workspace_root, so state lives under the user's home.
    monkeypatch.setenv("HOME", str(tmp_path))
    scope = Scope(kind=ScopeKind.GLOBAL, workspace_root=None)
    assert state_path(scope) == tmp_path / ".boff" / "state.json"
