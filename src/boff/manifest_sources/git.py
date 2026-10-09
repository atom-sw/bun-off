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
import shutil
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
    """A parsed git reference: a manifest's ``extends`` or a skill or rule's ``from``."""

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
        raise ManifestError(f"git reference must be a URL: {text!r}")
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
        f"cannot tell where the repository ends in git reference {ref!r}: "
        "give the repository a '.git' suffix, or use a '/tree/<ref>/' URL"
    )


def parse_git_ref(ref: str) -> GitRef:
    """Split a git reference into repository URL, subdirectory, and git ref."""
    base, path = _split_authority(ref.removeprefix("git+"))
    path, sep, ref_part = path.partition("@")
    if sep and not ref_part:
        raise ManifestError(f"git reference has an empty '@<ref>': {ref!r}")

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


def _git(*args: str, cwd: Path | None = None) -> str:
    """Execute a git command and return its standard output."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(["git", *args])
        raise ManifestError(f"git command failed ({cmd}):\n{exc.stderr}") from exc
    return done.stdout


_fetched: set[Path] = set()
"""Clones fetched, and commit checkouts verified, by this process.

A bundle listing nine skills from one repository resolves nine references to it, and each
would otherwise cost a network round trip. A later boff run starts empty, so it fetches again
and a branch reference picks up new upstream commits.
"""


def _commit_of(clone: Path, ref: str | None) -> str:
    """Resolve ``ref`` to a commit SHA in a freshly fetched ``clone``.

    A branch resolves through ``origin/<ref>``, not the clone's own stale branch of that name,
    so it follows the remote tip. A tag or (abbreviated) SHA resolves as itself. No ref means
    the remote's default branch.
    """
    if ref is None:
        try:
            return _git("-C", str(clone), "rev-parse", "--verify", "origin/HEAD^{commit}").strip()
        except ManifestError:
            # A clone made by an older boff can lack origin/HEAD: ask the remote once.
            _git("-C", str(clone), "remote", "set-head", "origin", "--auto")
            return _git("-C", str(clone), "rev-parse", "--verify", "origin/HEAD^{commit}").strip()
    try:
        return _git("-C", str(clone), "rev-parse", "--verify", f"origin/{ref}^{{commit}}").strip()
    except ManifestError:
        return _git("-C", str(clone), "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _checkout(clone: Path, sha: str) -> Path:
    """Return a checkout of commit ``sha`` that no other reference will move.

    Every commit gets its own worktree beside the clone. Paths kept past manifest load (hook
    scripts, mise files, local plugin folders) then keep the content of the ref they came from,
    however many other refs of the same repository the run resolves. A reused checkout is reset,
    so an edit made inside the cache does not survive into a later run.
    """
    tree = clone.parent / f"{clone.name}-commits" / sha
    if tree in _fetched:
        return tree
    if (tree / ".git").is_file():
        _git("-C", str(tree), "reset", "--hard", "--quiet", sha)
    else:
        if tree.exists():
            shutil.rmtree(tree)  # half-made by an interrupted run
        # Forget worktrees whose directory is gone, so their paths can be registered again.
        _git("-C", str(clone), "worktree", "prune")
        _git("-C", str(clone), "worktree", "add", "--detach", "--force", str(tree), sha)
    _fetched.add(tree)
    return tree


def fetch(ref: str) -> Path:
    """Fetch the repository a git reference names and return its subdirectory, checked out.

    The first reference to a repository in a process clones or fetches it; later ones reuse
    that fetch. Each commit is checked out in a directory of its own (see :func:`_checkout`).
    """
    parsed = parse_git_ref(ref)
    clone = _cache_dir(parsed.repo_url)
    if clone not in _fetched:
        if (clone / ".git").is_dir():
            _git("-C", str(clone), "fetch", "--tags", "--force", "origin")
        else:
            clone.parent.mkdir(parents=True, exist_ok=True)
            _git("clone", "--filter=blob:none", "--no-checkout", parsed.repo_url, str(clone))
        _fetched.add(clone)
    tree = _checkout(clone, _commit_of(clone, parsed.ref))
    root = (tree / parsed.subdir).resolve() if parsed.subdir else tree
    if not root.is_dir():
        raise ManifestError(f"no directory {parsed.subdir!r} in {parsed.repo_url}, from {ref}")
    return root


class GitManifestSource(ManifestSource):
    """Clone or update a remote git repo into a local cache, returning the manifest subdir."""

    name: ClassVar[str] = "git"

    def matches(self, ref: str) -> bool:
        return ref.startswith(_REMOTE_PREFIXES)

    def resolve(self, ref: str, *, base_root: Path) -> Path:  # noqa: ARG002  (remote: no base)
        root = fetch(ref)
        if not (root / "boff.yaml").is_file():
            raise ManifestError(f"no boff.yaml in {root}, resolved from {ref}")
        return root
