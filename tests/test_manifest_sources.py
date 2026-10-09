import shutil
import subprocess
from pathlib import Path

import pytest

from boff.errors import ManifestError
from boff.manifest import load_manifest
from boff.manifest_sources import fetch, resolve_ref
from boff.manifest_sources import git as git_source
from boff.manifest_sources.git import (
    GitManifestSource,
    _cache_dir,  # pyright: ignore[reportPrivateUsage]  (the cache key has no public surface)
    parse_git_ref,
)
from tests.conftest import (
    META,
    REMOTE_RULE_V1,
    REMOTE_RULE_V2,
    REMOTE_SUBDIR,
    head_of,
    push_commit,
)

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


def test_git_source_resolves_via_file_url(
    bare_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = f"file://{bare_repo}/{REMOTE_SUBDIR}@main"
    root = resolve_ref(ref, base_root=tmp_path)
    assert (root / "boff.yaml").is_file()
    assert root.name == REMOTE_SUBDIR
    # Forgetting this process's fetches makes the second resolution take the path a later
    # boff run does: fetch into the existing clone.
    monkeypatch.setattr(git_source, "_fetched", set[Path]())
    assert resolve_ref(ref, base_root=tmp_path) == root


def test_git_source_rejects_a_directory_without_a_manifest(bare_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no boff.yaml"):
        resolve_ref(f"file://{bare_repo}@main", base_root=tmp_path)


def test_git_source_bad_ref_raises(bare_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="git command failed"):
        resolve_ref(f"file://{bare_repo}/{REMOTE_SUBDIR}@no-such-ref", base_root=tmp_path)


def test_git_source_missing_subdir_raises(bare_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no directory 'no-such-subdir'"):
        resolve_ref(f"file://{bare_repo}/no-such-subdir@main", base_root=tmp_path)


def _rule_at(repo: Path, ref: str | None) -> str:
    """Return a reference to the `content_repo` rules directory, at ``ref`` if given."""
    return f"file://{repo}/rules" + (f"@{ref}" if ref else "")


def test_each_ref_of_one_repository_keeps_its_own_checkout(content_repo: Path) -> None:
    """A path from one ref survives resolving another ref of the same repository.

    Hook scripts, mise files, and local plugin folders are read after the whole stack loads,
    so a shared checkout would hand them the last-resolved ref's files instead of their own.
    """
    first, second = fetch(_rule_at(content_repo, "v1")), fetch(_rule_at(content_repo, "main"))
    assert first != second
    assert [(first / "r.md").read_text(), (second / "r.md").read_text()] == [
        REMOTE_RULE_V1,
        REMOTE_RULE_V2,
    ]


def test_a_reference_without_a_ref_follows_the_default_branch(content_repo: Path) -> None:
    assert (fetch(_rule_at(content_repo, None)) / "r.md").read_text() == REMOTE_RULE_V2


@pytest.mark.parametrize("ref", ["main", None], ids=["named-branch", "default-branch"])
def test_a_branch_follows_the_remote_tip_on_the_next_run(
    ref: str | None, content_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch(_rule_at(content_repo, ref))
    upstream = "# rule, pushed upstream\n"
    push_commit(content_repo, {"rules/r.md": upstream})
    monkeypatch.setattr(git_source, "_fetched", set[Path]())  # a later boff run
    assert (fetch(_rule_at(content_repo, ref)) / "r.md").read_text() == upstream


@pytest.mark.parametrize("leftover", ["deleted", "half-made"])
def test_a_missing_checkout_is_recreated(
    leftover: str, content_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _rule_at(content_repo, "v1")
    rules = fetch(ref)
    shutil.rmtree(rules.parent)
    if leftover == "half-made":  # an interrupted run left a directory with no worktree in it
        rules.mkdir(parents=True)
    monkeypatch.setattr(git_source, "_fetched", set[Path]())  # a later boff run
    assert fetch(ref) == rules
    assert (rules / "r.md").read_text() == REMOTE_RULE_V1


def test_a_clone_without_a_remote_head_still_finds_the_default_branch(
    content_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone made by an older boff may lack ``origin/HEAD``; boff asks the remote for it."""
    ref = _rule_at(content_repo, None)
    fetch(ref)
    clone = _cache_dir(f"file://{content_repo}")
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-head", "origin", "--delete"],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(git_source, "_fetched", set[Path]())  # a later boff run
    assert (fetch(ref) / "r.md").read_text() == REMOTE_RULE_V2


def test_an_edited_checkout_is_restored(
    content_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _rule_at(content_repo, "v1")
    rule = fetch(ref) / "r.md"
    rule.write_text("# edited in the cache\n")
    monkeypatch.setattr(git_source, "_fetched", set[Path]())  # a later boff run
    assert (fetch(ref) / "r.md").read_text() == REMOTE_RULE_V1


def test_manifests_from_two_commits_of_one_repository_keep_their_own_tool_files(
    bare_repo: Path, tmp_path: Path
) -> None:
    """The stacked-bundles case: a tool file is read at plan time, after both refs resolve."""
    first, second = "[tools]\nfirst = 'latest'\n", "[tools]\nsecond = 'latest'\n"
    mise = f"{REMOTE_SUBDIR}/mise/mise.toml"
    push_commit(
        bare_repo, {f"{REMOTE_SUBDIR}/boff.yaml": META + "mise:\n  - mise/mise.toml\n", mise: first}
    )
    old = head_of(bare_repo)
    push_commit(bare_repo, {mise: second})
    manifests = [
        load_manifest(resolve_ref(f"file://{bare_repo}/{REMOTE_SUBDIR}@{ref}", base_root=tmp_path))
        for ref in (old, "main")
    ]
    assert [m.tool_files["mise"][0].read_text() for m in manifests] == [first, second]
