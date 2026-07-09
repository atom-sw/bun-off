from pathlib import Path

import pytest

from boff.errors import ManifestError
from boff.manifest_sources import resolve_ref
from boff.manifest_sources.git import (
    GitManifestSource,
    _cache_dir,  # pyright: ignore[reportPrivateUsage]  (the cache key has no public surface)
    parse_git_ref,
)
from tests.conftest import REMOTE_SUBDIR

_BUNDLES = "https://github.com/atom-sw/bun-off-bundles"


@pytest.mark.parametrize(
    ("ref", "repo_url", "subdir", "git_ref"),
    [
        pytest.param(
            "https://example.com/org/repo.git/sub/dir@v1.2.3",
            "https://example.com/org/repo.git",
            "sub/dir",
            "v1.2.3",
            id="dot-git-with-subdir-and-ref",
        ),
        pytest.param(
            "https://example.com/org/repo.git",
            "https://example.com/org/repo.git",
            "",
            None,
            id="dot-git-alone",
        ),
        pytest.param(
            "https://example.com/org/repo.git@main",
            "https://example.com/org/repo.git",
            "",
            "main",
            id="dot-git-with-ref",
        ),
        pytest.param(
            "git+https://example.com/org/repo.git@main",
            "https://example.com/org/repo.git",
            "",
            "main",
            id="git-plus-prefix-stripped",
        ),
        pytest.param(
            "https://gitlab.com/org/subgroup/repo.git/sub",
            "https://gitlab.com/org/subgroup/repo.git",
            "sub",
            None,
            id="dot-git-reaches-a-nested-group",
        ),
        pytest.param(
            "ssh://git@example.com/org/repo.git@v1",
            "ssh://git@example.com/org/repo.git",
            "",
            "v1",
            id="at-in-userinfo-is-not-the-ref-separator",
        ),
        pytest.param(f"{_BUNDLES}/python@main", _BUNDLES, "python", "main", id="bare-forge-url"),
        pytest.param(f"{_BUNDLES}/", _BUNDLES, "", None, id="bare-repo-root-trailing-slash"),
        pytest.param(f"{_BUNDLES}/tree/main/python", _BUNDLES, "python", "main", id="web-tree-url"),
        pytest.param(
            "https://example.com/org/repo/blob/v1.0/sub/dir",
            "https://example.com/org/repo",
            "sub/dir",
            "v1.0",
            id="web-blob-url",
        ),
        pytest.param(
            "https://gitlab.com/org/repo/-/tree/main/sub",
            "https://gitlab.com/org/repo",
            "sub",
            "main",
            id="gitlab-dash-tree-url",
        ),
        pytest.param(
            "https://example.com/org/repo/tree/main/sub@v2",
            "https://example.com/org/repo",
            "sub",
            "v2",
            id="explicit-ref-overrides-the-tree-segment",
        ),
        pytest.param(
            "https://example.com/org/.github/rules@main",
            "https://example.com/org/.github",
            "rules",
            "main",
            id="dot-github-is-not-a-dot-git-marker",
        ),
        pytest.param(
            "file:///tmp/repo.git/stack@main",
            "file:///tmp/repo.git",
            "stack",
            "main",
            id="file-url-keeps-its-absolute-path",
        ),
    ],
)
def test_parse_git_ref(ref: str, repo_url: str, subdir: str, git_ref: str | None) -> None:
    parsed = parse_git_ref(ref)
    assert parsed.repo_url == repo_url
    assert parsed.subdir == subdir
    assert parsed.ref == git_ref


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        pytest.param("example.com/org/repo", "must be a URL", id="no-scheme"),
        pytest.param("https://example.com/org", "cannot tell where", id="no-repo-segment"),
        pytest.param("file:///tmp/deep/path", "cannot tell where", id="file-url-needs-dot-git"),
        pytest.param("https://example.com/org/repo@", "empty '@<ref>'", id="empty-ref"),
    ],
)
def test_parse_git_ref_rejects(ref: str, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse_git_ref(ref)


def test_git_source_matches_every_remote_url() -> None:
    src = GitManifestSource()
    assert src.matches("https://h/o/r.git/sub@main")
    assert src.matches("https://h/o/r")  # a browser URL, no .git
    assert src.matches("file:///tmp/r.git")
    assert not src.matches("../local/path")
    assert not src.matches("/abs/path")


def test_both_spellings_of_a_repo_share_one_clone() -> None:
    assert _cache_dir(f"{_BUNDLES}.git") == _cache_dir(_BUNDLES)


def test_local_ref_resolves_relative(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "boff.yaml").write_text("meta:\n  name: b\n  description: b\n")
    resolved = resolve_ref("base", base_root=tmp_path)
    assert resolved == base.resolve()


def test_local_ref_missing_boff_raises(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ManifestError, match="boff.yaml"):
        resolve_ref("empty", base_root=tmp_path)


def test_git_source_resolves_via_file_url(bare_repo: Path, tmp_path: Path) -> None:
    ref = f"file://{bare_repo}/{REMOTE_SUBDIR}@main"
    root = resolve_ref(ref, base_root=tmp_path)
    assert (root / "boff.yaml").is_file()
    assert root.name == REMOTE_SUBDIR
    # A second resolution exercises the cache-hit fetch path.
    assert resolve_ref(ref, base_root=tmp_path) == root


def test_git_source_bad_ref_raises(bare_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="git command failed"):
        resolve_ref(f"file://{bare_repo}/{REMOTE_SUBDIR}@no-such-ref", base_root=tmp_path)


def test_git_source_missing_subdir_raises(bare_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="boff.yaml"):
        resolve_ref(f"file://{bare_repo}/no-such-subdir@main", base_root=tmp_path)
