"""Only a completed, current full read can establish an incremental baseline."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import flet as ft
import pytest

from relaydeck.client.features.inventory.paging import InventoryPager
from relaydeck.client.ports.transport import SseFrame, TransportError
from tests.security.helpers import NOW, SECRET, mint_token
from tests.support.host_harness import run
from tests.support.sync.delta_inventory import HOST, InventoryHarness


def render_rows(pager: InventoryPager) -> ft.Column:
    return ft.Column(
        controls=[
            ft.Text(
                f"{row['status']}/{row['tab_id']}", key=f"row-{row['host_id']}:{row['terminal_id']}"
            )
            for row in pager.visible_rows
        ]
    )


@pytest.mark.parametrize("trigger", ["moved", "snapshot"])
def test_failed_full_read_recovers_other_stale_agent_on_the_next_delta(trigger: str) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(2)
        app = await harness.mount(render_rows)
        pager = harness.control["pager"]
        original = harness.transport.agents
        fail = True
        before = harness.backend.snapshots[HOST]["agents"][1]
        terminal = before["terminal_id"]
        key = f"{HOST}:{terminal}"

        async def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal fail
            if fail:
                fail = False
                harness.calls += 1
                raise TransportError("network_unavailable", "synthetic outage", True)
            return await original(*args, **kwargs)

        harness.transport.agents = fail_once  # type: ignore[method-assign]
        try:
            moved = {**before, "status": "blocked", "tab_id": "new-tab", "revision": 44}
            harness.backend.snapshots[HOST]["agents"][1] = moved
            if trigger == "moved":
                event = harness.backend.ingest_event(
                    HOST,
                    {
                        "event_id": "moved",
                        "host_epoch": "epoch-7",
                        "type": "agent.upserted",
                        "resource_id": terminal,
                        "resource_revision": 44,
                        "observed_at": "2026-10-04T10:00:00Z",
                        "payload": moved,
                    },
                    NOW,
                    SECRET,
                )["ok"]
                harness.controller._apply_frame(SseFrame("metadata", json.dumps(event)))
            else:
                harness.controller.accept_snapshot(HOST, harness.backend.snapshots[HOST])
            await app.settle(20)
            assert not fail and "network_unavailable" in pager.error
            assert harness.controller.state.inventory.agents[key].status.value == "blocked"
            assert app.find(f"row-{key}").value == f"working/{before['tab_id']}"

            calls = harness.calls
            number = 2 if trigger == "moved" else 1
            harness.event(number, status="done")  # another loaded agent, after transport recovers
            await app.settle(20)
            assert harness.calls == calls + 1
            assert app.find(f"row-{key}").value == "blocked/new-tab"
            assert not pager.error

            calls = harness.calls
            harness.event(number + 1, status="working")
            await app.settle()
            assert harness.calls == calls  # successful recovery restores the cheap path
        finally:
            await harness.close()

    run(scenario())


def test_partial_multi_host_failure_cannot_establish_a_delta_baseline() -> None:
    async def scenario() -> None:
        harness = InventoryHarness(2)
        other = "other-host"
        twin = copy.deepcopy(harness.backend.snapshots[HOST])
        twin["host"]["id"] = other
        for kind in ("agents", "panes", "tabs", "workspaces"):
            for row in twin[kind]:
                row["host_id"] = other
        harness.backend.ingest_snapshot(other, twin, NOW, SECRET)
        harness.controller.accept_snapshot(other, harness.backend.snapshots[other])
        harness.token = mint_token(harness.state, hosts=frozenset({HOST, other}))
        app = await harness.mount(render_rows)
        pager = harness.control["pager"]
        original = harness.transport.agents
        failed = False
        requested: list[str] = []

        async def fail_other(host_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal failed
            requested.append(host_id)
            if host_id == other and not failed:
                failed = True
                harness.calls += 1
                raise TransportError("network_unavailable", "synthetic outage", True)
            return await original(host_id, *args, **kwargs)

        harness.transport.agents = fail_other  # type: ignore[method-assign]
        try:
            key = f"{other}:term-1"
            before = harness.backend.snapshots[other]["agents"][0]
            harness.backend.snapshots[other]["agents"][0] = {
                **before,
                "status": "blocked",
                "tab_id": "new-tab",
                "revision": 44,
            }
            harness.controller.accept_snapshot(other, harness.backend.snapshots[other])
            await app.settle(20)
            assert set(requested) == {HOST, other} and failed
            assert "network_unavailable" in pager.error
            assert app.find(f"row-{key}").value == f"working/{before['tab_id']}"

            requested.clear()
            harness.event(1, status="done")
            await app.settle(20)
            assert set(requested) == {HOST, other}
            assert not pager.error and app.find(f"row-{key}").value == "blocked/new-tab"

            requested.clear()
            harness.event(2, status="working")
            await app.settle()
            assert requested == []
        finally:
            await harness.close()

    run(scenario())


@pytest.mark.parametrize("boundary", ["account", "snapshot"])
def test_obsolete_success_cannot_bless_a_newer_failed_refresh(boundary: str) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(2)
        app = await harness.mount(render_rows)
        pager = harness.control["pager"]
        original = harness.transport.agents
        entered, release = asyncio.Event(), asyncio.Event()
        phase = "hold"

        async def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal phase
            if phase == "hold":
                phase = "fail"
                doc = await original(*args, **kwargs)
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()  # return an obsolete successful page after cancellation
                return doc
            if phase == "fail":
                phase = "recover"
                harness.calls += 1
                raise TransportError("network_unavailable", "synthetic outage", True)
            return await original(*args, **kwargs)

        harness.transport.agents = delayed  # type: ignore[method-assign]
        try:
            harness.control["refresh"]()
            await asyncio.wait_for(entered.wait(), 1)
            before = harness.backend.snapshots[HOST]["agents"][1]
            key = f"{HOST}:{before['terminal_id']}"
            harness.backend.snapshots[HOST]["agents"][1] = {
                **before,
                "status": "blocked",
                "tab_id": "new-tab",
                "revision": 44,
            }
            harness.controller.accept_snapshot(HOST, harness.backend.snapshots[HOST])
            if boundary == "account":
                harness.token = mint_token(harness.state, account="acct-2")
                harness.control["account"]("acct-2")
            await app.settle(20)
            assert phase == "recover" and "network_unavailable" in pager.error
            release.set()
            await app.settle(10)
            if boundary == "account":
                assert pager.account == "acct-2" and not pager.rows
            else:
                assert app.find(f"row-{key}").value == f"working/{before['tab_id']}"

            calls = harness.calls
            harness.event(1, status="done")
            await app.settle(20)
            assert harness.calls == calls + 1
            assert not pager.error and app.find(f"row-{key}").value == "blocked/new-tab"
        finally:
            release.set()
            await harness.close()

    run(scenario())
