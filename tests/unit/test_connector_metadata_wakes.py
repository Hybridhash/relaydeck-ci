"""Held reads keep idle work bounded without losing events, EOF, or heartbeat."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from relaydeck.connector.herdr import HerdrAdapter, HerdrProtocolError
from relaydeck.connector.link import ConnectorLink, LinkConfig
from relaydeck.connector.metadata import SubscriptionReads
from relaydeck.connector.source import HerdrSource
from tests.support.paths import TESTS_DIR


class Stream:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.reads = 0

    async def __anext__(self) -> Any:
        self.reads += 1
        value = await self.queue.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        pass


def test_held_reads_preserve_simultaneous_completions_and_cancel_cleanly() -> None:
    async def scenario() -> None:
        streams = (Stream(), Stream())
        reads = SubscriptionReads()
        task = asyncio.create_task(reads.wait(streams, None))
        await asyncio.sleep(0.22)
        assert [s.reads for s in streams] == [1, 1] and not task.done()
        for i, stream in enumerate(streams):
            stream.queue.put_nowait((str(i), {}))
        assert await task
        await reads.cancel_pending()
        assert len(reads.tasks) == 2
        assert [reads.tasks[s].result()[0] for s in streams] == ["0", "1"]
        await reads.close()
        assert reads.tasks == {}

    asyncio.run(scenario())


def test_idle_link_keeps_one_receive_and_one_subscription_and_still_pings(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
        source._snapshot = {"panes": []}
        stream = Stream()
        source._subscription = stream  # type: ignore[assignment]
        collects: list[int] = []
        collect = source.collect_live

        async def counted(timeout: float) -> list[dict[str, Any]]:
            collects.append(1)
            return await collect(timeout)

        source.collect_live = counted  # type: ignore[method-assign]

        class Transport:
            receives = 0
            sent: list[str] = []

            async def recv_text(self) -> str:
                self.receives += 1
                await asyncio.Future()
                raise AssertionError("unreachable")

            async def send_text(self, text: str) -> None:
                self.sent.append(text)

            async def close(self) -> None:
                pass

        transport = Transport()
        link = ConnectorLink(
            LinkConfig(
                "http://localhost",
                "h",
                "synthetic",
                str(tmp_path / "journal.db"),
                ping_interval_s=0.04,
            ),
            source,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(link._serve_messages(transport, stop))
        try:
            await asyncio.sleep(0.23)
            assert transport.receives == 1 and stream.reads == 1 and collects == []
            assert 3 <= sum(json.loads(t)["kind"] == "ping" for t in transport.sent) <= 8
            stream.queue.put_nowait(StopAsyncIteration())
            assert await asyncio.wait_for(task, 1) == "drop"
            assert source._metadata_reads.tasks == {} and link._source_stale
        finally:
            stop.set()
            await task
            await source.close()
            await link.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["", "moved", "revision", "unknown", "unavailable"])
def test_targeted_agent_get_has_current_revision_and_guarded_fallback(
    failure: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        source = HerdrSource(HerdrAdapter(socket_path=str(tmp_path / "unused.sock")))
        golden = json.loads((TESTS_DIR / "fixtures/upstream/agent_list.json").read_text())
        source._agents = golden["result"]["agents"]
        calls: list[str] = []
        old = source._agents[0]

        async def get(target: str) -> dict[str, Any]:
            calls.append(target)
            if failure == "unavailable":
                raise HerdrProtocolError("upstream_closed", "synthetic")
            return {
                "type": "agent_info",
                "agent": {
                    **old,
                    "revision": 42 if failure == "revision" else 44,
                    "tab_id": "new-tab" if failure == "moved" else old["tab_id"],
                    "agent_status": "working",
                },
            }

        source._adapter.agent_get = get  # type: ignore[method-assign]
        events = [
            {
                "type": "pane.upserted",
                "resource_id": "unknown" if failure == "unknown" else old["pane_id"],
                "event_id": f"event-{i}",
                "payload": {"agent_status": status},
            }
            for i, status in enumerate(("blocked", "working"))
        ]
        result = await source.metadata_deltas(events, "h")
        if failure:
            assert result is None and source._agents[0] is old
            assert calls == ([] if failure == "unknown" else [old["terminal_id"]])
        else:
            assert result is not None
            assert calls == [old["terminal_id"]]  # one lookup, all transitions retained
            assert [e["payload"]["status"] for e in result] == ["blocked", "working"]
            assert all(e["resource_revision"] == 44 for e in result)
            assert source._agents[0]["revision"] == 44
        await source.close()

    asyncio.run(scenario())
