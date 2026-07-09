import logging
from pathlib import Path

import pytest

from boff.artifacts import EventHook, EventHooks, PermissionRule, Permissions, Settings
from boff.artifacts.rule import Rule
from boff.manifest import Manifest, ManifestMeta
from boff.merge import merge_manifests


def _m(name: str, **kw: object) -> Manifest:
    return Manifest(root=Path(f"/{name}"), meta=ManifestMeta(name=name, description=name), **kw)  # type: ignore[arg-type]


def _rule(name: str, content: str) -> Rule:
    return Rule(name=name, content=content)


def test_child_overrides_parent_by_name(caplog: pytest.LogCaptureFixture) -> None:
    parent = _m("p", rules=(_rule("shared", "parent"), _rule("only_p", "p")))
    child = _m("c", rules=(_rule("shared", "child"), _rule("only_c", "c")))
    with caplog.at_level(logging.WARNING):
        merged = merge_manifests([parent], child)
    by = {r.name: r.content for r in merged.rules}
    assert by == {"shared": "child", "only_p": "p", "only_c": "c"}
    assert any("shared" in rec.message for rec in caplog.records)


def test_multiparent_last_wins() -> None:
    p1 = _m("p1", rules=(_rule("x", "p1"),))
    p2 = _m("p2", rules=(_rule("x", "p2"),))
    child = _m("c")
    merged = merge_manifests([p1, p2], child)
    assert {r.name: r.content for r in merged.rules} == {"x": "p2"}


def test_meta_not_inherited() -> None:
    parent = _m("p")
    child = _m("c")
    assert merge_manifests([parent], child).meta.name == "c"


def test_settings_deep_merge() -> None:
    parent = _m("p", settings=Settings(raw={"claude": {"a": 1, "b": 1}}, available_on=frozenset()))
    child = _m("c", settings=Settings(raw={"claude": {"b": 2, "c": 3}}, available_on=frozenset()))
    merged = merge_manifests([parent], child)
    assert merged.settings is not None
    assert merged.settings.raw_for("claude") == {"a": 1, "b": 2, "c": 3}


def test_permissions_concat_and_dedup() -> None:
    shared = PermissionRule(tool="read", action="allow")
    parent = _m("p", permissions=Permissions(rules=(shared, PermissionRule("bash", "deny"))))
    child = _m("c", permissions=Permissions(rules=(shared, PermissionRule("edit", "ask"))))
    merged = merge_manifests([parent], child)
    assert merged.permissions is not None
    rules = merged.permissions.rules
    assert len(rules) == 3  # shared de-duplicated
    assert shared in rules


def test_hooks_merge_by_name_last_wins_preserve_order() -> None:
    parent = _m("p", pre_install=(Path("/p/hooks/a.py"), Path("/p/hooks/b.py")))
    child = _m("c", pre_install=(Path("/c/hooks/b.py"), Path("/c/hooks/c.py")))
    merged = merge_manifests([parent], child).pre_install
    assert [p.stem for p in merged] == ["a", "b", "c"]
    # An inherited-only hook keeps the parent path; a redefined one takes the child's.
    by_name = {p.stem: p for p in merged}
    assert by_name["a"] == Path("/p/hooks/a.py")
    assert by_name["b"] == Path("/c/hooks/b.py")


def test_tool_files_union_across_extends() -> None:
    parent = _m("p", tool_files={"mise": (Path("/p/mise.toml"),)})
    child = _m("c", tool_files={"mise": (Path("/c/mise.toml"),)})
    merged = merge_manifests([parent], child)
    assert merged.tool_files == {"mise": (Path("/p/mise.toml"), Path("/c/mise.toml"))}


def test_tool_files_union_dedups_shared_path() -> None:
    shared = Path("/shared/mise.toml")
    parent = _m("p", tool_files={"mise": (shared, Path("/p/mise.toml"))})
    child = _m("c", tool_files={"mise": (shared, Path("/c/mise.toml"))})
    merged = merge_manifests([parent], child)
    assert merged.tool_files == {"mise": (shared, Path("/p/mise.toml"), Path("/c/mise.toml"))}


def test_event_hooks_override_by_name() -> None:
    def hook(content: str) -> EventHook:
        return EventHook(name="h", event="on_finish", command=content, script_content=content)

    parent = _m("p", event_hooks=EventHooks(hooks=(hook("parent"),)))
    child = _m("c", event_hooks=EventHooks(hooks=(hook("child"),)))
    merged = merge_manifests([parent], child)
    assert merged.event_hooks is not None
    assert merged.event_hooks.hooks[0].command == "child"
