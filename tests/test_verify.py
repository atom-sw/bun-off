import json
from pathlib import Path
from typing import Any

import pytest

from boff import verify as verify_module
from boff.deploy import PlannedUnit, Support
from boff.jsonutil import dumps_json
from boff.state import DeployState, OwnerRecord
from boff.types import (
    DeleteOperation,
    FileOperation,
    MergeStrategy,
    Operation,
    PruneKeysOperation,
    Scope,
    ShellAction,
)
from boff.verify import (
    CheckReport,
    Status,
    _Exact,  # pyright: ignore[reportPrivateUsage]
    _Owned,  # pyright: ignore[reportPrivateUsage]
    fold_expectations,
    probe,
    verify,
)
from tests.conftest import findings_of

EMPTY_STATE = DeployState()


def _overwrite(target: Path, content: str | bytes) -> FileOperation:
    return FileOperation(target=target, content=content, merge=MergeStrategy.OVERWRITE)


def _merge(target: Path, payload: dict[str, Any]) -> FileOperation:
    return FileOperation(target=target, content=json.dumps(payload), merge=MergeStrategy.MERGE)


def _unit(
    *ops: Operation,
    owner: str = "claude",
    label: str = "rule style",
    support: Support = Support.RENDERED,
) -> PlannedUnit:
    return PlannedUnit(owner=owner, label=label, ops=list(ops), platform=owner, support=support)


def _check(*units: PlannedUnit, scope: Scope, prior: DeployState = EMPTY_STATE) -> CheckReport:
    return verify(units, prior, scope, ["claude"])


# --- fold_expectations -------------------------------------------------------------------


def test_fold_expectations_keeps_only_the_last_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    winner = _overwrite(target, "second")
    expectation = fold_expectations([_overwrite(target, "first"), winner])[target]

    assert expectation == _Exact("second", writer=winner)


def test_fold_expectations_deep_merges_a_merge_only_target(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    ops = [_merge(target, {"a": {"b": 1}}), _merge(target, {"a": {"c": 2}})]

    assert fold_expectations(ops)[target] == _Owned({"a": {"b": 1, "c": 2}})


def test_fold_expectations_merges_into_the_content_a_preceding_overwrite_wrote(
    tmp_path: Path,
) -> None:
    # An OVERWRITE resets the file, so the MERGE that follows lands on known content: the
    # target is exactly what the executor would leave behind, not a shared file.
    target = tmp_path / "settings.json"
    writer = _overwrite(target, dumps_json({"a": 1}))
    ops = [_merge(target, {"ignored": True}), writer, _merge(target, {"b": 2})]

    assert fold_expectations(ops)[target] == _Exact(dumps_json({"a": 1, "b": 2}), writer=writer)


def test_fold_expectations_ignores_non_file_operations(tmp_path: Path) -> None:
    assert fold_expectations([ShellAction(argv=["true"]), _overwrite(tmp_path / "f", "x")]) == {
        tmp_path / "f": _Exact("x", writer=_overwrite(tmp_path / "f", "x"))
    }


# --- whole-file (_Exact) comparison ------------------------------------------------------


@pytest.mark.parametrize(
    ("on_disk", "expected"),
    [
        pytest.param("planned", Status.OK, id="matching"),
        pytest.param("tampered", Status.DRIFTED, id="edited"),
        pytest.param(None, Status.MISSING, id="deleted"),
    ],
)
def test_overwrite_target_reports_its_state(
    tmp_path: Path, workspace_scope: Scope, on_disk: str | None, expected: Status
) -> None:
    content = "planned"
    target = tmp_path / "rule.md"
    if on_disk is not None:
        target.write_text(on_disk)

    report = _check(_unit(_overwrite(target, content)), scope=workspace_scope)

    assert [f.status for f in report.findings] == [expected]
    assert report.failed is (expected is not Status.OK)


def test_bytes_content_is_compared_as_bytes(tmp_path: Path, workspace_scope: Scope) -> None:
    content = b"\x00\x01binary"
    target = tmp_path / "blob.bin"
    target.write_bytes(content)

    assert not _check(_unit(_overwrite(target, content)), scope=workspace_scope).failed


def test_a_directory_where_a_file_belongs_reports_missing(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "rule.md"
    target.mkdir()

    report = _check(_unit(_overwrite(target, "planned")), scope=workspace_scope)

    assert findings_of(report, Status.MISSING)


def test_undecodable_bytes_where_text_belongs_reports_drift(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "rule.md"
    target.write_bytes(b"\xff\xfe not utf-8")

    report = _check(_unit(_overwrite(target, "planned")), scope=workspace_scope)

    assert findings_of(report, Status.DRIFTED)


def test_an_overwrite_superseded_by_a_later_one_is_not_compared(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    # Only the last writer's content ever reaches disk, so the earlier op must not report drift.
    target = tmp_path / "f.md"
    target.write_text("second")

    report = _check(
        _unit(_overwrite(target, "first"), _overwrite(target, "second")), scope=workspace_scope
    )

    assert [f.status for f in report.findings] == [Status.OK]


# --- merged-file (_Owned) comparison -----------------------------------------------------


def test_merge_target_reports_a_drifted_key_with_both_values(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"permissions": {"allow": ["Bash(rm:*)"]}}))

    report = _check(
        _unit(_merge(target, {"permissions": {"allow": ["Read"]}})), scope=workspace_scope
    )

    (finding,) = findings_of(report, Status.DRIFTED)
    assert "permissions.allow" in finding.detail
    assert "['Read']" in finding.detail and "['Bash(rm:*)']" in finding.detail


def test_merge_target_reports_a_key_boff_wrote_and_the_user_deleted(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"other": 1}))

    report = _check(_unit(_merge(target, {"theme": "dark"})), scope=workspace_scope)

    (finding,) = findings_of(report, Status.DRIFTED)
    assert finding.detail == "missing key theme"


def test_merge_target_ignores_keys_the_user_added(tmp_path: Path, workspace_scope: Scope) -> None:
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"theme": "dark", "env": {"FOO": "1"}}))

    report = _check(_unit(_merge(target, {"theme": "dark"})), scope=workspace_scope)

    assert not report.failed
    assert [f.status for f in report.findings] == [Status.OK]


def test_a_leaf_a_later_op_replaced_is_not_compared(tmp_path: Path, workspace_scope: Scope) -> None:
    # The plan's own last-wins fold drops `a.b`, so only `a` == 5 is boff's expectation.
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"a": 5}))

    report = _check(
        _unit(_merge(target, {"a": {"b": 1}}), _merge(target, {"a": 5})), scope=workspace_scope
    )

    assert not report.failed


def test_an_empty_dict_leaf_compares_equal(tmp_path: Path, workspace_scope: Scope) -> None:
    # `ClaudeAdapter._permissions` emits `{"permissions": {}}` when every rule is scoped away,
    # and `leaf_paths` owns that empty dict wholesale at its own key path.
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"permissions": {}}))

    assert not _check(_unit(_merge(target, {"permissions": {}})), scope=workspace_scope).failed


def test_a_merge_target_holding_invalid_json_reports_drift_rather_than_raising(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{ not json")

    report = _check(_unit(_merge(target, {"theme": "dark"})), scope=workspace_scope)

    (finding,) = findings_of(report, Status.DRIFTED)
    assert finding.detail == "not valid JSON"


def test_a_missing_merge_target_reports_missing(tmp_path: Path, workspace_scope: Scope) -> None:
    report = _check(_unit(_merge(tmp_path / "settings.json", {"a": 1})), scope=workspace_scope)

    assert findings_of(report, Status.MISSING)


def test_identical_findings_from_different_units_collapse(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    # OpenCode re-emits its instructions-glob MERGE op once per rule: one drifted key, not N.
    target = tmp_path / "opencode.json"
    target.write_text(dumps_json({"instructions": ["stale"]}))
    payload = {"instructions": [".opencode/rules/**/*.md"]}

    report = _check(
        _unit(_merge(target, payload), label="rule one"),
        _unit(_merge(target, payload), label="rule two"),
        scope=workspace_scope,
    )

    (finding,) = findings_of(report, Status.DRIFTED)
    assert finding.label == "rule one"


# --- support verdicts --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("support", "expected"),
    [
        pytest.param(Support.DROPPED, [Status.DROPPED], id="dropped-is-reported"),
        pytest.param(Support.UNSUPPORTED, [Status.UNSUPPORTED], id="unsupported-is-reported"),
        pytest.param(Support.SHADOWED, [], id="shadowed-is-silent"),
        pytest.param(Support.RENDERED, [], id="rendered-with-no-ops-is-silent"),
    ],
)
def test_support_verdict_drives_the_finding(
    workspace_scope: Scope, support: Support, expected: list[Status]
) -> None:
    report = _check(_unit(support=support), scope=workspace_scope)

    assert [f.status for f in report.findings] == expected
    assert not report.failed


def test_a_shell_action_is_unverifiable(workspace_scope: Scope) -> None:
    report = _check(_unit(ShellAction(argv=["echo"], description="say hi")), scope=workspace_scope)

    (finding,) = report.findings
    assert finding.status is Status.UNVERIFIABLE
    assert finding.detail == "say hi"
    assert not report.failed


# --- stale footprint ---------------------------------------------------------------------


def test_an_orphaned_file_still_on_disk_is_stale(tmp_path: Path, workspace_scope: Scope) -> None:
    orphan = tmp_path / "old.md"
    orphan.write_text("left behind")
    prior = DeployState(scopes={"workspace": {"claude": OwnerRecord(files=["old.md"])}})

    report = _check(scope=workspace_scope, prior=prior)

    (finding,) = findings_of(report, Status.STALE)
    assert finding.target == orphan
    assert finding.owner == "claude"


def test_an_orphaned_file_already_gone_is_not_stale(tmp_path: Path, workspace_scope: Scope) -> None:
    # The executor no-ops on an absent delete target, so nothing is actually wrong here.
    del tmp_path
    prior = DeployState(scopes={"workspace": {"claude": OwnerRecord(files=["old.md"])}})

    assert not _check(scope=workspace_scope, prior=prior).failed


def test_orphaned_merge_keys_still_on_disk_are_stale(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(dumps_json({"outputStyle": "Explanatory"}))
    prior = DeployState(
        scopes={"workspace": {"claude": OwnerRecord(merged={"settings.json": [("outputStyle",)]})}}
    )

    report = _check(scope=workspace_scope, prior=prior)

    (finding,) = findings_of(report, Status.STALE)
    assert finding.detail == "outputStyle"


def test_orphaned_merge_keys_already_pruned_are_not_stale(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    (tmp_path / "settings.json").write_text(dumps_json({"model": "opus"}))
    prior = DeployState(
        scopes={"workspace": {"claude": OwnerRecord(merged={"settings.json": [("outputStyle",)]})}}
    )

    assert not _check(scope=workspace_scope, prior=prior).failed


def test_an_owner_outside_the_active_set_is_not_reconciled(
    tmp_path: Path, workspace_scope: Scope
) -> None:
    # Checking --platform claude must never report opencode's footprint as stale.
    (tmp_path / "old.md").write_text("opencode's")
    prior = DeployState(scopes={"workspace": {"opencode": OwnerRecord(files=["old.md"])}})

    assert not verify([], prior, workspace_scope, ["claude"]).failed


# --- probe -------------------------------------------------------------------------------


def test_probe_maps_each_platform_to_its_resolved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = {"claude": "/usr/bin/claude", "agy": None}
    monkeypatch.setattr(verify_module.shutil, "which", resolved.get)

    assert probe(["claude", "antigravity"]) == {"claude": "/usr/bin/claude", "antigravity": None}


# --- report ------------------------------------------------------------------------------


def test_counts_tallies_per_status_and_omits_zeroes(tmp_path: Path, workspace_scope: Scope) -> None:
    (tmp_path / "ok.md").write_text("same")

    report = _check(
        _unit(_overwrite(tmp_path / "ok.md", "same")),
        _unit(_overwrite(tmp_path / "gone.md", "x"), label="rule gone"),
        scope=workspace_scope,
    )

    assert report.counts() == {Status.OK: 1, Status.MISSING: 1}


def test_cleanup_ops_never_reach_the_workspace(tmp_path: Path, workspace_scope: Scope) -> None:
    # `verify` is read-only: it plans a delete and a prune but applies neither.
    orphan = tmp_path / "old.md"
    orphan.write_text("left behind")
    settings = tmp_path / "settings.json"
    settings.write_text(dumps_json({"outputStyle": "Explanatory"}))
    prior = DeployState(
        scopes={
            "workspace": {
                "claude": OwnerRecord(
                    files=["old.md"], merged={"settings.json": [("outputStyle",)]}
                )
            }
        }
    )

    _check(scope=workspace_scope, prior=prior)

    assert orphan.read_text() == "left behind"
    assert json.loads(settings.read_text()) == {"outputStyle": "Explanatory"}


def test_unhandled_operation_kinds_are_reported_not_crashed(workspace_scope: Scope) -> None:
    ops: list[Operation] = [
        DeleteOperation(target=Path("/nowhere"), description="delete"),
        PruneKeysOperation(target=Path("/nowhere"), key_paths=[("a",)], description="prune"),
    ]
    report = _check(_unit(*ops), scope=workspace_scope)

    assert [f.status for f in report.findings] == [Status.UNVERIFIABLE] * 2
