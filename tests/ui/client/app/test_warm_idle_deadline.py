"""Root owns one cancellable expiry waiter; quiet warm views send no patches."""

from __future__ import annotations

import asyncio

import pytest

from relaydeck.client.app.sessions.retained import RetainedSessions
from tests.support.host_harness import run
from tests.support.patch_meter import PatchMeter
from tests.support.ui.retained_root import _open, _render


def test_root_hidden_idle_view_has_no_periodic_work_and_logout_stops_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans: list[int] = []
    tasks: list[asyncio.Task[None]] = []
    original_scan, original_wait = RetainedSessions.evict_idle, RetainedSessions.run_expiry

    async def scan(pool: RetainedSessions) -> None:
        scans.append(1)
        await original_scan(pool)

    async def waiter(pool: RetainedSessions) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.append(task)
        await original_wait(pool)

    monkeypatch.setattr(RetainedSessions, "evict_idle", scan)
    monkeypatch.setattr(RetainedSessions, "run_expiry", waiter)

    async def scenario() -> None:
        app, _controller, sessions, pool, _now = await _render()
        try:
            await _open(app, "home-mac:term-1")
            await app.set_props_and_fire("console-rail-terminals", {}, "click")
            await app.settle(15)
            assert len(tasks) == 1 and pool.foreground is None
            assert pool.entries() and pool.entries()[0].session.lease
            meter = PatchMeter(app)
            before = len(scans)
            await asyncio.sleep(0.25)
            assert len(scans) == before and meter.bytes == 0
        finally:
            await app.set_props_and_fire("test-unmount", {}, "click")
        assert tasks[0].done() and not pool.entries()
        assert all(s.disposed and not s.renewal_running for s in sessions.factory.created)
        assert not sessions.ops.closes

    run(scenario())
