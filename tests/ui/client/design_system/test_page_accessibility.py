"""Native accessibility flags reach rendered motion controls once per page."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.state.accessibility import use_page_accessibility
from relaydeck.client.design_system.theme import palette
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.terminal.connecting_dots import ConnectingDots
from relaydeck.client.screens.terminal.screen import mount_agent_screen
from relaydeck.client.widgets.usage.cards import console_loading
from tests.support.host_harness import FakeConnection, render_component, run
from tests.support.terminal.dispatch import _agent_session


@pytest.mark.parametrize("flag", ["reduce_motion", "disable_animations"])
def test_native_flags_are_read_once_across_resource_subscribers(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    calls: list[Any] = []

    async def read(service: Any) -> Any:
        calls.append(service)
        return SimpleNamespace(**{flag: True, "bold_text": True})

    monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

    async def scenario() -> None:
        holder: dict[str, Any] = {}

        @ft.component
        def Probe() -> Any:
            snapshot = use_page_accessibility()
            return ft.Column(
                [
                    ft.Text(str(snapshot.motion_reduced), key="motion-value"),
                    console_loading(
                        tokens=usage_palette(True), reduce_motion=snapshot.motion_reduced
                    ),
                    ConnectingDots(palette(True), True, reduce_motion=snapshot.motion_reduced),
                ]
            )

        @ft.component
        def View() -> Any:
            account, change = ft.use_state("first")
            visible, show = ft.use_state(True)
            holder.update(change=change, show=show)
            return Probe(key=account) if visible else ft.Container()

        app = await render_component(View)
        assert app.find("motion-value").value == "True"
        assert len(calls) == 1
        dot = app.find("terminal-connecting-dot-0")
        assert dot.animate_offset is None and dot.on_animation_end is None
        assert dot.offset.y == 0
        holder["change"]("second")
        await app.settle()
        assert app.find("motion-value").value == "True"
        assert len(calls) == 1
        holder["show"](False)
        await app.settle()
        owner = app.page._relaydeck_accessibility_owner
        assert owner._listeners == {} and owner._task is None
        other = await render_component(Probe)
        assert other.find("motion-value").value == "True"
        assert len(calls) == 2 and calls[0] is not calls[1]

    run(scenario())


async def _replace_connection(app: Any) -> None:
    connection = FakeConnection()
    connection.loop, connection.executor, connection.pubsubhub = (
        app.conn.loop,
        app.conn.executor,
        app.conn.pubsubhub,
    )
    await app.session.connect(connection)
    app.conn = connection


@pytest.mark.parametrize("order", ["result-first", "remount-first"])
@pytest.mark.parametrize("flag", ["reduce_motion", "disable_animations"])
def test_connection_replacement_recovers_an_unfinished_motion_read(
    monkeypatch: pytest.MonkeyPatch, order: str, flag: str
) -> None:
    async def scenario() -> None:
        gate = asyncio.get_running_loop().create_future()
        calls: list[Any] = []
        holder: dict[str, Any] = {}

        async def read(service: Any) -> Any:
            calls.append(service)
            if len(calls) == 1:
                try:
                    await asyncio.shield(gate)
                except asyncio.CancelledError:
                    await gate
                return SimpleNamespace(**{flag: False})
            return SimpleNamespace(**{flag: True})

        monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

        @ft.component
        def Probe() -> Any:
            snapshot = use_page_accessibility()
            return ft.Column(
                [
                    ft.Text(str(snapshot.motion_reduced), key="motion-value"),
                    ConnectingDots(palette(True), True, reduce_motion=snapshot.motion_reduced),
                ]
            )

        @ft.component
        def View() -> Any:
            key, change = ft.use_state(0)
            holder["change"] = change
            return Probe(key=str(key))

        app = await render_component(View)
        old = app.page._relaydeck_accessibility_owner
        assert len(calls) == 1 and app.find("motion-value").value == "False"
        try:
            await _replace_connection(app)
            if order == "result-first":
                gate.set_result(None)
                await app.settle()
            holder["change"](1)
            await app.settle()
            assert app.find("motion-value").value == "True" and len(calls) == 2
            if order == "remount-first":
                gate.set_result(None)
                await app.settle()
            current = app.page._relaydeck_accessibility_owner
            assert current is not old and old._closed and not current._closed
            assert current._current() and app.find("motion-value").value == "True"
            dot = app.find("terminal-connecting-dot-0")
            assert dot.animate_offset is None and dot.on_animation_end is None
        finally:
            if not gate.done():
                gate.set_result(None)
            app.page._relaydeck_accessibility_owner.close()
            await app.settle()

    run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_disconnect_remount_keeps_completed_motion_and_recovers_an_unfinished_read(
    monkeypatch: pytest.MonkeyPatch, completed: bool
) -> None:
    async def scenario() -> None:
        gate = asyncio.get_running_loop().create_future()
        calls: list[Any] = []
        holder: dict[str, Any] = {}

        async def read(service: Any) -> Any:
            calls.append(service)
            if len(calls) == 1 and not completed:
                try:
                    await asyncio.shield(gate)
                except asyncio.CancelledError:
                    await gate
                return SimpleNamespace(reduce_motion=False)
            return SimpleNamespace(reduce_motion=True)

        monkeypatch.setattr(ft.SemanticsService, "get_accessibility_features", read)

        @ft.component
        def Probe() -> Any:
            snapshot = use_page_accessibility()
            return ft.Text(str(snapshot.motion_reduced), key="motion-value")

        @ft.component
        def View() -> Any:
            visible, show = ft.use_state(True)
            holder["show"] = show
            return Probe() if visible else ft.Container()

        app = await render_component(View)
        old = app.page._relaydeck_accessibility_owner
        assert app.find("motion-value").value == str(completed) and len(calls) == 1
        try:
            await app.session.disconnect(0)
            await _replace_connection(app)
            holder["show"](False)
            await app.settle()
            assert not old._listeners
            holder["show"](True)
            await app.settle()
            assert app.find("motion-value").value == "True"
            current = app.page._relaydeck_accessibility_owner
            assert current is not old and old._closed and not current._closed and current._current()
            assert len(current._listeners) == 1 and len(calls) == (1 if completed else 2)
            if not gate.done():
                gate.set_result(None)
                await app.settle()
            assert app.find("motion-value").value == "True"
        finally:
            if not gate.done():
                gate.set_result(None)
            app.page._relaydeck_accessibility_owner.close()
            await app.settle()

    run(scenario())


@pytest.mark.parametrize("reduced", [False, True])
@pytest.mark.parametrize("dark", [False, True])
def test_terminal_slot_motion_changes_without_retiring_the_editor(
    reduced: bool, dark: bool
) -> None:
    async def scenario() -> None:
        session, _, _ = _agent_session()
        holder: dict[str, Any] = {}
        session.open()
        await session.confirm_takeover()

        @ft.component
        def View() -> Any:
            motion, change = ft.use_state(reduced)
            holder["change"] = change
            return mount_agent_screen(
                session,
                dark=dark,
                reduce_motion=motion,
                viewport_width=1376,
                width_tier="expanded",
                typing_split=True,
                touch=True,
            )

        app = await render_component(View)
        slot = app.find("agent-composer-slot")
        original_id = slot._i
        assert (slot.animate_size is None) is reduced
        assert (slot.animate_position is None) is reduced
        holder["change"](not reduced)
        await app.settle()
        slot = app.find("agent-composer-slot")
        assert slot._i == original_id
        assert (slot.animate_size is None) is not reduced
        assert (slot.animate_position is None) is not reduced
        await session.dispose()

    run(scenario())
