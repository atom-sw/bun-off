from collections.abc import Callable
from pathlib import Path

import pytest

from boff.manifest import load_manifest
from tests.conftest import SKILL_SUPPORT

# Factory signatures for the `write_manifest` and `write_skill_dir` fixtures (see conftest.py).
WriteManifest = Callable[..., Path]
WriteSkillDir = Callable[..., Path]


def test_load_manifest_counts(sample_manifest: Path) -> None:
    m = load_manifest(sample_manifest)
    assert len(m.rules) == 1
    assert len(m.skills) == 1
    assert len(m.slash_commands) == 1
    assert len(m.mcp_servers) == 2
    assert len(m.plugins) == 1
    assert m.permissions is not None
    assert len(m.permissions.rules) == 6
    assert len(m.agents) == 1
    assert m.settings is not None
    assert m.event_hooks is not None
    assert len(m.event_hooks.hooks) == 2
    assert m.tool_files["mise"][0].name == "mise.toml"
    assert [p.name for p in m.pre_install] == ["say_pre.py"]
    assert [p.name for p in m.post_install] == ["say_post.py"]
    assert m.pre_install[0] == sample_manifest / "hooks" / "say_pre.py"


def test_manifest_rule_fields(sample_manifest: Path) -> None:
    m = load_manifest(sample_manifest)
    rule = m.rules[0]
    assert rule.name == "style"
    assert rule.category == "dev-essentials"
    assert rule.globs == ("**/*.md", "docs/**")
    assert "Style rule" in rule.content


def test_manifest_rejects_non_list_globs(tmp_path: Path, write_manifest: WriteManifest) -> None:
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "r.md").write_text("body")
    write_manifest(tmp_path, 'rules:\n  - name: r\n    globs: "**/*.py"\n')
    with pytest.raises(ValueError, match="globs"):
        load_manifest(tmp_path)


def test_flat_and_directory_skills_load_the_same_body(
    tmp_path: Path, write_manifest: WriteManifest, write_skill_dir: WriteSkillDir
) -> None:
    body = "# shared body\n"
    flat, nested = tmp_path / "flat", tmp_path / "nested"
    (flat / "skills").mkdir(parents=True)
    (flat / "skills" / "s.md").write_text(body)
    write_skill_dir(nested, "s", body=body)
    for root in (flat, nested):
        write_manifest(root, "skills:\n  - s\n")
    assert load_manifest(flat).skills[0].content == load_manifest(nested).skills[0].content == body


def test_directory_skill_carries_its_supporting_files(
    tmp_path: Path, write_manifest: WriteManifest, write_skill_dir: WriteSkillDir
) -> None:
    write_skill_dir(tmp_path, "s")
    write_manifest(tmp_path, "skills:\n  - s\n")
    skill = load_manifest(tmp_path).skills[0]
    assert {str(f.path): f.content.decode() for f in skill.files} == SKILL_SUPPORT


def test_flat_skill_carries_no_supporting_files(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "s.md").write_text("body")
    write_manifest(tmp_path, "skills:\n  - s\n")
    assert load_manifest(tmp_path).skills[0].files == ()


def test_skill_directory_without_an_entry_point_raises(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    (tmp_path / "skills" / "s" / "references").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "references" / "contract.md").write_text("body")
    write_manifest(tmp_path, "skills:\n  - s\n")
    with pytest.raises(FileNotFoundError, match="no entry point"):
        load_manifest(tmp_path)


def test_skill_present_as_both_file_and_directory_raises(
    tmp_path: Path, write_manifest: WriteManifest, write_skill_dir: WriteSkillDir
) -> None:
    write_skill_dir(tmp_path, "s")
    (tmp_path / "skills" / "s.md").write_text("body")
    write_manifest(tmp_path, "skills:\n  - s\n")
    with pytest.raises(ValueError, match="both a file and a directory"):
        load_manifest(tmp_path)


def test_manifest_rejects_non_list_mise(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(tmp_path, "mise: mise/mise.toml\n")
    with pytest.raises(ValueError, match="'mise' must be a list of paths"):
        load_manifest(tmp_path)


def test_mcp_server_raw_loaded_from_disk(sample_manifest: Path) -> None:
    m = load_manifest(sample_manifest)
    by_name = {s.name: s for s in m.mcp_servers}
    assert by_name["context7"].raw["claude"]["command"] == "uvx"
    assert by_name["tldr"].raw["claude"]["args"] == ["llm-tldr"]


def test_manifest_permission_rules_loaded(sample_manifest: Path) -> None:
    m = load_manifest(sample_manifest)
    assert m.permissions is not None
    by = {(r.tool, r.pattern): r for r in m.permissions.rules}
    assert by[("read", "./src/**")].action == "allow"
    assert by[("webfetch", "github.com")].action == "allow"
    assert by[("bash", "git push *")].action == "ask"
    assert by[("edit", None)].action == "ask"
    assert by[("bash", "curl *")].action == "deny"
    mcp = by[("mcp", "secret__*")]
    assert mcp.action == "deny"
    assert mcp.available_on == frozenset({"claude"})


def test_manifest_without_permissions_is_none(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "rules: []\n")
    assert load_manifest(tmp_path).permissions is None


def test_manifest_rejects_unknown_permission_tool(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "permissions:\n  allow:\n    - { tool: bogus }\n")
    with pytest.raises(ValueError, match="tool"):
        load_manifest(tmp_path)


def test_manifest_rejects_unknown_permission_verdict(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "permissions:\n  maybe:\n    - { tool: bash }\n")
    with pytest.raises(ValueError, match="verdict"):
        load_manifest(tmp_path)


def test_manifest_loads_agents(sample_manifest: Path) -> None:
    m = load_manifest(sample_manifest)
    agent = m.agents[0]
    assert agent.name == "reviewer"
    assert agent.description == "Read-only code reviewer"
    assert agent.model == "haiku"
    assert agent.mode == "subagent"
    assert "review" in agent.content.lower()
    by = {(r.tool, r.pattern): r for r in agent.permissions}
    assert by[("read", None)].action == "allow"
    assert by[("grep", None)].action == "allow"
    assert by[("edit", None)].action == "deny"
    assert by[("bash", None)].action == "deny"
    assert by[("bash", "git log *")].available_on == frozenset({"opencode"})


def test_manifest_without_agents_is_empty(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(tmp_path, "rules: []\n")
    assert load_manifest(tmp_path).agents == ()


def test_manifest_agent_missing_description_raises(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "r.md").write_text("body")
    write_manifest(tmp_path, "agents:\n  - name: r\n")
    with pytest.raises(ValueError, match="description"):
        load_manifest(tmp_path)


def test_manifest_agent_missing_body_raises(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(tmp_path, "agents:\n  - name: r\n    description: d\n")
    with pytest.raises(FileNotFoundError, match="agent content"):
        load_manifest(tmp_path)


def test_manifest_agent_model_map(tmp_path: Path, write_manifest: WriteManifest) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "r.md").write_text("body")
    write_manifest(
        tmp_path,
        "agents:\n"
        "  - name: r\n"
        "    description: d\n"
        "    model:\n"
        "      claude: opus\n"
        "      opencode: anthropic/claude-opus-4-8\n",
    )
    agent = load_manifest(tmp_path).agents[0]
    assert agent.model == {"claude": "opus", "opencode": "anthropic/claude-opus-4-8"}
    assert agent.model_for("claude") == "opus"
    assert agent.model_for("opencode") == "anthropic/claude-opus-4-8"


def test_manifest_agent_model_map_non_string_value_raises(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "r.md").write_text("body")
    write_manifest(
        tmp_path, "agents:\n  - name: r\n    description: d\n    model:\n      claude: 3\n"
    )
    with pytest.raises(ValueError, match="model"):
        load_manifest(tmp_path)


def test_manifest_loads_settings(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(
        tmp_path,
        "settings:\n"
        "  claude:\n"
        "    outputStyle: Explanatory\n"
        "    cleanupPeriodDays: 14\n"
        "  opencode:\n"
        "    theme: tokyonight\n",
    )
    settings = load_manifest(tmp_path).settings
    assert settings is not None
    assert settings.raw_for("claude") == {"outputStyle": "Explanatory", "cleanupPeriodDays": 14}
    assert settings.raw_for("opencode") == {"theme": "tokyonight"}
    assert settings.available_on == frozenset({"claude", "opencode"})


def test_manifest_without_settings_is_none(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(tmp_path, "rules: []\n")
    assert load_manifest(tmp_path).settings is None


def test_manifest_settings_rejects_reserved_claude_key(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "settings:\n  claude:\n    permissions: {}\n")
    with pytest.raises(ValueError, match="dedicated artifacts"):
        load_manifest(tmp_path)


def test_manifest_settings_rejects_reserved_opencode_key(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "settings:\n  opencode:\n    instructions: []\n")
    with pytest.raises(ValueError, match="dedicated artifacts"):
        load_manifest(tmp_path)


def test_manifest_settings_rejects_non_mapping_block(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "settings:\n  claude: not-a-mapping\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_manifest(tmp_path)


def test_manifest_loads_event_hooks(
    tmp_path: Path, write_manifest: WriteManifest, lint_script: str
) -> None:
    (tmp_path / "event_hooks").mkdir()
    (tmp_path / "event_hooks" / "lint.sh").write_text(lint_script)
    write_manifest(
        tmp_path,
        "event_hooks:\n"
        "  - name: lint\n"
        "    event: after_edit\n"
        "    script: lint.sh\n"
        "  - name: notify\n"
        "    event: on_finish\n"
        '    command: "notify-send done"\n'
        "    available_on: [claude]\n",
    )
    eh = load_manifest(tmp_path).event_hooks
    assert eh is not None
    assert [h.name for h in eh.hooks] == ["lint", "notify"]
    lint, notify = eh.hooks
    assert lint.event == "after_edit"
    assert lint.script_content == lint_script
    assert notify.command == "notify-send done"
    assert notify.script_content == "#!/usr/bin/env sh\nnotify-send done\n"
    assert notify.available_on == frozenset({"claude"})


def test_manifest_without_event_hooks_is_none(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "rules: []\n")
    assert load_manifest(tmp_path).event_hooks is None


def test_manifest_accepts_session_events(tmp_path: Path, write_manifest: WriteManifest) -> None:
    write_manifest(
        tmp_path,
        "event_hooks:\n"
        "  - name: warm\n    event: session_start\n    command: tldr warm .\n"
        "  - name: bye\n    event: session_end\n    command: echo bye\n",
    )
    eh = load_manifest(tmp_path).event_hooks
    assert eh is not None
    assert {h.event for h in eh.hooks} == {"session_start", "session_end"}


def test_manifest_event_hook_rejects_unknown_event(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(tmp_path, "event_hooks:\n  - name: x\n    event: bogus\n    command: echo hi\n")
    with pytest.raises(ValueError, match="unknown 'event'"):
        load_manifest(tmp_path)


def test_manifest_event_hook_requires_exactly_one_body(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(
        tmp_path,
        "event_hooks:\n  - name: x\n    event: on_finish\n    command: echo hi\n    script: x.sh\n",
    )
    with pytest.raises(ValueError, match="exactly one of 'command' or 'script'"):
        load_manifest(tmp_path)


def test_manifest_event_hook_rejects_duplicate_name(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(
        tmp_path,
        "event_hooks:\n"
        "  - name: dup\n    event: on_finish\n    command: a\n"
        "  - name: dup\n    event: on_finish\n    command: b\n",
    )
    with pytest.raises(ValueError, match="duplicate event hook name"):
        load_manifest(tmp_path)


def test_manifest_event_hook_missing_script_raises(
    tmp_path: Path, write_manifest: WriteManifest
) -> None:
    write_manifest(
        tmp_path, "event_hooks:\n  - name: x\n    event: after_edit\n    script: nope.sh\n"
    )
    with pytest.raises(FileNotFoundError, match="event hook script not found"):
        load_manifest(tmp_path)


# --- metadata ---------------------------------------------------------------


def test_manifest_loads_full_metadata(sample_manifest: Path) -> None:
    meta = load_manifest(sample_manifest).meta
    assert meta.name == "sample-stack"
    assert meta.description.startswith("Sample manifest")
    assert meta.version == "0.1.0"
    assert meta.author == "bun-off tests"


def test_manifest_requires_meta_name(tmp_path: Path) -> None:
    (tmp_path / "boff.yaml").write_text("meta:\n  description: d\n")
    with pytest.raises(ValueError, match="meta.name"):
        load_manifest(tmp_path)


def test_manifest_requires_meta_description(tmp_path: Path) -> None:
    (tmp_path / "boff.yaml").write_text("meta:\n  name: t\n")
    with pytest.raises(ValueError, match="meta.description"):
        load_manifest(tmp_path)


def test_manifest_rejects_non_string_meta_field(tmp_path: Path) -> None:
    (tmp_path / "boff.yaml").write_text("meta:\n  name: t\n  description: d\n  version: 3\n")
    with pytest.raises(ValueError, match="meta.version"):
        load_manifest(tmp_path)


# --- extends ----------------------------------------------------------------


def _base_with_child(tmp_path: Path) -> Path:
    """Build a base manifest and a child that extends it; return the child dir."""
    base = tmp_path / "base"
    (base / "rules").mkdir(parents=True)
    (base / "rules" / "shared.md").write_text("base shared")
    (base / "rules" / "only_base.md").write_text("base only")
    (base / "boff.yaml").write_text(
        "meta:\n  name: base\n  description: base\n"
        "rules:\n  - shared\n  - only_base\n"
        "settings:\n  claude:\n    a: 1\n    b: 1\n"
    )
    child = tmp_path / "child"
    (child / "rules").mkdir(parents=True)
    (child / "rules" / "shared.md").write_text("child shared")
    (child / "rules" / "only_child.md").write_text("child only")
    (child / "boff.yaml").write_text(
        "meta:\n  name: child\n  description: child\n"
        "extends: ../base\n"
        "rules:\n  - shared\n  - only_child\n"
        "settings:\n  claude:\n    b: 2\n    c: 3\n"
    )
    return child


def test_extends_merges_and_overrides(tmp_path: Path) -> None:
    m = load_manifest(_base_with_child(tmp_path))
    by_name = {r.name: r.content for r in m.rules}
    assert by_name["only_base"] == "base only"
    assert by_name["only_child"] == "child only"
    assert by_name["shared"] == "child shared"  # child overrides parent
    assert m.extends == ("../base",)
    assert m.meta.name == "child"  # metadata is not inherited
    assert m.settings is not None
    # deep-merge per platform: child wins on conflicts, parent-only keys survive.
    assert m.settings.raw_for("claude") == {"a": 1, "b": 2, "c": 3}


def test_extends_accepts_list(tmp_path: Path) -> None:
    for name in ("p1", "p2"):
        d = tmp_path / name
        (d / "rules").mkdir(parents=True)
        (d / "rules" / f"{name}.md").write_text(name)
        (d / "rules" / "common.md").write_text(name)
        (d / "boff.yaml").write_text(
            f"meta:\n  name: {name}\n  description: {name}\nrules:\n  - {name}\n  - common\n"
        )
    child = tmp_path / "c"
    child.mkdir()
    (child / "boff.yaml").write_text(
        "meta:\n  name: c\n  description: c\nextends:\n  - ../p1\n  - ../p2\n"
    )
    m = load_manifest(child)
    by_name = {r.name: r.content for r in m.rules}
    assert by_name["common"] == "p2"  # later parent wins


def test_extends_cycle_detected(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "boff.yaml").write_text("meta:\n  name: a\n  description: a\nextends: ../b\n")
    (b / "boff.yaml").write_text("meta:\n  name: b\n  description: b\nextends: ../a\n")
    with pytest.raises(ValueError, match="cycle"):
        load_manifest(a)


def test_extends_rejects_bad_type(tmp_path: Path) -> None:
    (tmp_path / "boff.yaml").write_text("meta:\n  name: t\n  description: d\nextends: 3\n")
    with pytest.raises(ValueError, match="extends"):
        load_manifest(tmp_path)
