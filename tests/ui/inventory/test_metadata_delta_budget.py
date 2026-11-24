"""Known status deltas avoid page reads; uncertain contexts still refresh."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from relaydeck.client.features.inventory.paging import InventoryFilters
from relaydeck.client.ports.transport import SseFrame, TransportError
from tests.security.helpers import SECRET
from tests.support.host_harness import run
from tests.support.patch_meter import PatchMeter
from tests.support.sync.delta_inventory import HOST, KEY, InventoryHarness


@pytest.mark.parametrize("count", [1, 50, 4096])
def test_status_updates_only_one_loaded_row_without_a_request(count: int) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(count)
        app = await harness.mount()
        try:
            pager = harness.control["pager"]
            before = dict(pager.rows)
            calls = harness.calls
            meter = PatchMeter(app)
            event = harness.event(1)
            await app.settle()
            assert harness.calls == calls
            assert app.find("status").value == "blocked"
            assert pager.rows[KEY] is not before[KEY]
            assert all(pager.rows[k] is row for k, row in before.items() if k != KEY)
            assert meter.bytes < 1000
            expected = harness.state.inventory.rows(
                "acct-1",
                HOST,
                harness.backend.snapshots[HOST],
                harness.backend.event_logs[HOST],
                SECRET,
            )
            reference = next(row for row in expected if row["terminal_id"] == "term-1")
            assert {
                k: pager.rows[KEY][k] for k in ("activity_id", "unread", "revision", "status")
            } == {k: reference[k] for k in ("activity_id", "unread", "revision", "status")}
            row = pager.rows[KEY]
            harness.controller._apply_frame(SseFrame("metadata", json.dumps(event)))
            await app.settle()
            assert harness.calls == calls and pager.rows[KEY] is row
            harness.control["refresh"]()
            await app.settle(20)
            assert harness.calls == calls + 1  # explicit Refresh is always authoritative
        finally:
            await harness.close()

    run(scenario())


@pytest.mark.parametrize("reason", ["gap", "unknown", "filter", "grants", "account", "snapshot"])
def test_uncertain_or_changed_context_falls_back_to_a_full_read(reason: str) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(50)
        app = await harness.mount()
        try:
            calls = harness.calls
            if reason == "gap":
                with pytest.raises(TransportError, match="gap"):
                    harness.event(1, sequence=3)
            elif reason == "unknown":
                harness.event(1, agent="new-agent")
            elif reason == "filter":
                harness.control["filters"](InventoryFilters(status="working"))
                await app.settle(20)
                calls = harness.calls
                harness.event(1)
            elif reason == "grants":
                harness.controller._set(
                    replace(harness.controller.state, granted_hosts=frozenset())
                )
            elif reason == "account":
                harness.control["account"]("acct-2")
            else:
                harness.backend.snapshots[HOST]["agents"][0]["title"] = "Fresh title"
                harness.controller.accept_snapshot(HOST, harness.backend.snapshots[HOST])
            await app.settle(20)
            if reason == "grants":
                assert not harness.control["pager"].rows
            else:
                assert harness.calls == calls + 1
        finally:
            await harness.close()

    run(scenario())


def test_delta_then_stale_continuation_recovers_with_the_same_filters() -> None:
    async def scenario() -> None:
        harness = InventoryHarness(63)
        app = await harness.mount()
        try:
            pager = harness.control["pager"]
            assert pager.more and len(pager.rows) == 50
            harness.event(1)
            await app.settle()
            calls = harness.calls
            harness.control["more"]()
            await app.settle(30)
            assert harness.calls == calls + 2  # rejected old cursor, then a current first page
            assert not pager.error and pager.rows[KEY]["status"] == "blocked"
            harness.control["more"]()
            await app.settle(20)
            assert len(pager.rows) == 63 and not pager.more
        finally:
            await harness.close()

    run(scenario())


def test_delta_during_an_inflight_read_fences_the_obsolete_page() -> None:
    async def scenario() -> None:
        harness = InventoryHarness(50)
        app = await harness.mount()
        entered, release = asyncio.Event(), asyncio.Event()
        original = harness.transport.agents

        async def held(*args: Any, **kwargs: Any) -> dict[str, Any]:
            doc = await original(*args, **kwargs)
            if not entered.is_set():
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()  # an already-completed transport may return late
            return doc

        harness.transport.agents = held  # type: ignore[method-assign]
        try:
            harness.control["refresh"]()
            await asyncio.wait_for(entered.wait(), 1)
            harness.event(1)
            await app.settle(20)
            assert harness.control["pager"].rows[KEY]["status"] == "blocked"
            release.set()
            await app.settle(10)
            assert harness.control["pager"].rows[KEY]["status"] == "blocked"
            assert not harness.control["pager"].error
            calls = harness.calls
            harness.event(2, status="working")
            await app.settle()
            assert harness.calls == calls  # the late page cannot replace the current proof
        finally:
            release.set()
            await harness.close()

    run(scenario())
