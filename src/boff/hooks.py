"""Hook context type and subprocess runner for pre/post-install hooks."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boff.errors import HookError
from boff.types import HookPhase, Scope, ScopeKind

_HOOK_TIMEOUT = 300  # seconds; a hook running longer is treated as hung

# A hook lives at ``<bundle>/hooks/<name>.py``, so its path needs at least this many
# parents for ``parents[1]`` (the bundle root, used as cwd) to exist.
_MIN_HOOK_PARENTS = 2


@dataclass(frozen=True)
class HookContext:
    """JSON-serializable context handed to each hook subprocess via stdin."""

    phase: HookPhase
    platforms: tuple[str, ...]
    scope: Scope
    manifest_root: Path
    ops_count: int

    def to_json(self) -> str:
        """Encode this context as a JSON string suitable for hook stdin."""
        payload = {
            "phase": str(self.phase),
            "platforms": list(self.platforms),
            "scope": {
                "kind": str(self.scope.kind),
                "workspace_root": (
                    str(self.scope.workspace_root) if self.scope.workspace_root else None
                ),
            },
            "manifest_root": str(self.manifest_root),
            "ops_count": self.ops_count,
        }
        return json.dumps(payload)

    @classmethod
    def from_stdin(cls) -> HookContext:
        """Read a JSON-encoded HookContext from the current process's stdin.

        Raises :class:`HookError` if the payload is not valid JSON or is missing
        an expected key, so a hook fails cleanly instead of with a bare
        ``KeyError``/``JSONDecodeError``.
        """
        try:
            data: dict[str, Any] = json.loads(sys.stdin.read())
            scope_data = data["scope"]
            workspace_root = scope_data["workspace_root"]
            return cls(
                phase=HookPhase(data["phase"]),
                platforms=tuple(data["platforms"]),
                scope=Scope(
                    kind=ScopeKind(scope_data["kind"]),
                    workspace_root=Path(workspace_root) if workspace_root else None,
                ),
                manifest_root=Path(data["manifest_root"]),
                ops_count=int(data["ops_count"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise HookError(f"malformed hook context on stdin: {exc}") from exc


def run_hooks(scripts: Iterable[Path], ctx: HookContext) -> None:
    """Run each hook script with ``ctx`` JSON piped to stdin.

    Each script is an absolute path resolved against the manifest that declared
    it, so an inherited hook runs from its own bundle. The subprocess ``cwd`` is
    that bundle's root: hook scripts live at ``<bundle>/hooks/<name>.py``, so the
    grandparent is the bundle root. A hook that exits non-zero, times out, or has
    a too-shallow path raises :class:`HookError` instead of a raw traceback.
    """
    payload = ctx.to_json()
    for script in scripts:
        if len(script.parents) < _MIN_HOOK_PARENTS:
            raise HookError(f"hook script path is too shallow to locate its bundle: {script}")
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                input=payload,
                text=True,
                cwd=script.parents[1],
                capture_output=True,
                timeout=_HOOK_TIMEOUT,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise HookError(
                f"hook {script.name} failed (exit {exc.returncode}):\n"
                f"{(exc.stderr or exc.stdout or '').strip()}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HookError(f"hook {script.name} timed out after {_HOOK_TIMEOUT}s") from exc
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
