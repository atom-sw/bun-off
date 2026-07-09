"""Shared fixtures for the boff test suite.

These replace the per-file `_scope` helpers, retyped `FIXTURE` constants, `_deploy_ops`
flatteners, and the duplicated `META` prefix that previously drifted across test modules.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from boff.cli import ExitCode, main
from boff.deploy import deploy_plan
from boff.manifest import Manifest
from boff.types import FileOperation, Operation, Scope, ScopeKind
from boff.verify import CheckReport, Finding, Status

_FIXTURES = Path(__file__).parent / "fixtures"

# Minimal required metadata, prepended to inline manifest bodies under test.
META = "meta:\n  name: t\n  description: d\n"

# The manifest folder and name inside the `bare_repo` fixture's repository.
REMOTE_SUBDIR = "stack"
REMOTE_NAME = "remote"


@pytest.fixture(scope="session")
def sample_manifest() -> Path:
    """Path to the on-disk sample manifest folder exercised across the suite."""
    return _FIXTURES / "sample_manifest"


@pytest.fixture
def make_scope() -> Callable[[Path], Scope]:
    """Return a factory that builds a workspace scope rooted at a given path."""

    def _make(root: Path) -> Scope:
        return Scope(kind=ScopeKind.WORKSPACE, workspace_root=root)

    return _make


@pytest.fixture
def workspace_scope(tmp_path: Path, make_scope: Callable[[Path], Scope]) -> Scope:
    """A workspace scope rooted at the test's ``tmp_path``."""
    return make_scope(tmp_path)


@pytest.fixture
def write_manifest() -> Callable[..., Path]:
    """Return a factory that writes a ``boff.yaml`` with valid meta plus ``body``.

    The factory creates ``root`` if needed and returns it, so callers can pass the
    result straight to ``load_manifest``.
    """

    def _write(root: Path, body: str = "") -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "boff.yaml").write_text(META + body)
        return root

    return _write


@pytest.fixture
def deploy_ops() -> Callable[[Manifest, str, Scope], list[Operation]]:
    """Return a factory flattening the owner-bucketed plan into one op list."""

    def _ops(manifest: Manifest, platform: str, scope: Scope) -> list[Operation]:
        return [op for ops in deploy_plan(manifest, [platform], scope).values() for op in ops]

    return _ops


@pytest.fixture(scope="session")
def lint_script(sample_manifest: Path) -> str:
    """The event-hook script body, read from the fixture so assertions cannot drift."""
    return (sample_manifest / "event_hooks" / "lint.sh").read_text()


@pytest.fixture
def deployed_workspace(
    sample_manifest: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    """A workspace with the sample manifest freshly deployed to claude, and cwd set to it."""
    monkeypatch.chdir(tmp_path)
    assert main(["deploy", str(sample_manifest), "--platform", "claude"]) == ExitCode.OK
    capsys.readouterr()
    return tmp_path


def _git(*args: str, cwd: Path) -> None:
    """Run git with a fixed identity, so the commit does not depend on the user's config."""
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**env})


@pytest.fixture
def bare_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare git repo holding a ``<REMOTE_SUBDIR>/`` manifest on ``main``; return its path.

    Points ``XDG_CACHE_HOME`` at ``tmp_path`` too, so resolving a ``file://`` reference to this
    repo never touches the developer's real clone cache. The repo is named ``repo.git`` because
    a ``file://`` reference carries no ``<owner>/<repo>`` convention: it needs the marker.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    work = tmp_path / "work"
    sub = work / REMOTE_SUBDIR
    sub.mkdir(parents=True)
    (sub / "boff.yaml").write_text(f"meta:\n  name: {REMOTE_NAME}\n  description: r\n")
    _git("init", "-q", "-b", "main", ".", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "init", cwd=work)
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "clone", "--bare", "-q", str(work), str(bare)], check=True, capture_output=True
    )
    return bare


def findings_of(report: CheckReport, status: Status) -> list[Finding]:
    """Return every finding in ``report`` with the given status."""
    return [finding for finding in report.findings if finding.status is status]


def file_op(op: Operation) -> FileOperation:
    """Assert ``op`` writes a file and return it narrowed.

    Adapters return the ``Operation`` union, so a test reading ``.target`` or ``.content``
    must narrow first. Asserting rather than casting means a planner that starts emitting a
    different op fails the test instead of failing only the type checker.
    """
    assert isinstance(op, FileOperation)
    return op


def text_of(op: Operation) -> str:
    """Assert ``op`` writes text (not bytes) and return its content narrowed to ``str``."""
    content = file_op(op).content
    assert isinstance(content, str)
    return content
