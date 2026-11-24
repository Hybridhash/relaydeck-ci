"""A status event costs the Root one pass over the agents' names, not one per use."""

from __future__ import annotations

import asyncio
import gc
import statistics
import time
from dataclasses import replace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.app.root import Root
from relaydeck.client.models import inventory_selectors
from tests.support.host_harness import render_component, run
from tests.support.scripted_sessions import make_scripted_sessions
from tests.support.sync.delta_inventory import HOST, InventoryHarness

NAMES_PASS_CPU_MS = 10


async def _mount(harness: InventoryHarness) -> Any:
    sessions = make_scripted_sessions()

    async def held(*args: Any, **kwargs: Any) -> None:
        await asyncio.Future()

    harness.controller.run = held  # type: ignore[method-assign, assignment]

    @ft.component
    def Page() -> Any:
        mounted, set_mounted = ft.use_state(True)
        return ft.Column(
            controls=[
                Root(
                    controller=harness.controller,
                    transport=harness.transport,
                    host_id=HOST,
                    bridge=RouteBridge(route="/", width=1280),
                    go=lambda route: None,
                    session_factory=sessions.factory,
                    account="acct-1",
                )
                if mounted
                else ft.Text("Closed"),
                ft.Button("Close", key="close", on_click=lambda e: set_mounted(False)),
            ]
        )

    harness.app = await render_component(Page)
    await harness.app.settle(30)
    return harness.app


def test_a_status_event_at_4096_agents_names_them_once_and_stays_in_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(4096)
        app = await _mount(harness)
        passes: list[float] = []
        real = inventory_selectors._compute_names

        def timed(inventory: Any, last: Any) -> Any:
            before = time.process_time()
            try:
                return real(inventory, last)
            finally:
                passes.append((time.process_time() - before) * 1000)

        monkeypatch.setattr(inventory_selectors, "_compute_names", timed)
        try:
            gc.collect()  # what earlier tests left behind is not this event's cost
            for number in range(1, 6):
                passes.clear()
                harness.event(number, status="blocked" if number % 2 else "working")
                await app.settle(20)
                assert len(passes) == 1, passes  # one changed inventory, one pass
                events.append(passes[0])
            assert statistics.median(events) < NAMES_PASS_CPU_MS, events
        finally:
            await harness.close()

    events: list[float] = []
    run(scenario())


def test_a_render_without_an_inventory_change_names_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        harness = InventoryHarness(200)
        app = await _mount(harness)
        worked: list[int] = []
        real = inventory_selectors._compute_names

        def counting(inventory: Any, last: Any) -> Any:
            worked.append(len(inventory.agents))
            return real(inventory, last)

        monkeypatch.setattr(inventory_selectors, "_compute_names", counting)
        try:
            for stale in (True, False):  # app state changes, the inventory object stays
                harness.controller._set(replace(harness.controller.state, stale=stale))
                await app.settle(20)
            assert worked == []
        finally:
            await harness.close()

    run(scenario())
