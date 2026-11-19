"""RepositoryReader against real synthetic repositories, one per state."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.reader import (
    MAX_BASES,
    ReadError,
    RepositoryReader,
    parse_numstat,
    parse_status,
)
from relaydeck.git.runner import GitRunner
from tests.support.git_identity import AUTHOR_CONFIG

_ID = AUTHOR_CONFIG


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *_ID, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make(root: Path, *, commit: bool = True) -> Path:
    repo = root / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    if commit:
        (repo / "a.txt").write_text("one\ntwo\n")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-qm", "base")
    return repo


def reader(repo: Path) -> RepositoryReader:
    runner = GitRunner.create()
    return RepositoryReader(runner, RepositoryLocator(runner).locate(str(repo)))


def by_path(repo: Path) -> dict[bytes, object]:
    return {e.path: e for e in reader(repo).status().entries}


def test_clean_repository(tmp_path: Path) -> None:
    status = reader(make(tmp_path)).status()
    assert status.branch == "main" and not status.detached and status.head and not status.entries


def test_staged_and_unstaged_on_one_file(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / "a.txt").write_text("one\nTWO\n")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("one\nTWO\nthree\n")
    entry = by_path(repo)[b"a.txt"]
    assert entry.staged and entry.unstaged  # type: ignore[attr-defined]


def test_staged_edit_reverted_in_worktree_is_dirty_with_no_net_diff(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / "a.txt").write_text("changed\n")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("one\ntwo\n")
    r = reader(repo)
    entry = {e.path: e for e in r.status().entries}[b"a.txt"]
    assert entry.staged and entry.unstaged
    assert r.numstat(r.status().head or "", None) == ()


def test_staged_add_then_worktree_delete(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / "n.txt").write_text("new\n")
    git(repo, "add", "n.txt")
    (repo / "n.txt").unlink()
    entry = by_path(repo)[b"n.txt"]
    assert entry.staged and entry.unstaged  # type: ignore[attr-defined]


def test_untracked_and_ignored(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "u.txt").write_text("u\n")
    (repo / "x.log").write_text("x\n")
    entries = by_path(repo)
    assert entries[b"u.txt"].kind == "?"  # type: ignore[attr-defined]
    assert b"x.log" not in entries


def test_untracked_patch_and_escape_refused(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / "u.txt").write_text("hello\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    (repo / "link.txt").symlink_to(tmp_path / "outside.txt")
    r = reader(repo)
    assert b"+hello" in r.untracked_patch(b"u.txt")
    with pytest.raises(ReadError):
        r.untracked_patch(b"link.txt")
    with pytest.raises(ValueError):
        r.untracked_patch(b"../outside.txt")


def test_conflict(tmp_path: Path) -> None:
    repo = make(tmp_path)
    git(repo, "checkout", "-qb", "side")
    (repo / "a.txt").write_text("side\n")
    git(repo, "commit", "-qam", "side")
    git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main\n")
    git(repo, "commit", "-qam", "main")
    subprocess.run(["git", "-C", str(repo), *_ID, "merge", "-q", "side"], capture_output=True)
    assert by_path(repo)[b"a.txt"].kind == "u"  # type: ignore[attr-defined]


def test_detached_head(tmp_path: Path) -> None:
    repo = make(tmp_path)
    git(repo, "checkout", "-q", "--detach")
    status = reader(repo).status()
    assert status.detached and status.branch is None and status.head


def test_unborn_head_compares_with_the_empty_tree(tmp_path: Path) -> None:
    repo = make(tmp_path, commit=False)
    (repo / "a.txt").write_text("x\ny\n")
    git(repo, "add", "a.txt")
    r = reader(repo)
    assert r.status().head is None
    (stat,) = r.numstat(r.empty_tree(), None)
    assert (stat.path, stat.additions, stat.deletions) == (b"a.txt", 2, 0)


def test_rename_and_binary_numstat(tmp_path: Path) -> None:
    repo = make(tmp_path)
    (repo / "bin.dat").write_bytes(b"\0\1\2")
    git(repo, "add", "bin.dat")
    git(repo, "commit", "-qm", "bin")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "a.txt", "b.txt")
    (repo / "bin.dat").write_bytes(b"\0\1\3")
    stats = {s.path: s for s in reader(repo).numstat(head, None)}
    assert stats[b"b.txt"].old_path == b"a.txt"
    assert stats[b"bin.dat"].additions is None


def test_branch_scope_merge_base_count_and_commits(tmp_path: Path) -> None:
    repo = make(tmp_path)
    git(repo, "checkout", "-qb", "feature")
    for n in range(3):
        (repo / "a.txt").write_text(f"v{n}\n")
        git(repo, "commit", "-qam", f"step {n}")
    r = reader(repo)
    base = r.resolve("refs/heads/main")
    head = r.status().head
    assert base and head
    (merge_base,) = r.merge_bases(base, head)
    assert merge_base == base and r.count(base, head) == 3
    page = r.commits(base, head, skip=1, limit=1)
    assert [c.subject for c in page] == ["step 1"] and page[0].parents


def test_remote_default_and_base_candidates(tmp_path: Path) -> None:
    repo = make(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", head)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    r = reader(repo)
    assert r.remote_default() == "refs/remotes/origin/main"
    names = [name for name, _ in r.base_candidates()]
    assert names == ["refs/heads/main", "refs/remotes/origin/main"]


def test_base_candidates_are_bounded(tmp_path: Path) -> None:
    repo = make(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    lines = "".join(f"create refs/heads/b{n:04d} {head}\n" for n in range(MAX_BASES + 20))
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "--stdin"], input=lines, text=True, check=True
    )
    assert len(reader(repo).base_candidates()) == MAX_BASES


def test_no_remote_default(tmp_path: Path) -> None:
    assert reader(make(tmp_path)).remote_default() is None


def test_the_only_remote_head_is_the_default(tmp_path: Path) -> None:
    repo = make(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/mirror/main", head)
    git(repo, "update-ref", "refs/remotes/mirror/release", head)
    git(repo, "symbolic-ref", "refs/remotes/mirror/HEAD", "refs/remotes/mirror/main")
    assert reader(repo).remote_default() == "refs/remotes/mirror/main"
    git(repo, "update-ref", "refs/remotes/fork/main", head)
    git(repo, "symbolic-ref", "refs/remotes/fork/HEAD", "refs/remotes/fork/main")
    assert reader(repo).remote_default() is None


def test_a_local_trunk_is_the_default_without_a_remote_one(tmp_path: Path) -> None:
    repo = make(tmp_path)
    git(repo, "checkout", "-qb", "feature")
    assert reader(repo).remote_default() == "refs/heads/main"
    git(repo, "branch", "-m", "main", "trunk")
    git(repo, "branch", "master", "trunk")
    assert reader(repo).remote_default() == "refs/heads/master"  # main, then master, then trunk
    git(repo, "checkout", "-q", "master")
    assert reader(repo).remote_default() is None  # a trunk is not compared with a sibling trunk


def test_head_ref(tmp_path: Path) -> None:
    repo = make(tmp_path)
    r = reader(repo)
    assert r.head_ref() == "refs/heads/main"
    git(repo, "checkout", "-q", "--detach")
    assert r.head_ref() is None


def test_qualified_refs_and_oids_only(tmp_path: Path) -> None:
    r = reader(make(tmp_path))
    for bad in ("main", "--output=/tmp/x", "refs/heads/../x"):
        with pytest.raises(ValueError):
            r.resolve(bad)
    with pytest.raises(ValueError):
        r.count("--all", "HEAD")


def test_patch_for_unusual_names(tmp_path: Path) -> None:
    repo = make(tmp_path)
    names = ["-dash.txt", ":(glob)*.txt", "sp ace.txt", "uni-é.txt", "new\nline.txt"]
    for name in names:
        (repo / name).write_text("x\n")
        git(repo, "add", "--", name)
    git(repo, "commit", "-qm", "names")
    head = git(repo, "rev-parse", "HEAD")
    r = reader(repo)
    for name in names:
        (repo / name).write_text("y\n")
        out = r.patch(head, None, (name.encode(),))
        assert b"+y" in out and out.count(b"diff --git") == 1


def test_manifest_paths_only(tmp_path: Path) -> None:
    r = reader(make(tmp_path))
    head = r.status().head or ""
    for bad in (b"", b"/etc/passwd", b"a/../../x", b"a\0b"):
        with pytest.raises(ValueError):
            r.patch(head, None, (bad,))


def test_parsers_reject_garbage() -> None:
    with pytest.raises(ReadError):
        parse_status(b"Z nonsense\0")
    assert parse_numstat(b"") == ()


def test_filter_added_after_the_guard_never_runs_through_the_reader(tmp_path: Path) -> None:
    repo = make(tmp_path)
    marker = tmp_path / "ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
    helper.chmod(0o755)
    r = reader(repo)  # located and ready before the repository changes
    git(repo, "config", "filter.x.clean", str(helper))
    git(repo, "config", "diff.x.textconv", str(helper))
    (repo / ".gitattributes").write_text("*.txt filter=x diff=x\n")
    (repo / "a.txt").write_text("one\nTWO\n")
    head = r.status().head or ""
    r.numstat(head, None)
    r.patch(head, None, (b"a.txt",))
    assert not marker.exists()


def test_untracked_read_never_follows_a_swapped_parent(tmp_path: Path) -> None:
    repo = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "secret.txt").write_text("fine\n")
    r = reader(repo)
    assert b"+fine" in r.untracked_patch(b"sub/secret.txt")
    (repo / "sub" / "secret.txt").unlink()
    (repo / "sub").rmdir()
    (repo / "sub").symlink_to(outside)
    with pytest.raises(ReadError):
        r.untracked_patch(b"sub/secret.txt")


def test_cancel_stops_a_hanging_git_promptly(tmp_path: Path) -> None:
    import threading
    import time as clock

    from relaydeck.git.locator import Worktree
    from relaydeck.git.runner import GitFailed

    fake = tmp_path / "git"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    cancel = threading.Event()
    worktree = Worktree(str(tmp_path), str(tmp_path), str(tmp_path), (0,))
    hanging = RepositoryReader(GitRunner(str(fake)), worktree, cancel)
    failures: list[BaseException] = []

    def read() -> None:
        try:
            hanging.status()
        except GitFailed as exc:
            failures.append(exc)

    worker = threading.Thread(target=read)
    worker.start()
    clock.sleep(0.3)
    begun = clock.monotonic()
    cancel.set()
    worker.join(2)
    assert not worker.is_alive() and clock.monotonic() - begun < 1
    assert failures and str(failures[0]) == "git_cancelled"
