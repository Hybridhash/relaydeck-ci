"""Unchanged working directories need no Git process or content hashing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from relaydeck.git.base_store import BaseStore
from relaydeck.git.polling import metadata_stamp
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesError, ChangesService
from tests.support.graph_repos import git, located, repository


def test_large_dirty_worktree_polls_reuse_capture_and_detect_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    for index in range(5000):
        (repo / f"file-{index}").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Files")
    git(repo, "branch", "base")
    for index in range(5000):
        (repo / f"file-{index}").write_text("changed\n")
    runner = GitRunner.create()
    service = ChangesService(runner, BaseStore(str(tmp_path / "state")))
    calls: list[str] = []
    run = runner.run

    def counted(root: str, args: tuple[str, ...], **kwargs: Any) -> Any:
        calls.append(args[0])
        return run(root, args, **kwargs)

    async def scenario() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        first = await service.snapshot("account", "host", key, "working")
        await service.snapshot("account", "host", key, "branch")
        monkeypatch.setattr(runner, "run", counted)
        for _ in range(6):
            captures = await asyncio.gather(
                *(service.snapshot("account", "host", key, "working") for _ in range(3))
            )
            assert all(snap is first for snap in captures)
            await service.snapshot("account", "host", key, "branch")
        assert calls == []
        (repo / "file-0").write_text("another\n")
        second = await service.snapshot("account", "host", key, "working")
        assert second.revision != first.revision
        calls.clear()
        await service.snapshot("account", "host", key, "branch")
        assert "diff" not in calls and "rev-list" not in calls
        (repo / "new").write_text("untracked\n")
        third = await service.snapshot("account", "host", key, "working")
        assert third.revision != second.revision
        git(repo, "config", "core.fsmonitor", "/not-allowed")
        with pytest.raises(ChangesError, match="unsupported_config"):
            await service.snapshot("account", "host", key, "working")

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_metadata_probe_never_follows_worktree_links(tmp_path: Path) -> None:
    from relaydeck.git.locator import RepositoryLocator

    repo = repository(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value").write_text("private")
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    worktree = RepositoryLocator(GitRunner.create()).locate(str(repo))
    first = metadata_stamp(worktree, default_ignore_file=GitRunner.create().default_ignore_file)
    (outside / "value").write_text("changed private")
    assert (
        first is not None
        and metadata_stamp(worktree, default_ignore_file=GitRunner.create().default_ignore_file)
        == first
    )
    (repo / "linked").unlink()
    assert (
        metadata_stamp(worktree, default_ignore_file=GitRunner.create().default_ignore_file)
        != first
    )


def test_active_graph_only_keeps_its_account_commit_alive(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    now = [100.0]
    service = ChangesService(
        GitRunner.create(), BaseStore(str(tmp_path / "state")), clock=lambda: now[0]
    )

    async def scenario() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/main")
        await service.set_base("other", "host", key, "refs/heads/main")
        one = await service.graph("account", "host", key, "all")
        two = await service.graph("other", "host", key, "all")
        oid = one.page.commits[0].oid
        kept = await service.commit_snapshot("account", "host", key, one.page.snapshot, "all", oid)
        gone = await service.commit_snapshot("other", "host", key, two.page.snapshot, "all", oid)
        for _ in range(9):
            now[0] += 10
            await service.graph("account", "host", key, "all", revision=one.page.snapshot)
            service._cache.retire_idle()
        assert service.cached("account", "host", key, "commit", kept.revision) is kept
        with pytest.raises(ChangesError, match="stale_snapshot"):
            service.cached("other", "host", key, "commit", gone.revision)

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_due_now_retirement_yields_to_other_loop_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    order: list[str] = []

    def sweep() -> None:
        order.append("sweep")
        if order.count("sweep") == 3:
            raise RuntimeError("end_probe")

    monkeypatch.setattr(service._cache, "next_expiry", lambda: 0.0)
    monkeypatch.setattr(service._cache, "retire_idle", sweep)

    async def scenario() -> None:
        task = asyncio.create_task(service._retire_idle(asyncio.Event()))
        asyncio.get_running_loop().call_soon(order.append, "other_work")
        with pytest.raises(RuntimeError, match="end_probe"):
            await task
        assert order[:2] == ["sweep", "other_work"]

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_linked_config_never_authorizes_a_guard_cache_hit(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    target = tmp_path / "config"
    config = repo / ".git/config"
    target.write_bytes(config.read_bytes())
    config.unlink()
    config.symlink_to(target)
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def scenario() -> None:
        key = await located(service, repo)
        first = await service.snapshot("account", "host", key, "working")
        assert not first.partial
        target.write_text(target.read_text() + "\n[core]\nfsmonitor = /not-allowed\n")
        with pytest.raises(ChangesError, match="unsupported_config"):
            await service.snapshot("account", "host", key, "working")

    try:
        asyncio.run(scenario())
    finally:
        service.close()
