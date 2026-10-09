"""Manifest source registry: resolve a manifest reference to a local directory.

Both the CLI's positional manifest argument and every ``extends`` reference come through here,
so the two share one grammar.

Sources are tried in order; the first whose :meth:`~ManifestSource.matches` returns True wins.
:class:`LocalManifestSource` is the catch-all, so it must come last.
"""

from __future__ import annotations

from pathlib import Path

from boff.errors import ManifestError
from boff.manifest_sources.base import ManifestSource
from boff.manifest_sources.git import GitManifestSource, fetch
from boff.manifest_sources.local import LocalManifestSource

_SOURCES: list[ManifestSource] = [GitManifestSource(), LocalManifestSource()]


def resolve_ref(ref: str, *, base_root: Path) -> Path:
    """Resolve a manifest reference to a local directory containing ``boff.yaml``."""
    for source in _SOURCES:
        if source.matches(ref):
            return source.resolve(ref, base_root=base_root)
    raise ManifestError(f"no manifest source can resolve reference: {ref!r}")


__all__ = [
    "GitManifestSource",
    "LocalManifestSource",
    "ManifestSource",
    "fetch",
    "resolve_ref",
]
