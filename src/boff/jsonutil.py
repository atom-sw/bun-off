"""Shared JSON helpers: object narrowing, the deep-merge semantics, and the canonical dump format.

Both the executor (which applies MERGE ops) and ``state.leaf_paths`` (which records the
keys boff owns) depend on the exact deep-merge rule, so it lives here as the single source.
"""

from __future__ import annotations

import json
from typing import Any, cast


def as_json_object(value: Any) -> dict[str, Any] | None:
    """Return ``value`` as a JSON object, or None when it is not a mapping.

    ``json.loads`` and ``yaml.safe_load`` return ``Any``, and narrowing that with
    ``isinstance`` yields an unparameterized ``dict``: this is where a decoded payload
    becomes typed.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def as_json_array(value: Any) -> list[Any] | None:
    """Return ``value`` as a JSON array, or None when it is not a list."""
    return cast("list[Any]", value) if isinstance(value, list) else None


def json_deep_merge(existing: Any, incoming: Any) -> Any:
    """Recursively merge ``incoming`` into ``existing``: dicts recurse, others replace."""
    existing_object = as_json_object(existing)
    incoming_object = as_json_object(incoming)
    if existing_object is None or incoming_object is None:
        return incoming
    out = dict(existing_object)
    for key, value in incoming_object.items():
        out[key] = json_deep_merge(out[key], value) if key in out else value
    return out


def dumps_json(data: Any) -> str:
    """Serialize ``data`` to boff's canonical JSON: two-space indent, trailing newline."""
    return json.dumps(data, indent=2) + "\n"
