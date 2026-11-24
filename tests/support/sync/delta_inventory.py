"""Real inventory adapter and Flet hook over a synthetic, local gateway."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import flet as ft
import httpx

from relaydeck.client.app.hooks.inventory import use_inventory
from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.platform.transport.transport import HttpxGatewayTransport
from relaydeck.client.ports.transport import SseFrame
from relaydeck.domain.models import ConnectionState
from relaydeck.gateway.app.bootstrap import create_app
from tests.security.helpers import NOW, SECRET, make_state, mint_token
from tests.support.host_harness import render_component

HOST = "home-mac"
KEY = f"{HOST}:term-1"


class InventoryHarness:
    def __init__(self, count: int) -> None:
        self.state, self.backend = make_state()
        snapshot = self.backend.snapshots[HOST]
        first = snapshot["agents"][0]
        snapshot["agents"] = [
            {
                **first,
                "terminal_id": "term-1" if i == 0 else f"term-{i + 1:05}",
                "name": f"agent-{i:05}",
                "status": "working",
                "revision": 43,
            }
            for i in range(count)
        ]
        self.backend.ingest_snapshot(HOST, snapshot, NOW, SECRET)
        self.token = mint_token(self.state)
        self.calls = 0
        self.control: dict[str, Any] = {}
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.state)), base_url="http://gw"
        )
        owner = self

        class Counting(HttpxGatewayTransport):
            async def agents(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                owner.calls += 1
                return await super().agents(*args, **kwargs)

        async def token() -> str:
            return self.token

        self.transport = Counting("http://gw", token, self.http)
        self.controller = SyncController(self.transport)
        self.controller.accept_snapshot(HOST, self.backend.snapshots[HOST])
        self.controller._set(
            replace(
                self.controller.state,
                connection=replace(
                    self.controller.state.connection, host_id=HOST, state=ConnectionState.LIVE
                ),
            )
        )

    async def mount(self, render: Callable[[Any], Any] | None = None) -> Any:
        owner = self

        @ft.component
        def Body(current: Any, account: str) -> Any:
            values = use_inventory(
                account=account,
                app_state=current,
                controller=owner.controller,
                foreground=True,
                transport=owner.transport,
            )
            owner.control.update(
                pager=values[7], more=values[6], refresh=values[8], filters=values[10]
            )
            if render is not None:
                return render(values[7])
            rows = values[7].rows
            return ft.Text(str(rows.get(KEY, {}).get("status", "")), key="status")

        @ft.component
        def Inventory() -> Any:
            current, set_current = ft.use_state(owner.controller.state)
            account, set_account = ft.use_state("acct-1")
            mounted, set_mounted = ft.use_state(True)
            owner.control["account"] = set_account
            ft.use_effect(
                lambda: owner.controller.subscribe(set_current),
                dependencies=[],
                cleanup=lambda: owner.controller.unsubscribe(set_current),
            )

            return ft.Column(
                controls=[
                    Body(current, account) if mounted else ft.Text("Closed"),
                    ft.Button("Close", key="close", on_click=lambda e: set_mounted(False)),
                ]
            )

        self.app = await render_component(Inventory)
        await self.app.settle(20)
        return self.app

    def event(
        self, number: int, *, status: str = "blocked", agent: str = "term-1", **kw: Any
    ) -> dict[str, Any]:
        row = self.backend.snapshots[HOST]["agents"][0]
        result = self.backend.ingest_event(
            HOST,
            {
                "event_id": f"event-{number}",
                "host_epoch": "epoch-7",
                "type": "agent.upserted",
                "resource_id": agent,
                "resource_revision": 43 + number,
                "observed_at": "2026-10-04T10:00:00Z",
                "payload": {**row, "terminal_id": agent, "status": status, "status_changed": True},
            },
            NOW,
            SECRET,
        )
        event = {**result["ok"], **kw}
        self.controller._apply_frame(SseFrame("metadata", json.dumps(event)))
        return event

    async def close(self) -> None:
        await self.app.set_props_and_fire("close", {}, "click")
        await self.http.aclose()
        self.state.push.close()
