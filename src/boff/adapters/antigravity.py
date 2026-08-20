"""Antigravity CLI platform adapter.

Antigravity (`agy`) reads workspace customizations from `.agents/`, but only some of the
surfaces a bun-off manifest can describe exist there. Verified against a live `agy`:

- Rules load **only** from the primary instructions file. `.agents/rules/*.md` is never read,
  flat or nested, bare or inside a plugin, and `@`-includes are not expanded. So the whole rule
  set renders as one `GEMINI.md` (see :class:`~boff.artifacts.Rules`).
- Skills, subagents, MCP servers, and lifecycle hooks all have workspace targets.
- Slash commands, settings, and permissions have no workspace target at all. Each renderer
  warns and emits nothing rather than writing a file `agy` would silently ignore.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from boff.adapters.base import (
    PlatformAdapter,
    frontmatter_block,
    renders,
    require_rules_dir,
)
from boff.artifacts import (
    Agent,
    EventHook,
    EventHooks,
    OutputStyle,
    Permissions,
    Rules,
    Settings,
    SlashCommand,
)
from boff.jsonutil import dumps_json
from boff.platform_layout import ANTIGRAVITY_LAYOUT, PlatformLayout
from boff.types import FileOperation, MergeStrategy, Operation, Scope, ScopeKind

_log = logging.getLogger(__name__)

# The single file the global rules aggregate lands in, under `~/.gemini/config/rules/`.
AGGREGATE_RULES_FILENAME = "boff.md"
_RULES_SUMMARY = "Rules deployed by bun-off."

# Antigravity groups PreToolUse/PostToolUse handlers behind a `matcher`, but takes a flat list
# of handlers for the events that carry no tool payload.
_GROUPED_EVENTS = frozenset({"PreToolUse", "PostToolUse"})

# Antigravity pipes each hook's payload on stdin and requires a JSON object on stdout. This
# dispatcher normalizes the payload into the BOFF_* env contract, runs the user's shell hook,
# and always prints `{}`: for PostToolUse that is the documented empty result, and for Stop any
# `decision` other than "continue" lets the agent stop. Self-contained (no boff imports) so it
# runs from the deployed workspace.
#
# It re-applies the matcher itself when one is passed. Antigravity emits PostToolUse payloads
# with a null `toolCall` at invocation boundaries and runs the handler without consulting the
# matcher, so a hooks.json matcher alone would fire `after_bash` on turns where no command ran,
# with an empty BOFF_COMMAND. Re-checking here keeps the normalized contract honest.
_DISPATCHER = '''\
#!/usr/bin/env python3
"""bun-off event-hook dispatcher for Antigravity. Generated; do not edit."""
import json
import os
import re
import subprocess
import sys


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    matcher = sys.argv[3] if len(sys.argv) > 3 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    tool_call = payload.get("toolCall") or {}
    args = tool_call.get("args") or {}
    tool = str(tool_call.get("name") or "")
    workspaces = payload.get("workspacePaths") or [os.getcwd()]

    # A tool-scoped hook must see the tool it asked for, and nothing else. "*" is Antigravity's
    # match-all matcher, not a regex.
    if matcher and matcher != "*" and not (tool and re.search(matcher, tool)):
        sys.stdout.write("{}")
        return

    env = dict(os.environ)
    env["BOFF_EVENT"] = event
    env["BOFF_TOOL"] = tool.lower()
    # write_to_file reports TargetFile; view_file reports AbsolutePath.
    env["BOFF_FILE"] = str(args.get("TargetFile") or args.get("AbsolutePath") or "")
    env["BOFF_COMMAND"] = str(args.get("CommandLine") or "")

    if name:
        script = os.path.join(str(workspaces[0]), ".agents", "hooks", name)
        subprocess.run(["sh", script], env=env, check=False)
    sys.stdout.write("{}")


if __name__ == "__main__":
    main()
'''


def _dispatch_command(hook: EventHook, matcher: str | None) -> str:
    """Build the hooks.json command that invokes the dispatcher for one hook.

    Antigravity runs each handler with its working directory set to the directory holding
    `hooks.json`, which is the config root, so the dispatcher path is relative to it. The
    matcher is passed through so the dispatcher can re-apply it: see :data:`_DISPATCHER`.
    """
    command = f"python3 hooks/_boff_dispatch.py {hook.event} {hook.name}"
    return f'{command} "{matcher}"' if matcher else command


def _handler(hook: EventHook, matcher: str | None) -> dict[str, object]:
    """Render one hook as an Antigravity handler object."""
    entry: dict[str, object] = {"type": "command", "command": _dispatch_command(hook, matcher)}
    if hook.timeout is not None:
        entry["timeout"] = hook.timeout
    return entry


def _hooks_json(hooks: tuple[EventHook, ...]) -> dict[str, object]:
    """Render the hooks as Antigravity's ``{<hook name>: {<event>: [...]}}`` structure.

    Grouped events wrap their handlers behind a ``matcher``; flat events list handlers directly.
    Each hook gets its own top-level name, which is how Antigravity merges and disables them.
    """
    block: dict[str, object] = {}
    for hook in hooks:
        native = hook.native_for("antigravity")
        if native is None:  # filtered by the caller; kept for exhaustiveness
            continue
        if native.event in _GROUPED_EVENTS:
            matcher = native.matcher or "*"
            entry: object = [{"matcher": matcher, "hooks": [_handler(hook, matcher)]}]
        else:
            entry = [_handler(hook, None)]
        block[f"boff-{hook.name}"] = {native.event: entry}
    return block


class AntigravityAdapter(PlatformAdapter):
    """Render bun-off artifacts to Antigravity CLI's native workspace layout."""

    name: ClassVar[str] = "antigravity"
    layout: ClassVar[PlatformLayout] = ANTIGRAVITY_LAYOUT

    @renders(Rules)
    def _rules(self, artifact: Rules, *, platform: str, scope: Scope) -> list[Operation]:
        """Inline every rule into ``GEMINI.md``, the only file Antigravity loads rules from."""
        del platform
        rules = artifact.rules_for("antigravity")
        if not rules:
            return []
        paths = self.layout.paths(scope)

        scoped = [rule.name for rule in rules if rule.globs]
        if scoped:
            _log.warning(
                "rules %s have globs but Antigravity loads its instructions file wholesale; "
                "scope will not be enforced (deploying unscoped)",
                ", ".join(sorted(scoped)),
            )

        parts = ["<!-- Generated by bun-off. Do not edit. -->"]
        parts.extend(f"## {rule.name}\n\n{rule.content.strip()}" for rule in rules)
        body = "\n\n".join(parts) + "\n"

        if scope.kind is ScopeKind.GLOBAL:
            # `~/.gemini/config/rules/*.md` does load, unlike the workspace `.agents/rules/`,
            # but only when the file carries frontmatter: a bare file is silently skipped.
            # One aggregate file keeps the `Rules` shape the workspace renderer already uses.
            target = require_rules_dir(paths, self.name) / AGGREGATE_RULES_FILENAME
            body = frontmatter_block({"trigger": "always_on", "description": _RULES_SUMMARY}) + body
        else:
            target = paths.primary
        return [
            FileOperation(
                target=target,
                content=body,
                merge=MergeStrategy.OVERWRITE,
                description=f"antigravity rules ({len(rules)}) in {target.name}",
            )
        ]

    @renders(EventHooks)
    def _event_hooks(self, artifact: EventHooks, *, platform: str, scope: Scope) -> list[Operation]:
        """Write the dispatcher, per-hook scripts, and ``.agents/hooks.json``."""
        del platform
        hooks: list[EventHook] = []
        for hook in artifact.hooks_for("antigravity"):
            if hook.native_for("antigravity") is None:
                _log.warning(
                    "event hook %r uses event %r, which Antigravity has no equivalent for; "
                    "skipping (scope it with available_on: [claude, opencode])",
                    hook.name,
                    hook.event,
                )
                continue
            hooks.append(hook)
        if not hooks:
            return []

        paths = self.layout.paths(scope)
        ops: list[Operation] = [
            FileOperation(
                target=paths.hooks_dir / "_boff_dispatch.py",
                content=_DISPATCHER,
                merge=MergeStrategy.OVERWRITE,
                description="antigravity event-hook dispatcher",
            )
        ]
        ops.extend(self._hook_script_ops(tuple(hooks), paths.hooks_dir))
        ops.append(
            FileOperation(
                target=paths.config_root / "hooks.json",
                content=dumps_json(_hooks_json(tuple(hooks))),
                merge=MergeStrategy.MERGE,
                description="antigravity event hooks",
            )
        )
        return ops

    @renders(Agent)
    def _agent(self, artifact: Agent, *, platform: str, scope: Scope) -> list[Operation]:
        """Write a subagent to ``.agents/agents/<name>.md`` with frontmatter."""
        del platform
        agents_dir = self.layout.paths(scope).agents_dir
        if artifact.permissions_for("antigravity"):
            raise ValueError(
                f"agent '{artifact.name}' has per-agent permissions, which Antigravity cannot "
                "express; scope them with available_on: [claude, opencode]"
            )
        if artifact.model_for("antigravity") is not None:
            _log.warning(
                "agent %r sets a model but Antigravity subagents inherit the parent's model; "
                "ignoring",
                artifact.name,
            )
        frontmatter: dict[str, object] = {
            "name": artifact.name,
            "description": artifact.description,
        }
        return [
            FileOperation(
                target=agents_dir / f"{artifact.name}.md",
                content=frontmatter_block(frontmatter) + artifact.content,
                merge=MergeStrategy.OVERWRITE,
                description=f"antigravity agent {artifact.name}",
            )
        ]

    @renders(SlashCommand, drops=True)
    def _slash_command(
        self, artifact: SlashCommand, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Warn and skip: Antigravity's slash commands are built-ins, not author-supplied.

        A plugin's ``commands/`` directory is converted to skills, but only by ``agy plugin
        install``, which writes outside the workspace. Nothing in `.agents/` is discovered.
        """
        del platform, scope
        _log.warning(
            "slash command %r has no Antigravity equivalent (its slash commands are built-in); "
            "skipping (scope it with available_on: [claude, opencode])",
            artifact.name,
        )
        return []

    @renders(OutputStyle, drops=True)
    def _output_style(
        self, artifact: OutputStyle, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Warn and skip: Antigravity has no output-style surface at all.

        It exposes neither an output-style directory nor a system-prompt override; ``--mode``
        is a closed enum and ``.agents/agents/`` holds subagents only.
        """
        del platform, scope
        _log.warning(
            "output style %r has no Antigravity equivalent (it has no output-style or "
            "system-prompt override surface); skipping (scope it with available_on: [claude])",
            artifact.name,
        )
        return []

    @renders(Settings, drops={ScopeKind.WORKSPACE})
    def _settings(self, artifact: Settings, *, platform: str, scope: Scope) -> list[Operation]:
        """Merge settings into the user-level file, or warn and skip in a workspace.

        Antigravity has no workspace settings file at all: its only home is
        ``~/.gemini/antigravity-cli/settings.json``, which a workspace deploy must not write.
        A global deploy owns that file, so the block lands verbatim.
        """
        if not artifact.raw_for("antigravity"):
            return []
        if scope.kind is ScopeKind.WORKSPACE:
            _log.warning(
                "settings for antigravity are not deployed to a workspace: Antigravity keeps "
                "them in the user-level ~/.gemini/antigravity-cli/settings.json; deploy them "
                "with `boff deploy --global`"
            )
            return []
        return PlatformAdapter._settings(self, artifact, platform=platform, scope=scope)

    @renders(Permissions, drops=True)
    def _permissions(
        self, artifact: Permissions, *, platform: str, scope: Scope
    ) -> list[Operation]:
        """Warn and skip: boff cannot spell Antigravity's permission block.

        Antigravity does hold user-level permissions, as an allow/deny/ask triple, but the JSON
        key they live under is not established, and writing a guess would deploy keys the tool
        silently ignores while ``boff check`` reported success. Unlike ``settings:``, which
        ships verbatim, permissions need boff to translate into the platform's own shape.
        """
        del platform, scope
        if artifact.rules_for("antigravity"):
            _log.warning(
                "permissions are not deployed to antigravity in any scope: its user-level "
                "permission format is not established, so boff will not guess at it; scope "
                "them with available_on: [claude, opencode]"
            )
        return []
