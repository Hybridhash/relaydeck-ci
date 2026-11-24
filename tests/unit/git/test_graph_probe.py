"""Freshness work is shared, bounded and invalidated by admitted graph inputs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from relaydeck.contracts.git import GraphMode
from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_reader import GraphReader
from relaydeck.git.reader import RepositoryReader
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitResult, GitRunner
from relaydeck.git.service import ChangesService
from tests.support.git_cache import CacheControl
from tests.support.graph_repos import commit, git, repository


def prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, ChangesService, list[tuple[str, ...]]]:
    repo = repository(tmp_path / "repo")
    git(repo, "branch", "side")
    git(repo, "checkout", "-qb", "feature")
    (repo / "file.txt").write_text("content\n")
    git(repo, "add", ".")
    tip = commit(repo)
    git(repo, "branch", "twin")
    git(repo, "tag", "release", "main")
    git(repo, "update-ref", "refs/remotes/origin/feature", tip)
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(repo, "config", "branch.feature.remote", "origin")
    git(repo, "config", "branch.feature.merge", "refs/heads/feature")
    runner = GitRunner.create()
    original = runner.run
    calls: list[tuple[str, ...]] = []

    def run(root: str | None, args: tuple[str, ...], **kwargs: Any) -> GitResult:
        calls.append(args)
        return original(root, args, **kwargs)

    monkeypatch.setattr(runner, "run", run)
    service = ChangesService(runner, BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))
    return repo, service, calls


def test_unchanged_checks_use_no_git_and_fallback_uses_two_fixed_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        monkeypatch.setattr(
            GraphReader, "capture", lambda *a: pytest.fail("unchanged full capture")
        )
        calls.clear()
        assert await service.graph("account", "host", key, "all") is first
        assert calls == []
        (repo / ".git/reftable").mkdir()
        assert await service.graph("account", "host", key, "all") is first
        assert [c[0] for c in calls] == ["config", "for-each-ref", "rev-parse"]
        assert calls[1][-3:] == ("refs/heads/", "refs/remotes/", "refs/tags/")

    try:
        asyncio.run(run())
    finally:
        service.close()


@pytest.mark.parametrize(
    "change", ["local", "upstream", "tag", "base", "detach", "symbolic", "saved_base"]
)
def test_admitted_change_runs_full_capture_and_changes_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    original = GraphReader.capture
    captures = 0

    def capture(*args: Any, **kwargs: Any) -> Any:
        nonlocal captures
        captures += 1
        return original(*args, **kwargs)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        monkeypatch.setattr(GraphReader, "capture", capture)
        if change == "local":
            git(repo, "branch", "-f", "side", "feature")
        elif change == "upstream":
            git(
                repo,
                "update-ref",
                "refs/remotes/origin/feature",
                git(repo, "rev-parse", "main").decode(),
            )
        elif change == "tag":
            git(repo, "tag", "-f", "release", "feature")
        elif change == "base":
            git(repo, "branch", "-f", "main", "feature")
        elif change == "detach":
            git(repo, "checkout", "--detach")
        elif change == "symbolic":
            git(repo, "checkout", "twin")
        else:
            await service.set_base("account", "host", key, "refs/heads/side")
        calls.clear()
        fresh = await service.graph("account", "host", key, "all")
        assert captures >= 2 and fresh.page.snapshot != first.page.snapshot
        assert any(call[0] == "config" for call in calls)

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_unadmitted_movement_and_packing_update_only_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        monkeypatch.setattr(GraphReader, "capture", lambda *a: pytest.fail("irrelevant capture"))
        mutations: tuple[Callable[[], bytes], ...] = (
            lambda: git(repo, "update-ref", "refs/remotes/origin/untracked", first.page.head),
            lambda: git(repo, "pack-refs", "--all", "--prune"),
        )
        for mutate in mutations:
            mutate()
            calls.clear()
            assert await service.graph("account", "host", key, "all") is first
            assert len(calls) == 2
            calls.clear()
            assert await service.graph("account", "host", key, "all") is first
            assert calls == []
        CacheControl(service).assert_accounted()

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_three_concurrent_readers_share_probe_and_changed_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    original = GraphReader.capture
    captures = 0

    def capture(*args: Any, **kwargs: Any) -> Any:
        nonlocal captures
        captures += 1
        return original(*args, **kwargs)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        modes: tuple[GraphMode, ...] = ("all", "branch", "all")
        for mode in set(modes):
            await service.graph("account", "host", key, mode)
        monkeypatch.setattr(GraphReader, "capture", capture)

        async def check() -> Any:
            return await asyncio.gather(
                *(service.graph("account", "host", key, mode) for mode in modes)
            )

        calls.clear()
        await check()
        assert calls == [] and captures == 0
        (repo / ".git/reftable").mkdir()
        calls.clear()
        await check()
        assert len(calls) == 3 and captures == 0
        git(repo, "tag", "-f", "release", "feature")
        calls.clear()
        result = await check()
        assert captures == 2 and len(calls) <= 36
        assert result[0].page.snapshot == result[1].page.snapshot
        CacheControl(service).assert_accounted()

    try:
        asyncio.run(run())
    finally:
        service.close()


@pytest.mark.parametrize("metadata", ["shallow", "info/grafts", "config"])
def test_cached_probe_keeps_configuration_and_history_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        await service.graph("account", "host", key, "all")
        path = repo / ".git" / metadata
        if metadata == "config":
            with path.open("a") as out:
                out.write('\n[filter "unsafe"]\n smudge = forbidden\n')
        else:
            path.write_text("")
        with pytest.raises(ChangesError, match="unsupported_config"):
            await service.graph("account", "host", key, "all")

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_readers_share_deadline_aware_empty_tree_and_remote_default() -> None:
    assert GraphReader.empty_tree is RepositoryReader.empty_tree
    assert GraphReader.remote_default is RepositoryReader.remote_default


def test_idle_retirement_releases_graph_commit_and_diff_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    cache = CacheControl(service)
    now = [time.monotonic()]
    cache.use_clock(monkeypatch, lambda: now[0])

    async def run() -> None:
        key = cache.first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        snap = await service.commit_snapshot(
            "account", "host", key, first.page.snapshot, "all", first.page.head
        )
        await service.file_diff(snap, 0, "file", 0)
        assert cache.bytes > 0 and cache.graph_count == 1 and "commit" in cache.scopes
        now[0] += 59
        await service.graph("account", "host", key, "all")
        await service.file_diff(snap, 0, "file", 0)
        now[0] += 2
        cache.sweep()
        assert cache.graph_count == 1 and "commit" in cache.scopes
        now[0] += 61
        cache.sweep()
        assert cache.graph_count == 0 and "commit" not in cache.scopes and cache.bytes == 0
        cache.assert_accounted()
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph(
                "account", "host", key, "all", revision=first.page.snapshot, offset=100
            )

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_idle_sweep_runs_without_another_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relaydeck.git import cache as cache_module

    repo, service, calls = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cache_module, "GRAPH_IDLE_S", 0.05)
    monkeypatch.setattr(cache_module, "COMMIT_IDLE_S", 0.05)
    cache = CacheControl(service)
    now = [time.monotonic()]
    cache.use_clock(monkeypatch, lambda: now[0])
    sweeps = cache.watch_sweeps(monkeypatch)
    waiting = asyncio.Event()
    expired = asyncio.Event()
    original_wait = asyncio.wait_for
    owners: set[asyncio.Task[Any]] = set()

    async def wait(pending: Any, timeout: float | None = None) -> Any:
        owner = asyncio.current_task()
        if owner is not service._maintenance:
            return await original_wait(pending, timeout)
        assert owner is not None
        owners.add(owner)
        try:
            assert timeout == pytest.approx(0.05)
            waiting.set()
            await expired.wait()
            expired.clear()
            raise TimeoutError
        finally:
            pending.close()

    monkeypatch.setattr(asyncio, "wait_for", wait)

    async def run() -> None:
        try:
            key = cache.first_worktree
            await service.set_base("account", "host", key, "refs/heads/main")
            first = await service.graph("account", "host", key, "all")
            await service.commit_snapshot(
                "account", "host", key, first.page.snapshot, "all", first.page.head
            )
            assert cache.bytes > 0
            assert not cache.maintenance_done
            await original_wait(waiting.wait(), timeout=5)
            now[0] += 0.15
            expired.set()
            maintenance = service._maintenance
            assert maintenance is not None
            await original_wait(maintenance, timeout=5)
            assert cache.graph_count == 0 and "commit" not in cache.scopes and cache.bytes == 0
            cache.assert_accounted()
            assert cache.maintenance_done
            count = sweeps()
            now[0] += 0.05
            await asyncio.sleep(0)
            assert sweeps() == count
            waiting.clear()
            await service.graph("account", "host", key, "all")
            await asyncio.sleep(0)
            assert not cache.maintenance_done
            await original_wait(waiting.wait(), timeout=5)
        finally:
            if service._maintenance is not None:
                owners.add(service._maintenance)
            for owner in owners:
                owner.cancel()
            await asyncio.gather(*owners, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        service.close()


@pytest.mark.parametrize(
    "kind", ["loose_symlink", "directory_symlink", "packed_oversize", "reftable"]
)
def test_uncertain_ref_storage_falls_back_without_following_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import os

    from relaydeck.git.graph_probe import ref_stamp
    from relaydeck.git.graph_reader import MAX_REF_BYTES
    from relaydeck.git.locator import RepositoryLocator

    repo = repository(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.write_bytes(git(repo, "rev-parse", "HEAD") + b"\n")
    outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
    if kind == "loose_symlink":
        (repo / ".git/refs/heads/main").unlink()
        (repo / ".git/refs/heads/main").symlink_to(outside)
    elif kind == "directory_symlink":
        (repo / ".git/refs/tags").rmdir()
        (repo / ".git/refs/tags").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "packed_oversize":
        (repo / ".git/packed-refs").write_bytes(b"#" * (MAX_REF_BYTES + 1))
    else:
        (repo / ".git/reftable").mkdir()
    runner = GitRunner.create()
    worktree = RepositoryLocator(runner).locate(str(repo))
    original = os.read

    def read(fd: int, size: int) -> bytes:
        info = os.fstat(fd)
        assert (info.st_dev, info.st_ino) != outside_identity
        return original(fd, size)

    monkeypatch.setattr(os, "read", read)
    assert ref_stamp(worktree, time.monotonic() + 5) is None


def test_linked_worktree_probe_observes_common_refs_and_its_own_head(tmp_path: Path) -> None:
    from relaydeck.git.graph_probe import ref_stamp
    from relaydeck.git.locator import RepositoryLocator

    repo = repository(tmp_path / "repo")
    root = git(repo, "rev-parse", "HEAD").decode()
    tip = commit(repo)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--detach", str(linked), tip)
    worktree = RepositoryLocator(GitRunner.create()).locate(str(linked))
    first = ref_stamp(worktree, time.monotonic() + 5)
    git(repo, "tag", "release", root)
    second = ref_stamp(worktree, time.monotonic() + 5)
    assert first is not None and second != first
    git(linked, "checkout", "--detach", root)
    third = ref_stamp(worktree, time.monotonic() + 5)
    assert third != second


def test_reftable_repository_uses_guarded_git_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from tests.support.graph_repos import fast_history

    repo = tmp_path / "repo"
    repo.mkdir()
    result = subprocess.run(
        ["git", "init", "-q", "-b", "main", "--ref-format=reftable", str(repo)], capture_output=True
    )
    if result.returncode:
        pytest.skip("Git does not support reftable storage")
    fast_history(repo, 2)
    runner = GitRunner.create()
    original = runner.run
    calls: list[tuple[str, ...]] = []

    def run_git(root: str | None, args: tuple[str, ...], **kwargs: Any) -> GitResult:
        calls.append(args)
        return original(root, args, **kwargs)

    monkeypatch.setattr(runner, "run", run_git)
    service = ChangesService(runner, BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        monkeypatch.setattr(GraphReader, "capture", lambda *a: pytest.fail("unchanged capture"))
        calls.clear()
        assert await service.graph("account", "host", key, "all") is first
        assert [call[0] for call in calls] == ["config", "for-each-ref", "rev-parse"]

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_shared_stamp_is_read_once_for_overlapping_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from relaydeck.git import graph as graph_module

    repo, service, calls = prepare(tmp_path, monkeypatch)
    from relaydeck.git.graph_probe import ref_stamp

    original = ref_stamp
    started, release = threading.Event(), threading.Event()
    fingerprints = 0

    def stamp(*args: Any, **kwargs: Any) -> str | None:
        nonlocal fingerprints
        fingerprints += 1
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        await service.graph("account", "host", key, "all")
        await service.graph("account", "host", key, "branch")
        monkeypatch.setattr(graph_module, "ref_stamp", stamp)
        calls.clear()
        first = asyncio.create_task(service.graph("account", "host", key, "all"))
        assert await asyncio.to_thread(started.wait, 2)
        modes: tuple[GraphMode, ...] = ("branch", "all")
        rest = [asyncio.create_task(service.graph("account", "host", key, mode)) for mode in modes]
        await asyncio.sleep(0.01)
        release.set()
        await asyncio.gather(first, *rest)
        assert fingerprints == 1 and calls == []

    try:
        asyncio.run(run())
    finally:
        release.set()
        service.close()


def test_shared_default_capture_is_account_scoped_and_saved_base_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "main").decode())
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    original = GraphReader.capture
    captures = 0

    def capture(*args: Any, **kwargs: Any) -> Any:
        nonlocal captures
        captures += 1
        return original(*args, **kwargs)

    async def run() -> None:
        key = CacheControl(service).first_worktree
        first = await service.graph("first", "host", key, "all")
        monkeypatch.setattr(GraphReader, "capture", capture)
        calls.clear()
        second = await service.graph("second", "host", key, "all")
        assert second.page.snapshot == first.page.snapshot and calls == [] and captures == 0
        (repo / ".git/reftable").mkdir()
        calls.clear()
        repeated = await service.graph("first", "host", key, "all")
        assert repeated.page.snapshot == first.page.snapshot and len(calls) == 3 and captures == 0
        (repo / ".git/reftable").rmdir()
        with pytest.raises(ChangesError, match="stale_snapshot"):
            await service.graph("unissued", "host", key, "all", revision=first.page.snapshot)
        left = await service.commit_snapshot(
            "first", "host", key, first.page.snapshot, "all", first.page.head
        )
        right = await service.commit_snapshot(
            "second", "host", key, second.page.snapshot, "all", second.page.head
        )
        assert left.account != right.account and left.revision != right.revision
        with pytest.raises(ChangesError, match="stale_snapshot"):
            service.cached("second", "host", key, "commit", left.revision)
        await service.set_base("saved", "host", key, "refs/heads/feature")
        calls.clear()
        saved = await service.graph("saved", "host", key, "all")
        assert captures == 2 and any(call[0] == "config" for call in calls)
        assert saved.page.snapshot != first.page.snapshot and saved.page.ahead == 0
        assert saved.page.base_oid == first.page.head
        CacheControl(service).assert_accounted()

    try:
        asyncio.run(run())
    finally:
        service.close()


@pytest.mark.parametrize("packed", [False, True])
def test_ref_replaced_mid_fingerprint_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packed: bool
) -> None:
    import os

    from relaydeck.git.graph_probe import ref_stamp
    from relaydeck.git.locator import RepositoryLocator

    repo = repository(tmp_path / "repo")
    if packed:
        git(repo, "pack-refs", "--all", "--prune")
    target = repo / ".git" / ("packed-refs" if packed else "refs/heads/main")
    worktree = RepositoryLocator(GitRunner.create()).locate(str(repo))
    original = os.read
    identity = target.stat().st_dev, target.stat().st_ino
    replaced = False

    def read(fd: int, size: int) -> bytes:
        nonlocal replaced
        data = original(fd, size)
        info = os.fstat(fd)
        if not replaced and (info.st_dev, info.st_ino) == identity:
            replacement = target.with_name("replacement")
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
            replaced = True
        return data

    monkeypatch.setattr(os, "read", read)
    assert ref_stamp(worktree, time.monotonic() + 5) is None and replaced


def test_cold_saved_base_capture_reuses_its_own_probe_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_probe_graph", lambda *a: pytest.fail("extra cold query"))
    # GraphService retains the callback it was constructed with.
    monkeypatch.setattr(service._graphs, "_probe", lambda *a: pytest.fail("extra cold query"))

    async def run() -> None:
        key = CacheControl(service).first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        calls.clear()
        await service.graph("account", "host", key, "all")
        assert sum(call[0] == "for-each-ref" for call in calls) == 6

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_commit_open_restarts_maintenance_on_its_request_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, service, calls = prepare(tmp_path, monkeypatch)
    cache = CacheControl(service)
    key = cache.first_worktree

    async def graph() -> Any:
        await service.set_base("account", "host", key, "refs/heads/main")
        return await service.graph("account", "host", key, "all")

    async def open_commit(page: Any) -> None:
        await service.commit_snapshot("account", "host", key, page.snapshot, "all", page.head)
        await asyncio.sleep(0)
        assert not cache.maintenance_done

    try:
        first = asyncio.run(graph())
        assert cache.maintenance_done
        asyncio.run(open_commit(first.page))
    finally:
        service.close()


def test_remembered_commit_wakes_an_existing_later_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relaydeck.git import cache as cache_module

    repo, service, calls = prepare(tmp_path, monkeypatch)
    cache = CacheControl(service)
    monkeypatch.setattr(cache_module, "COMMIT_IDLE_S", 0.05)

    async def run() -> None:
        key = cache.first_worktree
        await service.set_base("account", "host", key, "refs/heads/main")
        first = await service.graph("account", "host", key, "all")
        await asyncio.sleep(0.01)
        await service.commit_snapshot(
            "account", "host", key, first.page.snapshot, "all", first.page.head
        )
        assert "commit" in cache.scopes
        await asyncio.sleep(0.15)
        assert "commit" not in cache.scopes and cache.graph_count == 1
        assert not cache.maintenance_done
        cache.assert_accounted()

    try:
        asyncio.run(run())
    finally:
        service.close()
