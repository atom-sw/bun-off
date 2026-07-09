"""Local manifest source: resolve filesystem paths (the catch-all reference form)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from boff.errors import ManifestError
from boff.manifest_sources.base import ManifestSource


class LocalManifestSource(ManifestSource):
    """Resolve a reference as a local filesystem path, relative to the extending manifest."""

    name: ClassVar[str] = "local"

    def matches(self, ref: str) -> bool:  # noqa: ARG002  (catch-all: accepts any reference)
        # Anything not claimed by a remote source is treated as a local path.
        return True

    def resolve(self, ref: str, *, base_root: Path) -> Path:
        candidate = Path(ref)
        root = (candidate if candidate.is_absolute() else base_root / candidate).resolve()
        if not (root / "boff.yaml").is_file():
            raise ManifestError(f"no boff.yaml in {root}")
        return root
