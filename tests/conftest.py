"""Shared fixtures for the boff test suite.

These replace the per-file `_scope` helpers, retyped `FIXTURE` constants, `_deploy_ops`
flatteners, and the duplicated `META` prefix that previously drifted across test modules.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Mapping
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

# The rule ``r`` in the `content_repo` fixture, at tag ``v1`` and on ``main``.
REMOTE_RULE_V1 = "# rule, first version\n"
REMOTE_RULE_V2 = "# rule, second version\n"

# The minimum a SKILL.md needs to load: every platform discovers a skill through these keys,
# and `_load_skills` rejects a file that omits them.
SKILL_BODY = "---\nname: s\ndescription: A test skill.\n---\n\n# skill\n"

# Supporting files of the `write_skill_dir` fixture's directory-form skill.
SKILL_SUPPORT = {
    "references/contract.md": "# the contract\n",
    "templates/probe.py": "print('probe')\n",
}


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
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate ``$HOME`` and ``$XDG_CONFIG_HOME`` under ``tmp_path``.

    Every global-scope path is resolved from these at call time, so a test that touches the
    global scope must take this fixture or it would read and write the real user config.
    """
    home = tmp_path / "home"
    home.mkdir()
    # `Path.home()` expands `~` from `$HOME` on POSIX, so setting the variable is enough.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


@pytest.fixture
def global_scope(fake_home: Path) -> Scope:
    """A user-level scope, with the home directory relocated under ``tmp_path``."""
    del fake_home
    return Scope(kind=ScopeKind.GLOBAL)


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
def write_skill_dir() -> Callable[..., Path]:
    """Return a factory writing a directory-form skill under ``<root>/skills/<name>/``.

    The default supporting files sit at two depths, so a test also pins that the subtree's
    own layout survives the round trip rather than being flattened.
    """

    def _write(
        root: Path,
        name: str,
        body: str = SKILL_BODY,
        support: Mapping[str, str] = SKILL_SUPPORT,
    ) -> Path:
        skill_root = root / "skills" / name
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(body)
        for rel, text in support.items():
            target = skill_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return skill_root

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


def _bare_clone(work: Path, bare: Path) -> Path:
    """Clone the committed ``work`` tree into a bare repo at ``bare``; return ``bare``."""
    subprocess.run(
        ["git", "clone", "--bare", "-q", str(work), str(bare)], check=True, capture_output=True
    )
    return bare


def _commit_files(work: Path, files: Mapping[str, str], message: str) -> None:
    """Write ``files`` under ``work`` and commit them."""
    for rel, text in files.items():
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", message, cwd=work)


def push_commit(bare: Path, files: Mapping[str, str]) -> None:
    """Commit ``files`` on top of ``main`` in ``bare``, as an upstream push would."""
    work = Path(tempfile.mkdtemp(dir=bare.parent))
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    _commit_files(work, files, "upstream")
    _git("push", "-q", "origin", "main", cwd=work)


def head_of(bare: Path) -> str:
    """Return the commit ``main`` points at in ``bare``."""
    return subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "main"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def bare_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare git repo holding a ``<REMOTE_SUBDIR>/`` manifest on ``main``; return its path.

    Points ``XDG_CACHE_HOME`` at ``tmp_path`` too, so resolving a ``file://`` reference to this
    repo never touches the developer's real clone cache. The repo is named ``repo.git`` because
    a ``file://`` reference carries no ``<owner>/<repo>`` convention: it needs the marker.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=work)
    _commit_files(
        work,
        {f"{REMOTE_SUBDIR}/boff.yaml": f"meta:\n  name: {REMOTE_NAME}\n  description: r\n"},
        "init",
    )
    return _bare_clone(work, tmp_path / "repo.git")


@pytest.fixture
def content_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare git repo of skills and rules with no ``boff.yaml``, as ``from:`` references it.

    It holds a directory-form skill ``s``, a flat skill ``flat``, a skill ``inert`` without
    frontmatter, and a rule ``r``. The rule reads ``REMOTE_RULE_V1`` at tag ``v1`` and
    ``REMOTE_RULE_V2`` on ``main``, so a test can load two refs of one repository.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    work = tmp_path / "content"
    work.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=work)
    skill_dir = {"skills/s/SKILL.md": SKILL_BODY} | {
        f"skills/s/{rel}": text for rel, text in SKILL_SUPPORT.items()
    }
    _commit_files(
        work,
        skill_dir
        | {
            "skills/flat.md": SKILL_BODY.replace("name: s", "name: flat"),
            "skills/inert/SKILL.md": "# no frontmatter\n",
            "rules/r.md": REMOTE_RULE_V1,
        },
        "v1",
    )
    _git("tag", "v1", cwd=work)
    _commit_files(work, {"rules/r.md": REMOTE_RULE_V2}, "v2")
    return _bare_clone(work, tmp_path / "content.git")


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
