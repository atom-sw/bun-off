"""Exception hierarchy for boff.

All user-facing failures derive from :class:`BoffError`, which the CLI catches once
in ``main`` to print a single-line message and exit non-zero instead of dumping a
traceback. A bare exception escaping to the user signals a bug, not user error.
"""

from __future__ import annotations


class BoffError(Exception):
    """Base for user-facing boff errors: printed without a traceback."""


class ManifestError(BoffError):
    """A manifest folder or ``boff.yaml`` is malformed or unreadable."""


class BundleError(BoffError):
    """A context bundle archive is corrupt or unreadable."""


class HookError(BoffError):
    """A lifecycle hook failed, timed out, or was invoked with malformed input."""


class RegistryError(BoffError):
    """Lookup of an unregistered name in a boff registry (unknown platform, source, ...)."""
