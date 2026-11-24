"""ChangeProbe: the revision moves with content, not only with status."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from relaydeck.git import probe as probe_module
from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.probe import ChangeProbe
from relaydeck.git.reader import RepositoryReader
from relaydeck.git.runner import GitRunner
from tests.support.git_identity import AUTHOR_CONFIG

_ID = AUTHOR_CONFIG


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *_ID, *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("one\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-qm", "c")
    return path


def revision(repo: Path, base: str | None = None) -> probe_module.Probe:
    runner = GitRunner.create()
    worktree = RepositoryLocator(runner).locate(str(repo))
    return ChangeProbe(worktree).probe(RepositoryReader(runner, worktree), base)


def test_unchanged_worktree_keeps_its_revision(repo: Path) -> None:
    first = revision(repo)
    assert first.stable and not first.partial
    assert revision(repo).revision == first.revision


def test_same_status_same_size_new_content_changes_the_revision(repo: Path) -> None:
    (repo / "a.txt").write_text("two\n")
    first = revision(repo)
    (repo / "a.txt").write_text("six\n")  # still modified, same size
    os.utime(repo / "a.txt", ns=(1, 1))
    assert revision(repo).revision != first.revision


def test_untracked_content_replacement_changes_the_revision(repo: Path) -> None:
    (repo / "u.txt").write_text("aaaa\n")
    first = revision(repo)
    (repo / "u.txt").write_text("bbbb\n")
    assert revision(repo).revision != first.revision


def test_staging_changes_the_revision(repo: Path) -> None:
    (repo / "a.txt").write_text("two\n")
    first = revision(repo)
    git(repo, "add", "a.txt")
    assert revision(repo).revision != first.revision


def test_base_choice_is_part_of_the_revision(repo: Path) -> None:
    assert revision(repo, "a" * 40).revision != revision(repo, "b" * 40).revision


def test_large_file_is_metadata_only_and_partial(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe_module, "MAX_HASHED_FILE", 4)
    (repo / "a.txt").write_text("much longer\n")
    assert revision(repo).partial


def test_symlink_is_stamped_not_followed(repo: Path, tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("one\n")
    (repo / "link").symlink_to(secret)
    first = revision(repo)
    secret.write_text("two\n")  # target content must not influence the revision
    assert revision(repo).revision == first.revision


def test_edit_during_the_probe_is_reported_unstable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    original = ChangeProbe._marker

    def racing(self: ChangeProbe) -> tuple[object, ...]:
        calls["n"] += 1
        return (calls["n"],)

    monkeypatch.setattr(ChangeProbe, "_marker", racing)
    result = revision(repo)
    assert not result.stable and calls["n"] == 4  # two attempts, no unbounded retry
    monkeypatch.setattr(ChangeProbe, "_marker", original)
