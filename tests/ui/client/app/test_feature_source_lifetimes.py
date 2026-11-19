"""A retired settings source cannot publish into its replacement."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from relaydeck.client.app.resources import bind_sources, use_sources
from relaydeck.client.features.web_push.web_push import OFF_TEXT, WebPushPanel
from tests.support.host_harness import render_component, run


class PushSource:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.gate: asyncio.Future[dict[str, bool]] | None = None
        self.calls = 0

    async def web_push_state(self) -> dict[str, bool]:
        self.calls += 1
        if self.blocked:
            self.gate = asyncio.get_running_loop().create_future()
            try:
                return await asyncio.shield(self.gate)
            except asyncio.CancelledError:
                return await self.gate
        return {"enabled": True, "subscribed": False}

    async def web_push_preferences(self) -> list[str]:
        return ["blocked"]


def test_replaced_web_push_source_drops_a_late_load() -> None:
    async def scenario() -> None:
        old, new = PushSource(blocked=True), PushSource()
        holder: dict[str, Any] = {}

        @ft.component
        def View() -> Any:
            source, replace = ft.use_state(old)
            holder["replace"] = replace
            return WebPushPanel(transport=source)

        app = await render_component(View)
        assert old.gate is not None
        holder["replace"](new)
        await app.settle()
        loaded = new.calls
        if old.gate is not None and not old.gate.done():
            old.gate.set_result({"enabled": True, "subscribed": True})
        assert loaded == 1
        assert app.find("notifications-status").value == OFF_TEXT
        await app.settle()
        assert app.find("notifications-status").value == OFF_TEXT

    run(scenario())


def test_source_bindings_are_stable_until_account_or_transport_changes() -> None:
    async def scenario() -> None:
        owner, replacement = PushSource(), PushSource()
        holder: dict[str, Any] = {}
        seen: list[Any] = []

        @ft.component
        def View() -> Any:
            account, change_account = ft.use_state("first")
            transport, change_transport = ft.use_state(owner)
            tick, redraw = ft.use_state(0)
            holder.update(account=change_account, transport=change_transport, redraw=redraw)
            sources = use_sources(transport, account)
            seen.append(sources)
            return ft.Text(str(tick))

        app = await render_component(View)
        first = seen[-1]
        holder["redraw"](1)
        await app.settle()
        assert seen[-1] is first
        holder["account"]("second")
        await app.settle()
        assert seen[-1] is not first
        second = seen[-1]
        holder["transport"](replacement)
        await app.settle()
        assert seen[-1] is not second
        assert seen[-1].web_push.web_push_state.__self__ is replacement

    run(scenario())


def test_sources_expose_only_their_feature_operations() -> None:
    class Owner:
        base_url = "https://synthetic.example"

        async def updates(self) -> dict[str, Any]:
            return {}

        async def _request(self) -> None:
            raise AssertionError("private request is not a feature operation")

    source = bind_sources(Owner()).update_notice
    assert source is not None
    assert callable(source.updates)
    assert not hasattr(source, "base_url")
    assert not hasattr(source, "_request")


def test_health_and_command_sources_keep_only_their_read_operations() -> None:
    class Owner:
        base_url = "https://synthetic.example"

        async def metrics(self) -> dict[str, Any]:
            return {"status": "ok"}

        async def command_status(self, host: str, command: str) -> dict[str, Any]:
            return {"host": host, "command": command}

        async def submit_command(self, command: dict[str, Any]) -> None:
            raise AssertionError("read sources must not submit")

    async def scenario() -> None:
        owner = Owner()
        sources = bind_sources(owner)
        assert sources.health is not None
        assert await sources.health.metrics() == {"status": "ok"}
        assert await sources.command_recovery.command_status("host", "command") == {
            "host": "host",
            "command": "command",
        }
        assert set(vars(sources.health)) == {"metrics"}
        assert set(vars(sources.command_recovery)) == {"command_status"}
        assert bind_sources(object()).health is None

    run(scenario())
