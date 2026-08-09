"""Claude Code platform adapter."""

from __future__ import annotations

from typing import ClassVar

from boff.adapters.base import PlatformAdapter, frontmatter_block, renders
from boff.artifacts import (
    Agent,
    EventHook,
    EventHooks,
    OutputStyle,
    PermissionRule,
    Permissions,
    Rule,
)
from boff.jsonutil import dumps_json
from boff.platform_layout import CLAUDE_LAYOUT, PlatformLayout, require_workspace_root
from boff.types import FileOperation, MergeStrategy, Operation, Scope

_CLAUDE_TOOL = {
    "bash": "Bash",
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "glob": "Glob",
    "grep": "Grep",
    "agent": "Task",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
}

# Static dispatcher: Claude pipes the event payload on stdin; this normalizes it into the
# BOFF_* env contract and runs the user's shell hook. Deployed once when any hook targets
# claude. Self-contained (no boff imports) so it runs from the deployed workspace.
_DISPATCHER = '''\
#!/usr/bin/env python3
"""bun-off event-hook dispatcher. Generated; do not edit."""
import json
import os
import subprocess
import sys


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    script = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    tool_input = payload.get("tool_input") or {}
    env = dict(os.environ)
    env["BOFF_EVENT"] = event
    env["BOFF_TOOL"] = str(payload.get("tool_name") or "").lower()
    env["BOFF_FILE"] = str(tool_input.get("file_path") or "")
    env["BOFF_COMMAND"] = str(tool_input.get("command") or "")
    if script:
        subprocess.run(["sh", script], env=env, check=False)


if __name__ == "__main__":
    main()
'''


def _dispatch_command(hook: EventHook) -> str:
    """Build the settings.json command string that invokes the dispatcher for one hook."""
    base = '"$CLAUDE_PROJECT_DIR'
    hooks = CLAUDE_LAYOUT.hooks_subdir
    return f'python3 {base}/{hooks}/_boff_dispatch.py" {hook.event} {base}/{hooks}/{hook.name}"'


def _claude_hooks_block(hooks: tuple[EventHook, ...]) -> dict[str, list[dict[str, object]]]:
    """Group hooks into Claude's ``event > [{matcher?, hooks:[...]}]`` structure."""
    grouped: dict[tuple[str, str | None], list[dict[str, object]]] = {}
    order: list[tuple[str, str | None]] = []
    for hook in hooks:
        native = hook.native_for("claude")
        if native is None:  # every normalized event maps on claude; kept for exhaustiveness
            continue
        key = (native.event, native.matcher)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        entry: dict[str, object] = {"type": "command", "command": _dispatch_command(hook)}
        if hook.timeout is not None:
            entry["timeout"] = hook.timeout
        grouped[key].append(entry)

    block: dict[str, list[dict[str, object]]] = {}
    for event, matcher in order:
        obj: dict[str, object] = {}
        if matcher is not None:
            obj["matcher"] = matcher
        obj["hooks"] = grouped[(event, matcher)]
        block.setdefault(event, []).append(obj)
    return block


def _globs_frontmatter(globs: tuple[str, ...]) -> str:
    """Build a ``globs:`` YAML frontmatter block (the format Claude Code honors).

    Deliberately hand-rolled rather than routed through :func:`frontmatter_block`: Claude
    honors the explicitly double-quoted ``globs: "a, b"`` form, which ``yaml.safe_dump`` would
    render unquoted.
    """
    return f'---\nglobs: "{", ".join(globs)}"\n---\n\n'


def _claude_spec(rule: PermissionRule) -> str:
    """Render one permission rule as a Claude ``Tool`` / ``Tool(specifier)`` string."""
    if rule.tool == "mcp":
        return f"mcp__{rule.pattern}" if rule.pattern else "mcp"
    name = _CLAUDE_TOOL.get(rule.tool)
    if name is None:
        raise ValueError(
            f"permission tool '{rule.tool}' is not supported on claude; "
            "scope it with available_on: [opencode]"
        )
    if rule.pattern is None:
        return name
    if rule.tool == "webfetch":
        return f"WebFetch(domain:{rule.pattern})"
    return f"{name}({rule.pattern})"


class ClaudeAdapter(PlatformAdapter):
    """Render bun-off artifacts to Claude Code's native workspace layout."""

    name: ClassVar[str] = "claude"
    layout: ClassVar[PlatformLayout] = CLAUDE_LAYOUT

    @renders(Rule)
    def _rule(self, artifact: Rule, *, platform: str, scope: Scope) -> list[Operation]:
        """Write a rule to ``.claude/rules/[<category>/]<name>.md``, prepending any globs."""
        del platform
        root = require_workspace_root(scope)
        parts = [self.layout.config_root, "rules"]
        if artifact.category:
            parts.append(artifact.category)
        parts.append(f"{artifact.name}.md")
        target = root.joinpath(*parts)
        content = artifact.content
        if artifact.globs:
            content = _globs_frontmatter(artifact.globs) + content
        return [
            FileOperation(
                target=target,
                content=content,
                merge=MergeStrategy.OVERWRITE,
                description=f"claude rule {artifact.name}",
            )
        ]

    @renders(OutputStyle)
    def _output_style(
        self, artifact: OutputStyle, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Write an output style to ``.claude/output-styles/<name>.md``.

        The body ships verbatim: Claude validates the frontmatter against a strict schema, so
        boff must not inject keys of its own. Selecting the style is a separate act -- set
        ``outputStyle`` through the ``settings:`` block.
        """
        del platform
        root = require_workspace_root(scope)
        target = root / self.layout.config_root / "output-styles" / f"{artifact.name}.md"
        return [
            FileOperation(
                target=target,
                content=artifact.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"claude output style {artifact.name}",
            )
        ]

    @renders(Permissions)
    def _permissions(
        self, artifact: Permissions, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Merge allow/ask/deny permission specs into ``.claude/settings.json``."""
        del platform
        root = require_workspace_root(scope)
        buckets: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
        for rule in artifact.rules_for("claude"):
            buckets[rule.action].append(_claude_spec(rule))
        payload = {"permissions": {k: v for k, v in buckets.items() if v}}
        return [
            FileOperation(
                target=self.layout.settings_path(root),
                content=dumps_json(payload),
                merge=MergeStrategy.MERGE,
                description="claude permissions",
            )
        ]

    @renders(EventHooks)
    def _event_hooks(self, artifact: EventHooks, *, platform: str, scope: Scope) -> list[Operation]:
        """Write the dispatcher, per-hook scripts, and the settings ``hooks`` block for Claude."""
        del platform
        hooks = artifact.hooks_for("claude")
        if not hooks:
            return []
        root = require_workspace_root(scope)
        ops: list[Operation] = [
            FileOperation(
                target=root / self.layout.hooks_subdir / "_boff_dispatch.py",
                content=_DISPATCHER,
                merge=MergeStrategy.OVERWRITE,
                description="claude event-hook dispatcher",
            )
        ]
        ops.extend(self._hook_script_ops(hooks, root))
        ops.append(
            FileOperation(
                target=self.layout.settings_path(root),
                content=dumps_json({"hooks": _claude_hooks_block(hooks)}),
                merge=MergeStrategy.MERGE,
                description="claude event hooks",
            )
        )
        return ops

    @renders(Agent)
    def _agent(self, artifact: Agent, *, platform: str, scope: Scope) -> list[Operation]:
        """Write a subagent to ``.claude/agents/<name>.md`` with frontmatter."""
        del platform
        root = require_workspace_root(scope)
        allow: list[str] = []
        deny: list[str] = []
        for rule in artifact.permissions_for("claude"):
            if rule.pattern is not None:
                raise ValueError(
                    f"agent '{artifact.name}' has a per-agent pattern rule for '{rule.tool}', "
                    "which Claude cannot scope per agent; scope it with available_on: [opencode]"
                )
            if rule.action == "ask":
                raise ValueError(
                    f"agent '{artifact.name}' uses a per-agent 'ask' verdict for '{rule.tool}', "
                    "which Claude cannot scope per agent; scope it with available_on: [opencode]"
                )
            name = _CLAUDE_TOOL.get(rule.tool)
            if name is None:
                raise ValueError(
                    f"agent '{artifact.name}' permission tool '{rule.tool}' is not supported "
                    "on claude; scope it with available_on: [opencode]"
                )
            (allow if rule.action == "allow" else deny).append(name)
        frontmatter: dict[str, object] = {
            "name": artifact.name,
            "description": artifact.description,
        }
        model = artifact.model_for("claude")
        if model is not None:
            frontmatter["model"] = model
        if allow:
            frontmatter["tools"] = ", ".join(allow)
        if deny:
            frontmatter["disallowedTools"] = ", ".join(deny)
        return [
            FileOperation(
                target=root / self.layout.config_root / "agents" / f"{artifact.name}.md",
                content=frontmatter_block(frontmatter) + artifact.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"claude agent {artifact.name}",
            )
        ]
