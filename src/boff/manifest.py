"""YAML manifest parser: loads ``boff.yaml`` into a typed :class:`Manifest`."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

import yaml

from boff.artifacts import (
    NORMALIZED_EVENTS,
    RESERVED_KEYS,
    SKILL_FILENAME,
    Action,
    Agent,
    Artifact,
    EventHook,
    EventHooks,
    MCPServer,
    PermissionRule,
    Permissions,
    Rule,
    Rules,
    Settings,
    Skill,
    SkillFile,
    SlashCommand,
    ships,
)
from boff.artifacts.permissions import CANONICAL_TOOLS
from boff.errors import ManifestError
from boff.manifest_sources import resolve_ref
from boff.sources.base import PluginSpec
from boff.sources.local import LocalSpec

# On-disk manifest-folder layout: the file and subdirectory names a manifest folder uses.
_log = logging.getLogger(__name__)

MANIFEST_FILENAME = "boff.yaml"
RULES_DIR = "rules"
SKILLS_DIR = "skills"
SLASH_COMMANDS_DIR = "slash_commands"
MCP_SERVERS_DIR = "mcp_servers"
MCP_RAW_DIR = "raw"
AGENTS_DIR = "agents"
EVENT_HOOKS_DIR = "event_hooks"
HOOKS_DIR = "hooks"


@dataclass(frozen=True)
class ManifestMeta:
    """Free-form documentation describing a manifest or bundle.

    ``name`` and ``description`` are mandatory; the rest are optional. Metadata is purely
    informational: it produces no deploy operations and is not inherited via ``extends``.
    """

    name: str
    description: str
    long_description: str | None = None
    version: str | None = None
    author: str | None = None
    homepage: str | None = None


@dataclass(frozen=True)
class PluginInstall:
    """One platform's install spec for a plugin."""

    source: str
    spec: PluginSpec


@dataclass(frozen=True)
class Plugin:
    """A plugin entry: name plus per-platform install specs."""

    name: str
    install: dict[str, PluginInstall]


@dataclass(frozen=True)
class Manifest:
    """Parsed bun-off manifest, anchored at ``root`` on disk."""

    root: Path
    meta: ManifestMeta
    extends: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()
    skills: tuple[Skill, ...] = ()
    slash_commands: tuple[SlashCommand, ...] = ()
    mcp_servers: tuple[MCPServer, ...] = ()
    plugins: tuple[Plugin, ...] = ()
    tool_files: dict[str, tuple[Path, ...]] = field(default_factory=dict[str, tuple[Path, ...]])
    pre_install: tuple[Path, ...] = ()
    post_install: tuple[Path, ...] = ()
    permissions: Permissions | None = None
    agents: tuple[Agent, ...] = ()
    settings: Settings | None = None
    event_hooks: EventHooks | None = None

    def iter_artifacts(self) -> Iterable[Artifact]:
        """Yield every artifact in the manifest, in deploy order.

        Rules are yielded twice, once per rule and once as a :class:`Rules` aggregate. An
        adapter renders whichever form matches how its platform loads rules -- a directory of
        files, or a single instructions file -- and ignores the other.
        """
        yield from self.rules
        if self.rules:
            yield Rules(rules=tuple(self.rules))
        yield from self.skills
        yield from self.slash_commands
        yield from self.mcp_servers
        if self.permissions is not None:
            yield self.permissions
        yield from self.agents
        if self.settings is not None:
            yield self.settings
        if self.event_hooks is not None:
            yield self.event_hooks


# YAML hands every value back as ``Any``, and narrowing it with ``isinstance`` yields only an
# unparameterized container. These helpers are the trust boundary: each checks the shape it
# promises and casts once, so every validator below reads typed fields. They are the only
# casts in the package.


def _as_mapping(value: Any) -> dict[str, Any] | None:
    """Return ``value`` as a str-keyed mapping, or None when it is not a mapping."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _require_mapping(value: Any, message: str) -> dict[str, Any]:
    """Return ``value`` as a str-keyed mapping, raising ``ValueError(message)`` otherwise."""
    mapping = _as_mapping(value)
    if mapping is None:
        raise ValueError(message)
    return mapping


def _require_list(value: Any, message: str) -> list[Any]:
    """Return ``value`` as a list, raising ``ValueError(message)`` otherwise."""
    if not isinstance(value, list):
        raise ValueError(message)
    return cast("list[Any]", value)


def _str_list(value: Any) -> list[str] | None:
    """Return ``value`` as a list of strings, or None when it is not one."""
    if not isinstance(value, list):
        return None
    items = cast("list[Any]", value)
    return cast("list[str]", items) if all(isinstance(item, str) for item in items) else None


def _str_map(value: Any) -> dict[str, str] | None:
    """Return ``value`` as a string-to-string mapping, or None when it is not one."""
    if not isinstance(value, dict):
        return None
    entries = cast("dict[Any, Any]", value).items()
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in entries):
        return None
    return cast("dict[str, str]", value)


def _parse_available_on(entry: dict[str, Any]) -> frozenset[str]:
    """Parse and validate an entry's ``available_on`` into a frozenset of platform names."""
    available = _str_list(entry.get("available_on") or [])
    if available is None:
        raise ValueError(f"'available_on' must be a list of strings: {entry!r}")
    return frozenset(available)


def _entry_name_and_available_on(entry: Any) -> tuple[str, frozenset[str]]:
    """Extract the entry name and supported platforms from a manifest entry."""
    if isinstance(entry, str):
        return entry, frozenset()
    mapping = _require_mapping(entry, f"unsupported manifest entry: {entry!r}")
    name = mapping.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"manifest entry missing 'name': {entry!r}")
    return name, _parse_available_on(mapping)


def _entry_globs(entry: Any) -> tuple[str, ...]:
    """Extract glob patterns from a manifest entry."""
    mapping = _as_mapping(entry)
    if mapping is None or "globs" not in mapping:
        return ()
    globs = _str_list(mapping["globs"])
    if globs is None or not all(globs):
        raise ValueError(f"rule 'globs' must be a list of non-empty strings: {entry!r}")
    return tuple(globs)


def _load_rules(root: Path, entries: list[Any]) -> tuple[Rule, ...]:
    """Load and parse a list of rule entries from the manifest."""
    rules: list[Rule] = []
    for entry in entries:
        name, available_on = _entry_name_and_available_on(entry)
        mapping = _as_mapping(entry) or {}
        category = mapping.get("category")
        if category is not None and not isinstance(category, str):
            raise ValueError(f"rule 'category' must be a string: {entry!r}")
        globs = _entry_globs(entry)
        md_path = root / RULES_DIR / f"{name}.md"
        if not md_path.is_file():
            raise FileNotFoundError(f"rule content not found: {md_path}")
        rules.append(
            Rule(
                name=name,
                content=md_path.read_text(),
                category=category,
                globs=globs,
                available_on=available_on,
            )
        )
    return tuple(rules)


def _load_skill_files(skill_root: Path) -> tuple[SkillFile, ...]:
    """Read the shippable files under a skill directory, minus its ``SKILL.md`` entry point.

    Build and editor droppings are skipped (see :func:`boff.artifacts.skill.ships`); everything
    else ships, so the author still decides what a skill carries.
    """
    entry = skill_root / SKILL_FILENAME
    files: list[SkillFile] = []
    for src in sorted(p for p in skill_root.rglob("*") if p.is_file() and p != entry):
        rel = PurePosixPath(src.relative_to(skill_root))
        if not ships(rel):
            _log.debug("skipping %s: not a shippable skill file", src)
            continue
        files.append(SkillFile(path=rel, content=src.read_bytes()))
    return tuple(files)


def _load_skills(root: Path, entries: list[Any]) -> tuple[Skill, ...]:
    """Load skill entries, accepting either a single markdown file or a skill directory.

    A skill directory ships supporting files (references, templates) alongside its
    ``SKILL.md``; nothing under it is filtered out, so the author decides what ships.
    """
    skills: list[Skill] = []
    for entry in entries:
        name, available_on = _entry_name_and_available_on(entry)
        md_path = root / SKILLS_DIR / f"{name}.md"
        skill_root = root / SKILLS_DIR / name
        if skill_root.is_dir() and md_path.is_file():
            raise ValueError(
                f"skill '{name}' is both a file and a directory: {md_path} and {skill_root}"
            )
        if skill_root.is_dir():
            entry_path = skill_root / SKILL_FILENAME
            if not entry_path.is_file():
                raise FileNotFoundError(f"skill directory has no entry point: {entry_path}")
            content, files = entry_path.read_text(), _load_skill_files(skill_root)
        elif md_path.is_file():
            content, files = md_path.read_text(), ()
        else:
            raise FileNotFoundError(f"{SKILLS_DIR} content not found: {md_path}")
        skills.append(Skill(name=name, content=content, available_on=available_on, files=files))
    return tuple(skills)


class _SimpleArtifactFactory[T](Protocol):
    """Constructor shape of an artifact whose whole body is one markdown file."""

    def __call__(self, *, name: str, content: str, available_on: frozenset[str]) -> T: ...


def _load_simple_artifacts[T](
    root: Path, subdir: str, entries: list[Any], factory: _SimpleArtifactFactory[T]
) -> tuple[T, ...]:
    """Load simple artifacts from the manifest."""
    items: list[T] = []
    for entry in entries:
        name, available_on = _entry_name_and_available_on(entry)
        md_path = root / subdir / f"{name}.md"
        if not md_path.is_file():
            raise FileNotFoundError(f"{subdir} content not found: {md_path}")
        items.append(factory(name=name, content=md_path.read_text(), available_on=available_on))
    return tuple(items)


def _load_mcp_servers(root: Path, entries: list[Any]) -> tuple[MCPServer, ...]:
    """Load MCP server configurations from the manifest."""
    servers: list[MCPServer] = []
    raw_root = root / MCP_SERVERS_DIR / MCP_RAW_DIR
    for entry in entries:
        name, available_on = _entry_name_and_available_on(entry)
        raw: dict[str, dict[str, Any]] = {}
        if raw_root.is_dir():
            for platform_dir in sorted(p for p in raw_root.iterdir() if p.is_dir()):
                json_path = platform_dir / f"{name}.json"
                if json_path.is_file():
                    try:
                        raw[platform_dir.name] = json.loads(json_path.read_text())
                    except json.JSONDecodeError as exc:
                        raise ManifestError(
                            f"invalid JSON in raw MCP server file {json_path}: {exc}"
                        ) from exc
        if not raw:
            raise FileNotFoundError(
                f"mcp server '{name}' has no raw JSON under {raw_root}/<platform>/{name}.json"
            )
        servers.append(MCPServer(name=name, raw=raw, available_on=available_on))
    return tuple(servers)


def _load_permission_rule(entry: Any, action: Action) -> PermissionRule:
    """Parse a permission rule entry."""
    mapping = _require_mapping(entry, f"permission entry must be a mapping: {entry!r}")
    tool = mapping.get("tool")
    if not isinstance(tool, str) or tool not in CANONICAL_TOOLS:
        raise ValueError(f"permission entry has unknown or missing 'tool': {entry!r}")
    pattern = mapping.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        raise ValueError(f"permission 'pattern' must be a string: {entry!r}")
    return PermissionRule(
        tool=tool, action=action, pattern=pattern, available_on=_parse_available_on(mapping)
    )


def _load_permissions(raw: Any) -> Permissions | None:
    """Parse the permissions block of the manifest."""
    if not raw:
        return None
    mapping = _require_mapping(raw, "'permissions' must be a mapping with allow/ask/deny lists")
    actions: tuple[Action, ...] = ("allow", "ask", "deny")
    unknown = set(mapping) - set(actions)
    if unknown:
        raise ValueError(f"unknown permission verdict groups: {sorted(unknown)}")
    rules: list[PermissionRule] = []
    for action in actions:
        entries = _require_list(mapping.get(action) or [], f"'permissions.{action}' must be a list")
        rules.extend(_load_permission_rule(entry, action) for entry in entries)
    return Permissions(rules=tuple(rules)) if rules else None


def _load_settings(raw: Any) -> Settings | None:
    """Parse the settings block of the manifest."""
    if not raw:
        return None
    raw_blocks = _require_mapping(raw, "'settings' must be a mapping of platform -> settings block")
    blocks: dict[str, dict[str, Any]] = {}
    for platform, raw_block in raw_blocks.items():
        block = _require_mapping(raw_block, f"'settings.{platform}' must be a mapping")
        reserved = RESERVED_KEYS.get(platform, frozenset())
        clashing = sorted(reserved & set(block))
        if clashing:
            raise ValueError(
                f"'settings.{platform}' contains keys owned by dedicated artifacts: "
                f"{clashing}; configure them via the dedicated manifest section instead"
            )
        blocks[platform] = block
    return Settings(raw=blocks, available_on=frozenset(blocks))


def _event_hook_body(root: Path, name: str, command: Any, script: Any) -> str:
    """Resolve a hook's shell body: materialize an inline command or read a script file."""
    if (command is None) == (script is None):
        raise ValueError(f"event hook '{name}' needs exactly one of 'command' or 'script'")
    if command is not None:
        if not isinstance(command, str) or not command:
            raise ValueError(f"event hook '{name}' 'command' must be a non-empty string")
        return f"#!/usr/bin/env sh\n{command}\n"
    if not isinstance(script, str) or not script:
        raise ValueError(f"event hook '{name}' 'script' must be a non-empty string")
    script_path = root / EVENT_HOOKS_DIR / script
    if not script_path.is_file():
        raise FileNotFoundError(f"event hook script not found: {script_path}")
    return script_path.read_text()


def _load_event_hook(root: Path, entry: Any, seen: set[str]) -> EventHook:
    """Parse a single event hook entry."""
    mapping = _require_mapping(entry, f"event hook entry must be a mapping: {entry!r}")
    name = mapping.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"event hook entry missing 'name': {entry!r}")
    if name in seen:
        raise ValueError(f"duplicate event hook name: {name!r}")
    seen.add(name)
    event = mapping.get("event")
    if event not in NORMALIZED_EVENTS:
        raise ValueError(
            f"event hook '{name}' has unknown 'event': {event!r}; "
            f"expected one of {sorted(NORMALIZED_EVENTS)}"
        )
    timeout = mapping.get("timeout")
    if timeout is not None and not isinstance(timeout, int):
        raise ValueError(f"event hook '{name}' 'timeout' must be an integer")
    return EventHook(
        name=name,
        event=event,
        command=mapping.get("command"),
        script=mapping.get("script"),
        script_content=_event_hook_body(root, name, mapping.get("command"), mapping.get("script")),
        timeout=timeout,
        available_on=_parse_available_on(mapping),
    )


def _load_event_hooks(root: Path, entries: Any) -> EventHooks | None:
    """Parse the event hooks block of the manifest."""
    if not entries:
        return None
    seen: set[str] = set()
    hooks = tuple(
        _load_event_hook(root, entry, seen)
        for entry in _require_list(entries, "'event_hooks' must be a list of hook entries")
    )
    return EventHooks(hooks=hooks)


def _parse_agent_model(name: str, entry: dict[str, Any]) -> str | dict[str, str] | None:
    """Validate an agent's optional ``model``: a string, a per-platform map, or absent."""
    model = entry.get("model")
    if isinstance(model, dict):
        model_map = _str_map(model)
        if model_map is None:
            raise ValueError(f"agent '{name}' 'model' map must have string keys and values")
        return model_map
    if model is not None and not isinstance(model, str):
        raise ValueError(f"agent '{name}' 'model' must be a string or a per-platform map")
    return model


def _load_agent(root: Path, entry: Any) -> Agent:
    """Parse and validate a single agent entry into an :class:`Agent`."""
    mapping = _require_mapping(entry, f"agent entry must be a mapping: {entry!r}")
    name = mapping.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"agent entry missing 'name': {entry!r}")
    description = mapping.get("description")
    if not isinstance(description, str) or not description:
        raise ValueError(f"agent '{name}' missing 'description'")
    model = _parse_agent_model(name, mapping)
    mode = mapping.get("mode")
    if mode is not None and not isinstance(mode, str):
        raise ValueError(f"agent '{name}' 'mode' must be a string")
    available_on = _parse_available_on(mapping)
    perms = _load_permissions(mapping.get("permissions") or {})
    rules = perms.rules if perms is not None else ()
    md_path = root / AGENTS_DIR / f"{name}.md"
    if not md_path.is_file():
        raise FileNotFoundError(f"agent content not found: {md_path}")
    return Agent(
        name=name,
        content=md_path.read_text(),
        description=description,
        model=model,
        mode=mode,
        permissions=rules,
        available_on=available_on,
    )


def _load_agents(root: Path, entries: list[Any]) -> tuple[Agent, ...]:
    """Load agent configurations from the manifest."""
    return tuple(_load_agent(root, entry) for entry in entries)


def _load_plugins(root: Path, raw: dict[str, Any]) -> tuple[Plugin, ...]:
    """Load plugin configurations from the manifest."""
    plugins: list[Plugin] = []
    for name, raw_body in raw.items():
        body = _require_mapping(raw_body, f"plugin '{name}' body must be a mapping")
        install_raw = _require_mapping(
            body.get("install") or {}, f"plugin '{name}' install must be a mapping"
        )
        installs: dict[str, PluginInstall] = {}
        for platform, raw_spec in install_raw.items():
            spec_raw = _as_mapping(raw_spec)
            if spec_raw is None or "source" not in spec_raw:
                raise ValueError(f"plugin '{name}' install for '{platform}' missing 'source'")
            source = spec_raw["source"]
            if source == "local":
                path_str = spec_raw.get("path")
                if not isinstance(path_str, str):
                    raise ValueError(
                        f"plugin '{name}' local install for '{platform}' missing 'path'"
                    )
                spec = LocalSpec(source="local", path=(root / path_str).resolve())
            else:
                raise ValueError(f"unsupported plugin source '{source}' in plugin '{name}'")
            installs[platform] = PluginInstall(source=source, spec=spec)
        plugins.append(Plugin(name=name, install=installs))
    return tuple(plugins)


def _load_hooks(root: Path, raw: dict[str, Any]) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Load lifecycle hook scripts from the manifest."""

    def _paths(items: list[Any]) -> tuple[Path, ...]:
        """Convert a list of string paths to Path objects."""
        out: list[Path] = []
        for item in items:
            entry = _as_mapping(item)
            if entry is None or "script" not in entry:
                raise ValueError(f"hook entry must be {{'script': <name>}}: {item!r}")
            script = entry["script"]
            if not isinstance(script, str):
                raise ValueError(f"hook script must be a string: {item!r}")
            out.append(root / HOOKS_DIR / f"{script}.py")
        return tuple(out)

    pre = _paths(raw.get("pre_install") or [])
    post = _paths(raw.get("post_install") or [])
    return pre, post


def _load_meta(raw: dict[str, Any]) -> ManifestMeta:
    """Parse the manifest metadata block."""
    block = _require_mapping(raw.get("meta") or {}, "'meta' must be a mapping")

    def _required(key: str) -> str:
        """Extract a required string value from a dictionary."""
        value = block.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"manifest 'meta.{key}' is required and must be a non-empty string")
        return value

    def _optional(key: str) -> str | None:
        """Extract an optional string value from a dictionary."""
        value = block.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"manifest 'meta.{key}' must be a string")
        return value

    return ManifestMeta(
        name=_required("name"),
        description=_required("description"),
        long_description=_optional("long_description"),
        version=_optional("version"),
        author=_optional("author"),
        homepage=_optional("homepage"),
    )


def _extends_refs(raw: dict[str, Any]) -> list[str]:
    """Normalize the ``extends`` field to an ordered list of reference strings."""
    refs = raw.get("extends")
    if refs is None:
        return []
    if isinstance(refs, str):
        return [refs]
    ref_list = _str_list(refs)
    if ref_list is None or not all(ref_list):
        raise ValueError("'extends' must be a reference string or a list of reference strings")
    return list(ref_list)


def _read_manifest_yaml(root: Path) -> dict[str, Any]:
    """Read and parse the manifest YAML file."""
    yaml_path = root / MANIFEST_FILENAME
    if not yaml_path.is_file():
        raise ManifestError(f"manifest not found: {yaml_path}")
    try:
        raw: Any = yaml.safe_load(yaml_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {yaml_path}: {exc}") from exc
    return _require_mapping(raw, f"manifest must be a YAML mapping: {yaml_path}")


def load_manifest(path: Path, *, _seen: frozenset[Path] = frozenset()) -> Manifest:
    """Read ``<path>/boff.yaml`` and return a fully resolved :class:`Manifest`.

    Resolves ``extends`` parents (local paths or remote git refs), recursively loads each,
    and merges them under this manifest (child overrides parent). ``_seen`` tracks resolved
    roots to detect inheritance cycles.
    """
    from boff.merge import merge_manifests  # noqa: PLC0415  (deferred to avoid an import cycle)

    root = path.resolve()
    if root in _seen:
        raise ValueError(f"manifest extends cycle detected at {root}")
    raw = _read_manifest_yaml(root)

    child = _load_own(root, raw)
    refs = _extends_refs(raw)
    if not refs:
        return child

    seen = _seen | {root}
    parents = [load_manifest(resolve_ref(ref, base_root=root), _seen=seen) for ref in refs]
    return merge_manifests(parents, child)


def _load_own(root: Path, raw: dict[str, Any]) -> Manifest:
    """Parse one manifest folder into a :class:`Manifest`, recording (not resolving) ``extends``."""
    meta = _load_meta(raw)
    rules = _load_rules(root, raw.get("rules") or [])
    skills = _load_skills(root, raw.get("skills") or [])
    slash_commands = _load_simple_artifacts(
        root, SLASH_COMMANDS_DIR, raw.get("slash_commands") or [], SlashCommand
    )
    mcp_servers = _load_mcp_servers(root, raw.get("mcp_servers") or [])
    permissions = _load_permissions(raw.get("permissions") or {})
    agents = _load_agents(root, raw.get("agents") or [])
    settings = _load_settings(raw.get("settings") or {})
    plugins = _load_plugins(root, raw.get("plugins") or {})

    tool_files: dict[str, tuple[Path, ...]] = {}
    mise_paths = _str_list(raw.get("mise") or [])
    if mise_paths is None:
        raise ValueError("'mise' must be a list of paths")
    if mise_paths:
        tool_files["mise"] = tuple((root / p).resolve() for p in mise_paths)

    pre, post = _load_hooks(root, raw.get("hooks") or {})

    return Manifest(
        root=root,
        meta=meta,
        extends=tuple(_extends_refs(raw)),
        rules=rules,
        skills=skills,
        slash_commands=slash_commands,
        mcp_servers=mcp_servers,
        plugins=plugins,
        tool_files=tool_files,
        pre_install=pre,
        post_install=post,
        permissions=permissions,
        agents=agents,
        settings=settings,
        event_hooks=_load_event_hooks(root, raw.get("event_hooks") or []),
    )
