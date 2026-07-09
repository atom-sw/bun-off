"""Git manifest source: fetch a manifest from a remote git repo and cache it locally.

A reference is any remote URL, optionally naming a subdirectory and a git ref. Three spellings
resolve to the same manifest, so a URL copied from a browser works as-is::

    https://host/org/repo.git/sub/dir@v1.2.0   explicit '.git' marker, any host
    https://host/org/repo/sub/dir@v1.2.0       bare forge URL
    https://host/org/repo/tree/v1.2.0/sub/dir  browser URL

Resolution happens at manifest load time, so this shells out to ``git`` directly rather than
emitting deploy operations.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from boff.errors import ManifestError
from boff.manifest_sources.base import ManifestSource

_REMOTE_PREFIXES = ("http://", "https://", "ssh://", "git://", "file://", "git+")

# The segment a forge puts between the repository and the ref in a browser URL. GitLab prefixes
# it with a "-" segment.
_WEB_MARKERS = ("tree", "blob")

# Path segments in the "<owner>/<repo>" every forge puts at the root of a project URL.
_OWNER_REPO = 2


@dataclass(frozen=True)
class GitRef:
    """A parsed git manifest reference."""

    repo_url: str
    subdir: str  # "" when the manifest sits at the repo root
    ref: str | None  # None means the remote default branch


@dataclass(frozen=True)
class _Boundary:
    """Where the repository ends within a reference's path segments."""

    repo_len: int  # segments forming the repository URL
    subdir_start: int  # first segment of the subdirectory
    ref: str | None  # the git ref a browser URL names, if any


def _split_authority(text: str) -> tuple[str, str]:
    """Split ``<scheme>://<authority>`` off ``text``, returning it and the remaining path.

    The returned path never has a leading slash. A ``file://`` URL has an empty authority, so
    its base ends in ``//`` and rejoining base and path reproduces ``file:///abs/path``.
    """
    scheme, sep, rest = text.partition("://")
    if not sep:
        raise ManifestError(f"git manifest reference must be a URL: {text!r}")
    authority, _, path = rest.partition("/")
    return f"{scheme}://{authority}", path


def _find_boundary(segments: list[str], ref: str, *, has_authority: bool) -> _Boundary:
    """Locate the repository boundary in a reference's path ``segments``.

    A segment named ``<repo>.git`` ends the repository on any host. Failing that, a browser
    URL's ``tree``/``blob`` marker ends it and names the git ref. Failing that, the first two
    segments are ``<owner>/<repo>``, which only holds for a URL with a host: a ``file://`` path
    has arbitrary depth and no such convention. ``ref`` is quoted in error messages.
    """
    for i, segment in enumerate(segments):
        if segment.endswith(".git"):
            return _Boundary(repo_len=i + 1, subdir_start=i + 1, ref=None)
    for i, segment in enumerate(segments[:-1]):
        # A marker needs an owner and a repo before it, and a git ref after it.
        if segment in _WEB_MARKERS and i >= _OWNER_REPO:
            repo_len = i - 1 if segments[i - 1] == "-" else i
            if repo_len >= _OWNER_REPO:
                return _Boundary(repo_len=repo_len, subdir_start=i + 2, ref=segments[i + 1])
    if has_authority and len(segments) >= _OWNER_REPO:
        return _Boundary(repo_len=_OWNER_REPO, subdir_start=_OWNER_REPO, ref=None)
    raise ManifestError(
        f"cannot tell where the repository ends in git manifest reference {ref!r}: "
        "give the repository a '.git' suffix, or use a '/tree/<ref>/' URL"
    )


def parse_git_ref(ref: str) -> GitRef:
    """Split a git manifest reference into repository URL, subdirectory, and git ref."""
    base, path = _split_authority(ref.removeprefix("git+"))
    path, sep, ref_part = path.partition("@")
    if sep and not ref_part:
        raise ManifestError(f"git manifest reference has an empty '@<ref>': {ref!r}")

    segments = [segment for segment in path.split("/") if segment]
    at = _find_boundary(segments, ref, has_authority=not base.endswith("//"))
    return GitRef(
        repo_url=f"{base}/{'/'.join(segments[: at.repo_len])}",
        subdir="/".join(segments[at.subdir_start :]),
        # An explicit '@<ref>' wins: it is the only way to name a branch containing a slash.
        ref=ref_part if sep else at.ref,
    )


def _cache_root() -> Path:
    """Resolve the cache root directory for git sources."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "boff" / "git"


def _cache_dir(repo_url: str) -> Path:
    """Resolve the cache directory for a specific git repository URL.

    The key drops a trailing ``.git``, so both spellings of a repository share one clone.
    """
    digest = hashlib.sha256(repo_url.removesuffix(".git").encode()).hexdigest()[:16]
    return _cache_root() / digest


def _git(*args: str, cwd: Path | None = None) -> None:
    """Execute a git command."""
    try:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(["git", *args])
        raise ManifestError(f"git command failed ({cmd}):\n{exc.stderr}") from exc


class GitManifestSource(ManifestSource):
    """Clone or update a remote git repo into a local cache, returning the manifest subdir."""

    name: ClassVar[str] = "git"

    def matches(self, ref: str) -> bool:
        return ref.startswith(_REMOTE_PREFIXES)

    def resolve(self, ref: str, *, base_root: Path) -> Path:  # noqa: ARG002  (remote: no base)
        parsed = parse_git_ref(ref)
        cache = _cache_dir(parsed.repo_url)
        if (cache / ".git").is_dir():
            _git("-C", str(cache), "fetch", "--tags", "--force", "origin")
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            _git("clone", "--filter=blob:none", parsed.repo_url, str(cache))
        self._checkout(cache, parsed.ref)
        root = (cache / parsed.subdir).resolve() if parsed.subdir else cache
        if not (root / "boff.yaml").is_file():
            raise ManifestError(f"no boff.yaml in {root}, resolved from {ref}")
        return root

    @staticmethod
    def _checkout(cache: Path, ref: str | None) -> None:
        """Checkout a specific git reference in the cache."""
        if ref is None:
            # Stay on the default branch; refresh it to the fetched upstream when possible.
            try:
                _git("-C", str(cache), "reset", "--hard", "@{u}")
            except ManifestError:
                pass
            return
        _git("-C", str(cache), "checkout", "--force", ref)
        # Branch refs: fast-forward to the fetched tip. Tags/SHAs have no origin/<ref>; ignore.
        try:
            _git("-C", str(cache), "reset", "--hard", f"origin/{ref}")
        except ManifestError:
            pass
