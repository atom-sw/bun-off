"""Serialize a :class:`ContextBundle` to a standalone ``.tar.gz`` and back.

The archive holds a ``manifest.json`` index plus the content blobs laid out by
section, so it stays human-inspectable while remaining losslessly reconstructable.
``--sanitize`` redacts common secret patterns before writing.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from boff.context.ir import (
    ContextBundle,
    HandoffDigest,
    InstructionsDoc,
    MemoryDoc,
    Plan,
    ProjectIdentity,
    SessionRecord,
    TodoList,
)
from boff.errors import BundleError
from boff.jsonutil import dumps_json

SCHEMA_VERSION = 1

_REDACTED = "[REDACTED]"

# Patterns whose entire match is a secret to drop.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(?:bearer\s+)[A-Za-z0-9._\-]{16,}"),
]

# Key/value assignment: keep the key and separator, redact the value.
_KEYED_SECRET = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)(\s*[:=]\s*)[\"']?[A-Za-z0-9_\-./+]{12,}"
)


def sanitize_text(text: str) -> str:
    """Redact common secret patterns from ``text``."""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return _KEYED_SECRET.sub(rf"\1\2{_REDACTED}", out)


def _safe_leaf(name: str) -> str:
    """Sanitize a string to be a safe path leaf name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "item"


def _add(tar: tarfile.TarFile, arcname: str, text: str) -> None:
    """Add a text file to a tar archive."""
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def write_bundle(bundle: ContextBundle, out_path: Path, *, sanitize: bool = False) -> None:
    """Write ``bundle`` to ``out_path`` as a gzipped tar archive."""
    clean: Callable[[str], str] = sanitize_text if sanitize else (lambda text: text)
    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "platform": bundle.platform,
        "sanitized": sanitize,
        "project": {
            "abs_path": "" if sanitize else bundle.project.abs_path,
            "git_root_commit": bundle.project.git_root_commit,
            "remote": bundle.project.remote,
            "branch": bundle.project.branch,
        },
        "instructions": [],
        "plans": [],
        "memory": [],
        "todos": [],
        "sessions": [],
        "summary": None,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tar:
        for idx, doc in enumerate(bundle.instructions):
            arc = f"instructions/{idx:04d}__{_safe_leaf(Path(doc.relative_path).name)}"
            _add(tar, arc, clean(doc.content))
            manifest["instructions"].append(
                {"path": arc, "relative_path": doc.relative_path, "kind": doc.kind}
            )
        for idx, plan in enumerate(bundle.plans):
            arc = f"plans/{idx:04d}__{_safe_leaf(plan.name)}.md"
            _add(tar, arc, clean(plan.content))
            manifest["plans"].append(
                {"path": arc, "name": plan.name, "origin_path": plan.origin_path}
            )
        for idx, mem in enumerate(bundle.memory):
            arc = f"memory/{idx:04d}__{_safe_leaf(Path(mem.relative_path).name)}"
            _add(tar, arc, clean(mem.content))
            manifest["memory"].append({"path": arc, "relative_path": mem.relative_path})
        for idx, todo in enumerate(bundle.todos):
            arc = f"todos/{idx:04d}__{_safe_leaf(todo.name)}"
            _add(tar, arc, clean(todo.content))
            manifest["todos"].append({"path": arc, "name": todo.name})
        for idx, session in enumerate(bundle.sessions):
            arc = f"sessions/{idx:04d}__{_safe_leaf(Path(session.relative_path).name)}"
            _add(tar, arc, clean(session.content))
            manifest["sessions"].append({"path": arc, "relative_path": session.relative_path})
        if bundle.summary is not None:
            _add(tar, "summary.md", clean(bundle.summary.text))
            manifest["summary"] = "summary.md"

        _add(tar, "manifest.json", dumps_json(manifest))


def read_bundle(path: Path) -> ContextBundle:
    """Read a bundle archive written by :func:`write_bundle`.

    Raises :class:`BundleError` if the archive is unreadable, is missing an
    expected entry, or was written by an incompatible schema version.
    """
    try:
        return _read_bundle(path)
    except (tarfile.TarError, json.JSONDecodeError, KeyError, UnicodeDecodeError) as exc:
        raise BundleError(f"corrupt context bundle {path}: {exc}") from exc


def _read_bundle(path: Path) -> ContextBundle:
    """Read a context bundle from a tar archive."""
    with tarfile.open(path, "r:gz") as tar:

        def text(arcname: str) -> str:
            member = tar.extractfile(arcname)
            if member is None:
                raise BundleError(f"bundle is missing entry '{arcname}'")
            return member.read().decode("utf-8")

        manifest = json.loads(text("manifest.json"))
        schema = manifest.get("schema")
        if schema != SCHEMA_VERSION:
            raise BundleError(
                f"unsupported bundle schema {schema!r} in {path} "
                f"(this boff writes schema {SCHEMA_VERSION})"
            )
        proj = manifest["project"]
        project = ProjectIdentity(
            abs_path=proj.get("abs_path", ""),
            git_root_commit=proj.get("git_root_commit"),
            remote=proj.get("remote"),
            branch=proj.get("branch"),
        )
        instructions = [
            InstructionsDoc(
                relative_path=entry["relative_path"],
                content=text(entry["path"]),
                kind=entry["kind"],
            )
            for entry in manifest["instructions"]
        ]
        plans = [
            Plan(
                name=entry["name"],
                content=text(entry["path"]),
                origin_path=entry.get("origin_path", ""),
            )
            for entry in manifest["plans"]
        ]
        memory = [
            MemoryDoc(relative_path=entry["relative_path"], content=text(entry["path"]))
            for entry in manifest["memory"]
        ]
        todos = [
            TodoList(name=entry["name"], content=text(entry["path"])) for entry in manifest["todos"]
        ]
        sessions = [
            SessionRecord(relative_path=entry["relative_path"], content=text(entry["path"]))
            for entry in manifest["sessions"]
        ]
        summary = HandoffDigest(text=text(manifest["summary"])) if manifest["summary"] else None

    return ContextBundle(
        platform=manifest["platform"],
        project=project,
        instructions=instructions,
        plans=plans,
        memory=memory,
        todos=todos,
        sessions=sessions,
        summary=summary,
    )
