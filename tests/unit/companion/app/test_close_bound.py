"""Closing the Changes service only signals; the wait for workers is bounded and off the loop."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.config import load_config
from relaydeck.git.base_store import BaseStore
from relaydeck.git.locator import Worktree
from relaydeck.git.results import ChangesError
from relaydeck.git.runner import GitRunner
from relaydeck.git.service import ChangesService
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.companion.asgi_companion import config_file

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")

WT = Worktree("/w/a", "/w/a/.git", "/w/a/.git", (1, 2, 1, 3, 1, 3))


class Held:
    """A locate that blocks on an event nothing sets until the test releases it."""

    def __init__(self, service: ChangesService) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.ran: list[str] = []
        self.service = service

    def locate(self, folder: str, _stop: object = None) -> Worktree:
        self.ran.append(folder)
        self.started.set()
        self.release.wait()
        return WT


def make(tmp_path: Path, workers: int = 2) -> tuple[ChangesService, Held]:
    service = ChangesService(GitRunner.create(), BaseStore(str(tmp_path)), workers=workers)
    held = Held(service)
    service._locator.locate = held.locate  # type: ignore[assignment]
    return service, held


async def started(held: Held) -> None:
    while not held.started.is_set():
        await asyncio.sleep(0.005)


async def heartbeat(ticks: list[float]) -> None:
    while True:
        ticks.append(time.monotonic())
        await asyncio.sleep(0.01)


def test_a_held_worker_neither_blocks_close_nor_the_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, held = make(tmp_path)
        ticks: list[float] = []
        beat = asyncio.ensure_future(heartbeat(ticks))
        try:
            await service.summaries("acct", "host", {"t": "/w/a"})
            await started(held)
            began = time.monotonic()
            service.close()
            assert time.monotonic() - began < 0.1  # signalling never joins
            assert await service.wait_closed(0.2) is False  # the worker is still held
            assert time.monotonic() - began < 1.0
            gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
            assert len(ticks) > 5 and max(gaps) < 0.15  # the loop kept running throughout
        finally:
            held.release.set()
            beat.cancel()
        assert await service.wait_closed(2.0) is True

    asyncio.run(scenario())


def test_close_is_idempotent_and_rejects_new_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, held = make(tmp_path)
        held.release.set()
        service.close()
        service.close()
        assert await service.wait_closed(2.0) is True
        assert await service.wait_closed(2.0) is True
        service.close()
        with pytest.raises(ChangesError) as caught:
            await service.summaries("acct", "host", {"t": "/w/a"})
        assert caught.value.code == "unavailable"
        with pytest.raises(ChangesError):
            await service._workers.call(("x",), lambda: None)
        service._workers.schedule(("y",), held.locate, "/w/b")
        assert held.ran == []

    asyncio.run(scenario())


def test_queued_work_is_dropped_and_never_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, held = make(tmp_path, workers=1)
        await service.summaries("acct", "host", {"a": "/w/a", "b": "/w/b"})
        await started(held)
        service.close()
        held.release.set()
        assert await service.wait_closed(2.0) is True
        assert held.ran == ["/w/a"]

    asyncio.run(scenario())


def test_work_finishing_after_close_retains_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, held = make(tmp_path)
        await service.summaries("acct", "host", {"t": "/w/a"})
        await started(held)
        service.close()
        assert await service.wait_closed(0.1) is False
        held.release.set()
        assert await service.wait_closed(2.0) is True
        cache = service._cache
        assert not cache.known and not cache.folders and not cache.snapshots
        stand_in: Any = SimpleNamespace(refs=None)
        service._graphs._remember_probe("k", stand_in, stand_in)
        assert not service._graphs._probes
        assert not service._config_stamps

    asyncio.run(scenario())


def test_maintenance_ends_with_close_and_the_join_thread_is_a_daemon(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, held = make(tmp_path)
        held.release.set()
        service._start_maintenance()
        task = service._maintenance
        assert task is not None
        service.close()
        assert await service.wait_closed(2.0) is True
        assert task.done()
        joins = [t for t in threading.enumerate() if t.name == "changes-join"]
        assert all(t.daemon for t in joins)

    asyncio.run(scenario())


def test_runtime_close_goes_on_past_a_held_worker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> list[float]:
        service, held = make(tmp_path)
        config = load_config(config_file(tmp_path, tailscale_auto=False))
        runtime = CompanionRuntime(config)
        noop: Any = SimpleNamespace(close=lambda: None)
        runtime.gateway = SimpleNamespace(
            usage=None,
            git=SimpleNamespace(service=service),
            backend=SimpleNamespace(journal=noop),
            push=noop,
        )
        ticks: list[float] = []
        beat = asyncio.ensure_future(heartbeat(ticks))
        try:
            await service.summaries("acct", "host", {"t": "/w/a"})
            await started(held)
            began = time.monotonic()
            await runtime.close()
            assert 1.5 < time.monotonic() - began < 4.0  # the 2 s bound, not the held worker
        finally:
            held.release.set()
            beat.cancel()
        return [b - a for a, b in zip(ticks, ticks[1:], strict=False)]

    caplog.set_level(logging.WARNING)
    gaps = asyncio.run(scenario())
    assert max(gaps) < 0.25
    assert "companion_shutdown_pending resource=changes_workers" in caplog.text
    assert "/w/a" not in caplog.text
