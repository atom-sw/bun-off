import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from boff.executor import execute
from boff.manifest import Manifest, load_manifest
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    Scope,
)

DeployOps = Callable[[Manifest, str, Scope], list[Operation]]

# The globs authored on the sample manifest's `style` rule, bound once so the rendered
# frontmatter assertion below cannot drift from the fixture.
_STYLE_GLOBS = ("**/*.md", "docs/**")
_STYLE_FRONTMATTER = (
    "---\n" + yaml.safe_dump({"paths": list(_STYLE_GLOBS)}, sort_keys=False).strip() + "\n---\n\n"
)


def test_execute_writes_all_expected_files(
    sample_manifest: Path,
    tmp_path: Path,
    workspace_scope: Scope,
    deploy_ops: DeployOps,
    lint_script: str,
) -> None:
    manifest = load_manifest(sample_manifest)
    execute(deploy_ops(manifest, "claude", workspace_scope))

    style = tmp_path / ".claude" / "rules" / "dev-essentials" / "style.md"
    assert style.is_file()
    assert style.read_text().startswith(_STYLE_FRONTMATTER)
    assert (tmp_path / ".claude" / "skills" / "format" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "commands" / "lint.md").is_file()
    assert (tmp_path / ".claude" / "agents" / "reviewer.md").is_file()
    assert (tmp_path / "greeting.txt").read_text() == "hello from the say-hi plugin\n"
    assert (
        (tmp_path / ".config" / "mise" / "conf.d" / "boff-0.toml")
        .read_bytes()
        .startswith(b"[tools]")
    )

    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert mcp == {
        "mcpServers": {
            "context7": {"command": "uvx", "args": ["context7-mcp"]},
            "tldr": {"command": "uvx", "args": ["llm-tldr"]},
        }
    }

    # The permissions block, settings passthrough, and event-hook block co-merge into one file.
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["permissions"] == {
        "allow": ["Read(./src/**)", "WebFetch(domain:github.com)"],
        "ask": ["Bash(git push *)", "Edit"],
        "deny": ["Bash(curl *)", "mcp__secret__*"],
    }
    assert settings["outputStyle"] == "Explanatory"
    assert settings["cleanupPeriodDays"] == 14
    matchers = [g["matcher"] for g in settings["hooks"]["PostToolUse"]]
    assert matchers == ["Bash", "Edit|Write"]

    assert (tmp_path / ".claude" / "hooks" / "_boff_dispatch.py").is_file()
    assert (tmp_path / ".claude" / "hooks" / "lint").read_text() == lint_script


def test_permissions_merge_preserves_unrelated_settings(
    sample_manifest: Path, tmp_path: Path, workspace_scope: Scope, deploy_ops: DeployOps
) -> None:
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"model": "opus", "permissions": {"deny": ["Bash(rm *)"]}}))
    manifest = load_manifest(sample_manifest)
    execute(deploy_ops(manifest, "claude", workspace_scope))
    merged = json.loads(target.read_text())
    assert merged["model"] == "opus"
    assert merged["permissions"]["allow"] == ["Read(./src/**)", "WebFetch(domain:github.com)"]
    assert merged["permissions"]["deny"] == ["Bash(curl *)", "mcp__secret__*"]


def test_merge_creates_file_when_target_missing(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    op = FileOperation(
        target=target,
        content=json.dumps({"a": 1, "nested": {"k": "v"}}),
        merge=MergeStrategy.MERGE,
    )
    execute([op])
    assert json.loads(target.read_text()) == {"a": 1, "nested": {"k": "v"}}


def test_merge_preserves_existing_unrelated_keys(tmp_path: Path) -> None:
    target = tmp_path / ".mcp.json"
    target.write_text(json.dumps({"mcpServers": {"user-added": {"command": "echo"}}}))
    op = FileOperation(
        target=target,
        content=json.dumps({"mcpServers": {"context7": {"command": "uvx"}}}),
        merge=MergeStrategy.MERGE,
    )
    execute([op])
    merged = json.loads(target.read_text())
    assert merged["mcpServers"]["user-added"] == {"command": "echo"}
    assert merged["mcpServers"]["context7"] == {"command": "uvx"}


def test_merge_combines_two_sequential_ops(tmp_path: Path) -> None:
    target = tmp_path / ".mcp.json"
    ops = [
        FileOperation(
            target=target,
            content=json.dumps({"mcpServers": {"a": {"command": "x"}}}),
            merge=MergeStrategy.MERGE,
        ),
        FileOperation(
            target=target,
            content=json.dumps({"mcpServers": {"b": {"command": "y"}}}),
            merge=MergeStrategy.MERGE,
        ),
    ]
    execute(ops)
    merged = json.loads(target.read_text())
    assert merged["mcpServers"] == {"a": {"command": "x"}, "b": {"command": "y"}}


def test_merge_later_op_wins_on_scalar_conflict(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"x": 1}))
    op = FileOperation(
        target=target,
        content=json.dumps({"x": 2}),
        merge=MergeStrategy.MERGE,
    )
    execute([op])
    assert json.loads(target.read_text()) == {"x": 2}


def test_merge_replaces_lists_rather_than_concatenating(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"args": ["old"]}))
    op = FileOperation(
        target=target,
        content=json.dumps({"args": ["new"]}),
        merge=MergeStrategy.MERGE,
    )
    execute([op])
    assert json.loads(target.read_text()) == {"args": ["new"]}


def test_merge_onto_non_dict_top_level_replaces_wholesale(tmp_path: Path) -> None:
    # A hand-edited target whose top level is valid JSON but not an object: the deep-merge
    # rule replaces non-dicts wholesale, so the incoming object wins rather than crashing.
    target = tmp_path / "config.json"
    target.write_text(json.dumps([1, 2, 3]))
    op = FileOperation(
        target=target,
        content=json.dumps({"mcpServers": {"a": {"command": "x"}}}),
        merge=MergeStrategy.MERGE,
    )
    execute([op])
    assert json.loads(target.read_text()) == {"mcpServers": {"a": {"command": "x"}}}


def test_merge_rejects_bytes_content(tmp_path: Path) -> None:
    op = FileOperation(
        target=tmp_path / "config.json",
        content=b'{"x": 1}',
        merge=MergeStrategy.MERGE,
    )
    with pytest.raises(ValueError, match="MERGE"):
        execute([op])


def test_delete_removes_file_and_prunes_empty_parents(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "rules" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    execute([DeleteOperation(target=target, prune_until=tmp_path)])
    assert not target.exists()
    assert not (tmp_path / ".claude").exists()  # emptied parents pruned
    assert tmp_path.is_dir()  # boundary preserved


def test_delete_keeps_nonempty_parent(tmp_path: Path) -> None:
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "x.md").write_text("x")
    (rules / "keep.md").write_text("keep")
    execute([DeleteOperation(target=rules / "x.md", prune_until=tmp_path)])
    assert (rules / "keep.md").is_file()


def test_delete_removes_directory_tree(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    (claude / "rules").mkdir(parents=True)
    (claude / "rules" / "x.md").write_text("x")
    execute([DeleteOperation(target=claude, prune_until=tmp_path)])
    assert not claude.exists()
    assert tmp_path.is_dir()


def test_prune_keys_removes_boff_keys_and_keeps_user_keys(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Read"]}}))
    execute([PruneKeysOperation(target=target, key_paths=[("permissions", "allow")])])
    data = json.loads(target.read_text())
    assert data == {"model": "opus"}  # emptied 'permissions' container garbage-collected


def test_prune_keys_missing_file_is_noop(tmp_path: Path) -> None:
    execute([PruneKeysOperation(target=tmp_path / "nope.json", key_paths=[("a",)])])
