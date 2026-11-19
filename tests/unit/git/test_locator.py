"""RepositoryLocator: folder → verified worktree, ownership and symlink checks, recreation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from relaydeck.git.locator import LocateError, RepositoryLocator
from relaydeck.git.runner import GitRunner
from tests.support.git_identity import AUTHOR_CONFIG


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _repo(path: Path) -> Path:
    _git("init", "-q", "-b", "main", str(path))
    _git(
        "-C",
        str(path),
        *AUTHOR_CONFIG,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "c",
    )
    return path


def _locator(**kwargs: object) -> RepositoryLocator:
    return RepositoryLocator(GitRunner.create(), **kwargs)  # type: ignore[arg-type]


def test_subdirectory_resolves_to_the_worktree_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "pkg" / "sub").mkdir(parents=True)
    found = _locator().locate(str(repo / "pkg" / "sub"))
    assert found.root == os.path.realpath(repo)
    assert found.gitdir == found.common_dir == os.path.realpath(repo / ".git")


def test_nested_repository_wins(tmp_path: Path) -> None:
    outer = _repo(tmp_path / "outer")
    inner = _repo(outer / "inner")
    assert _locator().locate(str(inner)).root == os.path.realpath(inner)


def test_linked_worktree_has_its_own_gitdir_and_shared_common_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    _git("-C", str(repo), "worktree", "add", "-q", "-b", "side", str(linked))
    main, side = _locator().locate(str(repo)), _locator().locate(str(linked))
    assert side.root == os.path.realpath(linked)
    assert side.common_dir == main.common_dir
    assert side.gitdir != main.gitdir and side.identity != main.identity


def test_symlinked_folder_resolves_to_its_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert _locator().locate(str(link)).root == os.path.realpath(repo)


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda p: p, "folder_gone"),
        (lambda p: (p.mkdir(), p)[1], "no_repo"),
    ],
)
def test_missing_or_plain_folders(tmp_path: Path, make, reason: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LocateError, match=reason):
        _locator().locate(str(make(tmp_path / "x")))


def test_relative_or_nul_folder_is_refused() -> None:
    for folder in ("relative/path", "/tmp/a\0b"):
        with pytest.raises(LocateError):
            _locator().locate(folder)


def test_folder_owned_by_another_user_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    with pytest.raises(LocateError, match="unavailable"):
        _locator(uid=os.getuid() + 1).locate(str(repo))


def test_ancestor_swapped_for_a_symlink_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    real = os.lstat

    def lstat(path: str) -> os.stat_result:
        if path == os.path.realpath(tmp_path):
            return os.lstat(tmp_path / "swap")
        return real(path)

    (tmp_path / "swap").symlink_to(tmp_path / "repo")
    with pytest.raises(LocateError, match="unavailable"):
        _locator(lstat=lstat).locate(str(repo))


def test_recreated_repository_is_a_new_identity(tmp_path: Path) -> None:
    clock = [0.0]
    locator = _locator(clock=lambda: clock[0])
    repo = _repo(tmp_path / "repo")
    first = locator.locate(str(repo))
    shutil.rmtree(repo)
    _repo(repo)
    second = locator.locate(str(repo))  # inside the TTL, but the identity check refuses the hit
    assert second.identity != first.identity


def test_git_file_pointing_elsewhere_is_validated_as_owned(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    _git("-C", str(repo), "worktree", "add", "-q", "-b", "side", str(linked))
    assert (linked / ".git").is_file()
    gitdir = os.path.realpath(repo / ".git" / "worktrees" / "linked")
    real = os.lstat

    def lstat(path: str) -> os.stat_result:
        info = real(path)
        if path != gitdir:
            return info
        fields = list(info)
        fields[4] = os.getuid() + 1  # st_uid
        return os.stat_result(fields)

    with pytest.raises(LocateError, match="unavailable"):
        _locator(lstat=lstat).locate(str(linked))
    assert _locator().locate(str(linked)).gitdir == gitdir
