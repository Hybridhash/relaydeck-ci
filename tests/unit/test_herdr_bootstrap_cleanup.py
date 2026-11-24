"""The adapter's bootstrap leaves nothing behind on any unsuccessful end, cancellation included:
the subscription is closed and the collector task is cancelled and finished before the call ends."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from relaydeck.connector.herdr import (
    BootstrapResult,
    HerdrAdapter,
    HerdrProtocolError,
    Subscription,
)
from relaydeck.connector.source import HerdrSource
from tests.support.gateway.herdr_adapter import _FakeWriter


class Silent:
    """A subscription reader that never has a line."""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""


class Ended:
    async def readline(self) -> bytes:
        return b""


class Held(HerdrAdapter):
    """The adapter with its transport calls replaced; the bootstrap code is the production code."""

    def __init__(self, reader: Any = None) -> None:
        super().__init__(opener=self._unused)
        self.writer = _FakeWriter()
        self.reader = reader or Silent()
        self.snapshot: Callable[[], Awaitable[dict[str, Any]]] = self._instant
        self.agents: Callable[[], Awaitable[list[dict[str, Any]]]] = self._none

    @staticmethod
    async def _unused() -> tuple[Any, Any]:
        raise AssertionError("the transport is replaced")

    async def ping(self) -> dict[str, Any]:
        return {}

    async def _open_subscription(self, subscriptions: list[dict[str, Any]]) -> Subscription:
        return Subscription(self.reader, self.writer)

    async def _instant(self) -> dict[str, Any]:
        return {"snapshot": {}}

    async def _none(self) -> list[dict[str, Any]]:
        return []

    async def session_snapshot(self) -> dict[str, Any]:
        return await self.snapshot()

    async def agent_list(self) -> list[dict[str, Any]]:
        return await self.agents()


async def others() -> list[asyncio.Task[Any]]:
    return [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


async def never() -> Any:
    await asyncio.Event().wait()


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_a_snapshot_that_fails_closes_the_subscription_and_ends_the_collector() -> None:
    async def scenario() -> None:
        adapter = Held()

        async def broken() -> dict[str, Any]:
            raise HerdrProtocolError("bad_snapshot", "synthetic")

        adapter.snapshot = broken
        with pytest.raises(HerdrProtocolError):
            await adapter.bootstrap([])
        assert adapter.writer.closed and await others() == []

    run(scenario)


def test_a_bootstrap_cancelled_during_the_snapshot_closes_everything() -> None:
    async def scenario() -> None:
        adapter = Held()
        adapter.snapshot = never
        task = asyncio.create_task(adapter.bootstrap([]))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert adapter.writer.closed and await others() == []

    run(scenario)


def test_a_bootstrap_cancelled_at_the_yield_before_handoff_closes_everything() -> None:
    async def scenario() -> None:
        adapter = Held()
        holder: list[asyncio.Task[Any]] = []

        async def cancel_self_then_answer() -> list[dict[str, Any]]:
            holder[0].cancel()
            return []

        adapter.agents = cancel_self_then_answer
        holder.append(asyncio.create_task(adapter.bootstrap([])))
        with pytest.raises(asyncio.CancelledError):
            await holder[0]
        assert adapter.writer.closed and await others() == []

    run(scenario)


def test_a_timeout_around_the_bootstrap_leaves_nothing_open() -> None:
    async def scenario() -> None:
        adapter = Held()
        adapter.snapshot = never
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await adapter.bootstrap([])
        assert adapter.writer.closed and await others() == []

    run(scenario)


def test_a_bootstrap_cancelled_while_the_subscribe_ack_is_awaited_closes_the_connection() -> None:
    async def scenario() -> None:
        writer = _FakeWriter()

        async def opener() -> tuple[Any, Any]:
            return Silent(), writer

        adapter = HerdrAdapter(opener=opener)

        async def no_ping() -> dict[str, Any]:
            return {}

        adapter.ping = no_ping  # type: ignore[method-assign]
        task = asyncio.create_task(adapter.bootstrap([]))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer.closed and await others() == []

    run(scenario)


def test_a_subscription_that_ends_during_the_bootstrap_is_closed_and_refused() -> None:
    async def scenario() -> None:
        adapter = Held(reader=Ended())
        with pytest.raises(HerdrProtocolError) as caught:
            await adapter.bootstrap([])
        assert caught.value.code == "upstream_closed"
        assert adapter.writer.closed and await others() == []

    run(scenario)


def test_a_good_bootstrap_hands_over_an_open_subscription_and_no_task() -> None:
    async def scenario() -> None:
        adapter = Held()
        result = await adapter.bootstrap([])
        assert isinstance(result, BootstrapResult)
        assert not adapter.writer.closed and await others() == []
        await result.subscription.aclose()

    run(scenario)


def test_the_source_owns_the_handed_over_subscription_even_if_its_next_step_is_cancelled() -> None:
    async def scenario() -> None:
        adapter = Held()
        source = HerdrSource(adapter)

        async def cancelled() -> None:
            raise asyncio.CancelledError

        reads = source._metadata_reads
        original = reads.close
        reads.close = cancelled  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await source.bootstrap()
        assert source._subscription is not None and not adapter.writer.closed
        reads.close = original  # type: ignore[method-assign]
        await source.close()
        assert adapter.writer.closed

    run(scenario)
