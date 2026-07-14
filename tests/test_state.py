import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from boff.state import (
    END_MARKER,
    START_MARKER,
    DeployState,
    OwnerRecord,
    leaf_paths,
    load_state,
    reconcile,
    save_state,
    state_path,
    update_workspace_gitignore,
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


_OWNED = ".claude/settings.json"


def _state_owning(rel: str) -> DeployState:
    """State where the ``claude`` owner tracks a single workspace file."""
    return DeployState(scopes={"workspace": {"claude": OwnerRecord(files=[rel])}})


@pytest.mark.parametrize(
    ("marker", "nested", "expect_block"),
    [
        pytest.param("dir", True, True, id="git_dir_at_repo_root_deploy_in_subfolder"),
        pytest.param("file", True, True, id="git_file_worktree_deploy_in_subfolder"),
        pytest.param("dir", False, True, id="git_dir_at_deploy_root"),
        pytest.param(None, True, False, id="no_git_ancestor_writes_no_block"),
    ],
)
def test_update_workspace_gitignore_detects_ancestor_work_tree(
    tmp_path: Path,
    make_scope: Callable[[Path], Scope],
    marker: str | None,
    nested: bool,
    expect_block: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    if marker == "dir":
        (repo / ".git").mkdir()
    elif marker == "file":
        (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    deploy_dir = repo / "sub" if nested else repo
    deploy_dir.mkdir(parents=True, exist_ok=True)

    update_workspace_gitignore(_state_owning(_OWNED), make_scope(deploy_dir))

    gitignore = deploy_dir / ".gitignore"
    if expect_block:
        content = gitignore.read_text(encoding="utf-8")
        assert START_MARKER in content
        assert END_MARKER in content
        assert f"/{_OWNED}" in content
    else:
        assert not gitignore.exists() or START_MARKER not in gitignore.read_text()


def test_update_workspace_gitignore_writes_nested_not_repo_root(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    # A subfolder deploy writes its own nested .gitignore; the repo root is untouched.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "sub"
    sub.mkdir()

    update_workspace_gitignore(_state_owning(_OWNED), make_scope(sub))

    assert START_MARKER in (sub / ".gitignore").read_text(encoding="utf-8")
    assert not (repo / ".gitignore").exists()


def _git_repo(tmp_path: Path) -> Path:
    """A directory that is a git work tree (has a ``.git`` dir)."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _managed_block_lines(gitignore: Path) -> list[str]:
    """Return the lines between the boff markers, or ``[]`` if there is no block."""
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if START_MARKER not in text:
        return []
    return text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0].splitlines()


def _merge_state(target: str, leaves: list[tuple[str, ...]]) -> DeployState:
    """State where the ``claude`` owner MERGE-owns ``leaves`` of ``target``."""
    return DeployState(scopes={"workspace": {"claude": OwnerRecord(merged={target: leaves})}})


def test_gitignore_ignores_fully_boff_owned_merge_target(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    # Every key in the file was written by boff, so the whole file is ignored.
    repo = _git_repo(tmp_path)
    target = ".mcp.json"
    content = {"mcpServers": {"context7": {"command": "x"}}}
    (repo / target).write_text(json.dumps(content), encoding="utf-8")

    update_workspace_gitignore(_merge_state(target, leaf_paths(content)), make_scope(repo))

    lines = _managed_block_lines(repo / ".gitignore")
    assert f"/{target}" in lines
    assert f"#/{target}" not in lines


def test_gitignore_comments_partially_owned_merge_target(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    # The file also holds a user key boff never wrote, so boff leaves it tracked
    # and only comments it out.
    repo = _git_repo(tmp_path)
    target = ".claude/settings.json"
    boff_block: dict[str, Any] = {"hooks": {"after_edit": []}}
    (repo / ".claude").mkdir()
    (repo / target).write_text(json.dumps({**boff_block, "model": "sonnet"}), encoding="utf-8")

    update_workspace_gitignore(_merge_state(target, leaf_paths(boff_block)), make_scope(repo))

    lines = _managed_block_lines(repo / ".gitignore")
    assert f"#/{target}" in lines
    assert f"/{target}" not in lines
    assert any("Uncomment a line to ignore" in line for line in lines)


def test_gitignore_mixes_overwrite_files_and_owned_merge_targets(
    tmp_path: Path, make_scope: Callable[[Path], Scope]
) -> None:
    repo = _git_repo(tmp_path)
    rule = ".claude/rules/style.md"
    target = ".mcp.json"
    content = {"mcpServers": {"context7": {"command": "x"}}}
    (repo / target).write_text(json.dumps(content), encoding="utf-8")
    state = DeployState(
        scopes={
            "workspace": {"claude": OwnerRecord(files=[rule], merged={target: leaf_paths(content)})}
        }
    )

    update_workspace_gitignore(state, make_scope(repo))

    lines = _managed_block_lines(repo / ".gitignore")
    assert f"/{rule}" in lines
    assert f"/{target}" in lines


@pytest.mark.parametrize("content", [None, "{ not json"], ids=["missing_file", "malformed_json"])
def test_gitignore_skips_unreadable_merge_target(
    tmp_path: Path, make_scope: Callable[[Path], Scope], content: str | None
) -> None:
    # A merge target boff cannot read is neither ignored nor commented, and never raises.
    repo = _git_repo(tmp_path)
    target = ".mcp.json"
    if content is not None:
        (repo / target).write_text(content, encoding="utf-8")

    update_workspace_gitignore(_merge_state(target, [("mcpServers", "context7")]), make_scope(repo))

    lines = _managed_block_lines(repo / ".gitignore")
    assert f"/{target}" not in lines
    assert f"#/{target}" not in lines
