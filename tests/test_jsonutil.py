"""Tests for the shared JSON helpers."""

from boff.jsonutil import dumps_json, json_deep_merge


def test_json_deep_merge_recurses_into_nested_dicts() -> None:
    existing = {"a": {"x": 1, "keep": True}, "top": 1}
    incoming = {"a": {"x": 2, "y": 3}, "new": 4}
    assert json_deep_merge(existing, incoming) == {
        "a": {"x": 2, "keep": True, "y": 3},
        "top": 1,
        "new": 4,
    }


def test_json_deep_merge_replaces_lists_and_scalars_wholesale() -> None:
    assert json_deep_merge({"k": [1, 2]}, {"k": [3]}) == {"k": [3]}
    assert json_deep_merge({"k": 1}, {"k": "s"}) == {"k": "s"}
    assert json_deep_merge({"k": {"nested": 1}}, {"k": 5}) == {"k": 5}


def test_json_deep_merge_does_not_mutate_inputs() -> None:
    existing = {"a": {"x": 1}}
    json_deep_merge(existing, {"a": {"y": 2}})
    assert existing == {"a": {"x": 1}}


def test_dumps_json_uses_two_space_indent_and_trailing_newline() -> None:
    assert dumps_json({"b": 1, "a": 2}) == '{\n  "b": 1,\n  "a": 2\n}\n'
