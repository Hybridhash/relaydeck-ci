"""Warm terminals wake at lifecycle changes and expiry, with no periodic sweep."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.client.app.sessions.retained import RetainedSession, RetainedSessions
from relaydeck.client.features.terminal.retention import TerminalRegistry
from relaydeck.client.features.terminal.shell_session import SHELL_LIVE
from tests.support.scripted_sessions import make_scripted_sessions


@pytest.mark.parametrize("count", [1, 3])
def test_idle_warm_sessions_wait_until_expiry_and_stop_without_closing_remote(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("relaydeck.client.app.sessions.retained.WARM_IDLE_MS", 250)

    async def scenario() -> None:
        sessions = make_scripted_sessions()
        pool = RetainedSessions(
            clock=lambda: int(time.monotonic() * 1000), registry=TerminalRegistry(idle_ms=550)
        )
        for n in range(count):
            entry = await pool.acquire(
                "host",
                "shell",
                str(n),
                "epoch",
                lambda: sessions.factory.make_shell(host_id="host"),
            )
            await entry.session.open(str(n))
        pool.release()
        await asyncio.sleep(0.03)  # allow hidden VIEW conversions to settle
        scans = 0
        original = pool.evict_idle

        async def counted() -> None:
            nonlocal scans
            scans += 1
            await original()

        pool.evict_idle = counted  # type: ignore[method-assign]
        task = asyncio.create_task(pool.run_expiry())
        try:
            await asyncio.sleep(0.10)
            initial = scans
            assert initial <= 2
            assert len(pool.entries()) == count and all(e.session.lease for e in pool.entries())
            await asyncio.sleep(0.06)
            assert scans == initial
            async with asyncio.timeout(1):
                while any(e.session.lease for e in pool.entries()):
                    await asyncio.sleep(0.005)
            assert len(pool.entries()) == count and not sessions.ops.closes
            assert all(not e.session.disposed for e in pool.entries())
            async with asyncio.timeout(1):
                while pool.entries():
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.04)
            settled = scans
            await asyncio.sleep(0.06)
            assert scans == settled and settled < 15
            assert not sessions.ops.closes
        finally:
            await pool.shutdown()
            await asyncio.wait_for(task, 1)
        assert task.done() and all(s.disposed for s in sessions.factory.created)

    asyncio.run(scenario())


def test_return_before_idle_deadline_rearms_without_disconnecting_visible_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("relaydeck.client.app.sessions.retained.WARM_IDLE_MS", 150)

    async def scenario() -> None:
        sessions = make_scripted_sessions()
        pool = RetainedSessions(clock=lambda: int(time.monotonic() * 1000))

        def make() -> Any:
            return sessions.factory.make_shell(host_id="host")

        entry = await pool.acquire("host", "shell", "one", "epoch", make)
        await entry.session.open("one")
        pool.release()
        task = asyncio.create_task(pool.run_expiry())
        try:
            await asyncio.sleep(0.07)
            assert await pool.acquire("host", "shell", "one", "epoch", make) is entry
            await asyncio.sleep(0.20)
            assert entry.session.lease and pool.next_expiry_ms() is None
        finally:
            await pool.shutdown()
            await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_failed_warm_drop_gets_a_bounded_retry_instead_of_due_now_spin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("relaydeck.client.app.sessions.retained.WARM_IDLE_MS", 1000)

    async def scenario() -> None:
        sessions = make_scripted_sessions()
        now = [1000]
        pool = RetainedSessions(clock=lambda: now[0])
        entry = await pool.acquire(
            "host", "shell", "one", "epoch", lambda: sessions.factory.make_shell(host_id="host")
        )
        await entry.session.open("one")
        pool.release()
        await asyncio.sleep(0.01)
        calls = 0

        async def failed() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("synthetic disconnect failure")

        entry.session.disconnect = failed
        now[0] += 1001
        task = asyncio.create_task(pool.run_expiry())
        try:
            await asyncio.sleep(0.12)
            assert calls == 1
            due = pool.next_expiry_ms()
            assert due is not None and 14_000 < due - pool.clock() <= 15_000
        finally:
            await pool.shutdown()
            await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_expiry_survives_a_failing_pass_and_cancellation_stays_clean(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        pool = RetainedSessions(clock=lambda: int(time.monotonic() * 1000))
        calls = 0

        async def flaky() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("secret detail")

        pool.evict_idle = flaky  # type: ignore[method-assign]
        with caplog.at_level("WARNING"):
            task = asyncio.create_task(pool.run_expiry())
            async with asyncio.timeout(2):
                while calls < 2:
                    await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text


def test_a_persistently_failing_expiry_pass_backs_off_instead_of_spinning() -> None:
    async def scenario() -> None:
        pool = RetainedSessions(clock=lambda: int(time.monotonic() * 1000))
        calls = 0

        async def broken() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("synthetic failure")

        pool.evict_idle = broken  # type: ignore[method-assign]
        task = asyncio.create_task(pool.run_expiry())
        await asyncio.sleep(0.35)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 2 <= calls <= 8  # retried, but bounded by the backoff

    asyncio.run(scenario())


def test_a_failed_reader_is_logged_by_type_and_only_a_replacement_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        pool = RetainedSessions()
        gate = asyncio.Event()
        runs = 0

        async def run_reader() -> None:
            nonlocal runs
            runs += 1
            if runs == 1:
                await gate.wait()
                raise ValueError("secret detail")

        session = SimpleNamespace(phase=SHELL_LIVE, controller=object(), run=run_reader)
        entry = RetainedSession(pool, "host", "one", "shell", "epoch", session, 1)
        with caplog.at_level("WARNING"):
            entry.supervise(session)
            await asyncio.sleep(0.02)
            session.controller = object()  # a replacement became live mid-read
            gate.set()
            async with asyncio.timeout(2):
                while runs < 2:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
        assert runs == 2

    asyncio.run(scenario())
    assert "ValueError" in caplog.text and "secret detail" not in caplog.text


def test_failed_budget_eviction_retries_before_the_full_warm_idle_period() -> None:
    async def scenario() -> None:
        sessions = make_scripted_sessions()
        now = [1000]
        pool = RetainedSessions(clock=lambda: now[0])
        pool.warm_budget = 0
        entry = await pool.acquire(
            "host", "shell", "one", "epoch", lambda: sessions.factory.make_shell(host_id="host")
        )
        await entry.session.open("one")
        calls = 0

        async def failed() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("synthetic disconnect failure")

        entry.session.disconnect = failed
        await pool.acquire(
            "host", "shell", "two", "epoch", lambda: sessions.factory.make_shell(host_id="host")
        )
        try:
            await asyncio.sleep(0.02)
            assert calls == 1 and pool.next_expiry_ms() == now[0] + 15_000
            now[0] += 15_001
            await pool.evict_idle()
            await asyncio.sleep(0.02)
            assert calls == 2 and pool.next_expiry_ms() == now[0] + 15_000
        finally:
            await pool.shutdown()

    asyncio.run(scenario())
