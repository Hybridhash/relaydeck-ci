"""Source replacement retires the old request and its publication authority."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.hooks.inventory import use_inventory
from relaydeck.client.app.resources import use_sources
from relaydeck.client.screens.settings.parts import hosts_pane as components
from tests.support.host_harness import render_component, run
from tests.support.ui.navigation import _state, _StubController

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/gateway"


def document(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / (name + ".json")).read_text())


class PendingRead:
    def __init__(self) -> None:
        self.gate: asyncio.Future[None] | None = None
        self.cancelled = False

    async def wait(self) -> None:
        self.gate = asyncio.get_running_loop().create_future()
        try:
            await asyncio.shield(self.gate)
        except asyncio.CancelledError:
            self.cancelled = True
            await self.gate

    def release(self) -> None:
        assert self.gate is not None
        self.gate.set_result(None)


@pytest.mark.parametrize("change", ["source", "account", "sign_out"])
def test_management_replacement_cannot_publish_old_metadata(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async def scenario() -> None:
        pending = PendingRead()
        managers: list[Any] = []
        publications: list[Any] = []
        holder: dict[str, Any] = {}
        original = components.ManagementController

        def manager(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            managers.append(result)
            return result

        monkeypatch.setattr(components, "ManagementController", manager)

        class Source:
            def __init__(self, account: str, *, blocked: bool = False) -> None:
                self.account = account
                self.blocked = blocked
                self.calls = 0

            async def me(self) -> dict[str, Any]:
                return document("me") | {
                    "account": self.account,
                    "device_id": "synthetic-device",
                }

            async def hosts(self) -> dict[str, Any]:
                self.calls += 1
                if self.blocked:
                    await pending.wait()
                return {"hosts": []}

            async def devices(self) -> dict[str, Any]:
                return document("devices")

            async def revoke_host(self, host: str) -> None:
                raise AssertionError("No command is expected")

        old = Source("acct-1", blocked=True)
        new = Source("acct-2" if change == "account" else "acct-1")

        @ft.component
        def View() -> Any:
            source, replace = ft.use_state(old)
            visible, show = ft.use_state(True)
            holder.update(replace=replace, show=show)
            return (
                components.ManagementPane(
                    transport=source,
                    account=source.account,
                    destination="settings",
                    selected_host="",
                    on_hosts=publications.append,
                    on_logout=lambda: None,
                )
                if visible
                else ft.Text("Signed out")
            )

        app = await render_component(View)
        assert pending.gate is not None and old.calls == 1
        if change == "sign_out":
            holder["show"](False)
        else:
            holder["replace"](new)
        await app.settle()
        assert pending.cancelled and not managers[0].alive
        assert managers[0].hosts == [] and managers[0].devices == []
        expected = [] if change == "sign_out" else [[]]
        assert publications == expected
        if change != "sign_out":
            assert len(managers) == 2 and managers[1].alive and new.calls == 1
        pending.release()
        await app.settle()
        assert publications == expected
        holder["show"](False)
        await app.settle()
        assert all(not owner.alive for owner in managers)

    run(scenario())


@pytest.mark.parametrize("change", ["source", "account", "sign_out"])
def test_inventory_replacement_clears_old_pager_and_cancels_read(change: str) -> None:
    async def scenario() -> None:
        pending = PendingRead()
        holder: dict[str, Any] = {}
        controller = _StubController()
        controller.push(_state())

        class Source:
            def __init__(self, *, blocked: bool = False) -> None:
                self.blocked = blocked
                self.calls = 0

            async def agents(
                self, host: str, limit: int = 50, page_token: str = "", **kwargs: Any
            ) -> dict[str, Any]:
                self.calls += 1
                if self.blocked:
                    await pending.wait()
                return {
                    "host_id": host,
                    "host_epoch": "epoch-7",
                    "agents": [],
                    "total": 0,
                    "next_page_token": "",
                }

        old, new = Source(blocked=True), Source()

        @ft.component
        def Inventory(source: Any, account: str) -> Any:
            sources = use_sources(source, account)
            state = use_inventory(
                account=account,
                app_state=controller.state,
                controller=controller,
                foreground=True,
                transport=sources.inventory,
            )
            holder["pager"] = state[7]
            return ft.Text(str(state[7].total), key="total")

        @ft.component
        def View() -> Any:
            source, replace = ft.use_state(old)
            account, select = ft.use_state("acct-1")
            visible, show = ft.use_state(True)
            holder.update(replace=replace, select=select, show=show)
            return Inventory(source, account) if visible else ft.Text("Signed out")

        app = await render_component(View)
        retired = holder["pager"]
        assert pending.gate is not None and old.calls == 1
        if change == "sign_out":
            holder["show"](False)
        else:
            holder["replace"](new)
            if change == "account":
                holder["select"]("acct-2")
        await app.settle()
        assert pending.cancelled
        assert retired.account == "" and retired.rows == {} and not retired.busy
        if change != "sign_out":
            assert holder["pager"] is not retired and new.calls == 1
            assert not holder["pager"].error
        pending.release()
        await app.settle()
        assert retired.rows == {} and retired.account == ""
        if change != "sign_out":
            assert app.find("total").value == "0" and new.calls == 1
        holder["show"](False)
        await app.settle()

    run(scenario())
