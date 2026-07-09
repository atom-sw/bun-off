"""Executor: apply the planned operations to the filesystem."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any, assert_never

from boff.errors import BoffError
from boff.jsonutil import dumps_json, json_deep_merge
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    ShellAction,
)

_log = logging.getLogger(__name__)

_SHELL_TIMEOUT = 300  # seconds; a shell op that runs longer is treated as hung


def execute(ops: Iterable[Operation]) -> None:
    """Apply each operation in order."""
    for op in ops:
        match op:
            case FileOperation():
                _apply_file(op)
            case ShellAction():
                _apply_shell(op)
            case DeleteOperation():
                _apply_delete(op)
            case PruneKeysOperation():
                _apply_prune(op)
            case _:
                assert_never(op)


def _apply_file(op: FileOperation) -> None:
    """Execute a file operation, handling merge strategies."""
    op.target.parent.mkdir(parents=True, exist_ok=True)
    if op.merge is MergeStrategy.OVERWRITE:
        if isinstance(op.content, bytes):
            op.target.write_bytes(op.content)
        else:
            op.target.write_text(op.content)
        return
    if op.merge is MergeStrategy.MERGE:
        if isinstance(op.content, bytes):
            raise ValueError("MERGE strategy requires text JSON content, not bytes")
        incoming = json.loads(op.content)
        try:
            existing: Any = json.loads(op.target.read_text()) if op.target.is_file() else {}
        except json.JSONDecodeError as exc:
            raise BoffError(f"cannot merge into malformed JSON file {op.target}: {exc}") from exc
        merged = json_deep_merge(existing, incoming)
        op.target.write_text(dumps_json(merged))
        return
    assert_never(op.merge)


def _apply_delete(op: DeleteOperation) -> None:
    """Remove ``target`` (file or directory tree) and prune emptied parent dirs."""
    target = op.target
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)  # does not follow symlinks
    elif target.exists() or target.is_symlink():
        target.unlink()
    _prune_empty_parents(target.parent, op.prune_until)


def _prune_empty_parents(start: Path, until: Path | None) -> None:
    """Remove empty directories from ``start`` upward, never removing ``until`` or above."""
    if until is None:
        return
    current = start
    while current != until and current.is_dir():
        try:
            current.rmdir()
        except OSError as exc:
            _log.debug("stopping prune at %s: %s", current, exc)
            return
        current = current.parent


def _apply_prune(op: PruneKeysOperation) -> None:
    """Delete each key path from the JSON file, garbage-collecting empty dicts."""
    if not op.target.is_file():
        return
    try:
        data: Any = json.loads(op.target.read_text())
    except json.JSONDecodeError as exc:
        raise BoffError(f"cannot prune keys from malformed JSON file {op.target}: {exc}") from exc
    for path in op.key_paths:
        _delete_key_path(data, tuple(path))
    op.target.write_text(dumps_json(data))


def _delete_key_path(data: Any, path: tuple[str, ...]) -> None:
    """Delete a nested key path from a dictionary."""
    if not path or not isinstance(data, dict):
        return
    head, rest = path[0], path[1:]
    if head not in data:
        return
    if not rest:
        del data[head]
        return
    _delete_key_path(data[head], rest)
    if isinstance(data[head], dict) and not data[head]:
        del data[head]


def _apply_shell(op: ShellAction) -> None:
    """Execute a shell action operation."""
    env = {**os.environ, **op.env} if op.env else None
    try:
        subprocess.run(op.argv, cwd=op.cwd, env=env, check=True, timeout=_SHELL_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        raise BoffError(f"command failed (exit {exc.returncode}): {' '.join(op.argv)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BoffError(f"command timed out after {_SHELL_TIMEOUT}s: {' '.join(op.argv)}") from exc
