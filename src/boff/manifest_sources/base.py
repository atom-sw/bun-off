"""ManifestSource base class: resolve a manifest reference to a local directory."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar


class ManifestSource:
    """Base class for manifest reference resolvers.

    Each source recognizes a family of reference strings via :meth:`matches` and turns a
    matching reference into a local directory containing a ``boff.yaml`` via :meth:`resolve`.
    """

    name: ClassVar[str] = ""

    def matches(self, ref: str) -> bool:  # noqa: ARG002  (abstract: overridden by subclasses)
        """Return True if this source can resolve ``ref``."""
        raise NotImplementedError

    def resolve(self, ref: str, *, base_root: Path) -> Path:  # noqa: ARG002  (abstract)
        """Resolve ``ref`` to a local manifest directory.

        ``base_root`` is the directory of the manifest that declared the reference, used to
        anchor relative local paths.
        """
        raise NotImplementedError
