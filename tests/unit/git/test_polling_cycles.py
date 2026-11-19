"""Large-tree fallback and concurrent mutable polls keep work bounded."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from relaydeck.git import service as service_module
from relaydeck.git.base_store import BaseStore
from relaydeck.git.polling import metadata_stamp
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesError, ChangesService
from tests.support.graph_repos import git, repository


def test_overlapping_accounts_share_one_stamp_without_a_timed_positive_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    entered, release = threading.Event(), threading.Event()
    original = metadata_stamp
    calls: list[int] = []

    def blocked(*args: Any, **kwargs: Any) -> str | None:
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    async def scenario() -> None:
        for account in ("first", "second", "third"):
            await service.snapshot(account, "host", key, "working")
        monkeypatch.setattr(service_module, "metadata_stamp", blocked)
        first = asyncio.create_task(service.snapshot("first", "host", key, "working"))
        assert await asyncio.to_thread(entered.wait, 3)
        others = [
            asyncio.create_task(service.snapshot(account, "host", key, "working"))
            for account in ("second", "third")
        ]
        await asyncio.sleep(0)
        release.set()
        assert all(not snap.rows for snap in await asyncio.gather(first, *others))
        assert len(calls) == 1
        (repo / "new.txt").write_text("new\n")
        current = await service.snapshot("first", "host", key, "working")
        assert {row.path for row in current.rows} == {b"new.txt"}
        assert len(calls) > 1

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        service.close()


def test_failed_walk_doubles_backoff_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    now = [100.0]
    service = ChangesService(
        GitRunner.create(), BaseStore(str(tmp_path / "state")), clock=lambda: now[0]
    )
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    known = service._cache.known[key]
    attempts: list[int] = []
    original = metadata_stamp
    recovering = False

    def proof(*args: Any, **kwargs: Any) -> str | None:
        attempts.append(1)
        return original(*args, **kwargs) if recovering else None

    monkeypatch.setattr(service_module, "metadata_stamp", proof)

    async def scenario() -> None:
        nonlocal recovering
        for expected_delay in (120, 240, 480):
            await service.snapshot("account", "host", key, "working")
            assert known.poll_retry_at - now[0] == expected_delay
            before = len(attempts)
            now[0] = known.poll_retry_at - 1
            await service.snapshot("account", "host", key, "working")
            assert len(attempts) == before
            now[0] += 2
        recovering = True
        await service.snapshot("account", "host", key, "working")
        assert known.poll_failures == 0 and known.poll_retry_at == 0

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_large_ignored_tree_uses_only_guarded_git_after_the_first_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    (repo / ".gitignore").write_text("deps/\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Ignore generated files")
    for index in range(120000):
        parent = repo / "deps" / str(index // 1000)
        if index % 1000 == 0:
            parent.mkdir(parents=True)
        (parent / str(index)).touch()
    runner = GitRunner.create()
    service = ChangesService(runner, BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    commands: list[str] = []
    run = runner.run

    def counted(root: str, args: tuple[str, ...], **kwargs: Any) -> Any:
        commands.append(args[0])
        return run(root, args, **kwargs)

    async def scenario() -> None:
        await service.snapshot("account", "host", key, "working")
        assert service._cache.known[key].poll_retry_at > 0

        def forbidden_walk(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("failed worktree proof was retried before its backoff")

        monkeypatch.setattr(service_module, "metadata_stamp", forbidden_walk)
        monkeypatch.setattr(runner, "run", counted)
        cpu = time.process_time()
        for _ in range(3):
            assert not (await service.snapshot("account", "host", key, "working")).rows
        assert (time.process_time() - cpu) / 3 < 0.05
        assert len(commands) <= 15 and commands.count("status") >= 6

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_successful_summary_check_advances_freshness_without_recounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    wall = [1000]
    service = ChangesService(
        GitRunner.create(), BaseStore(str(tmp_path / "state")), wall_ms=lambda: wall[0]
    )
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    service._summarize("account", "host", key)
    first = service._cache.known[key].summary
    assert first is not None and first.as_of_ms == 1000
    calls: list[int] = []
    original = metadata_stamp

    def counted(*args: Any, **kwargs: Any) -> str | None:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(service_module, "metadata_stamp", counted)
    wall[0] = 5000
    try:
        service._summarize("account", "host", key)
        checked = service._cache.known[key].summary
        assert checked is not None and checked.as_of_ms == 5000
        assert checked.revision == first.revision and checked.working is first.working
        assert len(calls) == 1
        git(repo, "config", "core.fsmonitor", "/not-allowed")
        wall[0] = 9000
        service._summarize("account", "host", key)
        failed = service._cache.known[key].summary
        assert (
            failed is not None and failed.state == "unsupported_config" and failed.as_of_ms == 5000
        )
    finally:
        service.close()


def test_benign_config_race_is_retryable_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path / "repo")
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    service._locate(str(repo))
    key = next(iter(service._cache.known))
    original = service._guard.check

    def changed(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        with (repo / ".git/config").open("a") as handle:
            handle.write("\n[help]\nautoCorrect = 0\n")

    monkeypatch.setattr(service._guard, "check", changed)

    async def scenario() -> None:
        with pytest.raises(ChangesError) as error:
            await service.snapshot("account", "host", key, "working")
        assert error.value.code == "busy" and error.value.retry_after_s == 1
        monkeypatch.setattr(service._guard, "check", original)
        assert not (await service.snapshot("account", "host", key, "working")).partial

    try:
        asyncio.run(scenario())
    finally:
        service.close()
