"""History guards and parser limits remain authoritative during frozen paging."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from relaydeck.git.base_store import BaseStore
from relaydeck.git.graph_reader import GraphReader
from relaydeck.git.locator import RepositoryLocator
from relaydeck.git.reader import ReadError
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitResult, GitRunner
from relaydeck.git.service import ChangesService
from tests.support.graph_repos import git, located, repository


@pytest.mark.parametrize("metadata", ["shallow", "info/grafts"])
def test_uncached_continuation_rejects_new_history_rewriting(
    tmp_path: Path,
    metadata: str,
) -> None:
    repo = repository(tmp_path / "repo", 102)
    git(repo, "branch", "base", git(repo, "rev-list", "--max-parents=0", "HEAD").decode())
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        await service.set_base("account", "host", key, "refs/heads/base")
        first = await service.graph("account", "host", key, "all")
        (repo / ".git" / metadata).write_bytes(git(repo, "rev-parse", "HEAD") + b"\n")
        with pytest.raises(ChangesError, match="unsupported_config"):
            await service.graph(
                "account", "host", key, "all", revision=first.page.snapshot, offset=100
            )

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_truncation_fails_even_for_direct_resolve_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repository(tmp_path / "repo")
    runner = GitRunner.create()
    reader = GraphReader(
        runner, RepositoryLocator(runner).locate(str(repo)), deadline=time.monotonic() + 5
    )
    reader.empty_tree()

    def truncated(*args: Any, **kwargs: Any) -> GitResult:
        return GitResult(b"a" * 40, True)

    monkeypatch.setattr(runner, "run", truncated)
    with pytest.raises(ReadError, match="truncated"):
        reader.resolve("refs/heads/main")


@pytest.mark.parametrize("upstream", [b"refs/heads/x*", b"refs/heads/a..b", b"refs/heads/a@{1}"])
def test_upstream_is_validated_before_becoming_an_argv_pattern(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    upstream: bytes,
) -> None:
    repo = repository(tmp_path / "repo")
    runner = GitRunner.create()
    reader = GraphReader(
        runner, RepositoryLocator(runner).locate(str(repo)), deadline=time.monotonic() + 5
    )
    oid = git(repo, "rev-parse", "HEAD")
    original = reader._query
    calls: list[tuple[str, ...]] = []

    def query(*args: str, cap: int = 2 * 1024 * 1024) -> bytes:
        calls.append(args)
        if "--format=%(refname)%00%(objectname)%00%(upstream)" in args:
            return b"refs/heads/main\0" + oid + b"\0" + upstream + b"\n"
        return original(*args, cap=cap)

    monkeypatch.setattr(reader, "_query", query)
    with pytest.raises(ReadError, match="malformed"):
        reader.capture("refs/heads/main", "default")
    assert not any(upstream.decode() in args for args in calls)


def test_unborn_head_has_no_graph_but_working_remains_available(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo", 0)
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))

    async def run() -> None:
        key = await located(service, repo)
        working = await service.snapshot("account", "host", key, "working")
        assert working.head is None
        await service.set_base("account", "host", key, "refs/heads/main")
        with pytest.raises(ChangesError, match="no_base"):
            await service.graph("account", "host", key, "all")

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_whole_request_deadline_includes_worker_queue_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relaydeck.git import service as service_module

    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path / "state")))
    release = threading.Event()
    monkeypatch.setattr(service_module, "GRAPH_REQUEST_S", 0.03)

    async def run() -> None:
        held = [
            asyncio.create_task(service._workers.call((f"held-{i}",), release.wait))
            for i in range(2)
        ]
        await asyncio.sleep(0.02)
        started = time.monotonic()
        try:
            with pytest.raises(ChangesError, match="busy"):
                await service.graph("account", "host", "unused", "all")
            assert time.monotonic() - started < 0.5
        finally:
            release.set()
            await asyncio.gather(*held)
            await asyncio.sleep(0.02)

    try:
        asyncio.run(run())
    finally:
        release.set()
        service.close()
