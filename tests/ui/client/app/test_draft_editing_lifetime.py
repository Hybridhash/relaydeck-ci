"""Native IDs and handlers belong to one eligible editing lifetime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest
from flet.messaging.protocol import MessageAction

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.features.terminal.controller import TerminalController
from relaydeck.client.platform.transport.terminal_stream import (
    TerminalStreamClient as _terminal_stream_factory,
)
from relaydeck.client.ports.transport import TerminalSocket
from tests.support.fakes import FakeCredentialStore
from tests.support.host_harness import render_component
from tests.support.scripted_sessions import make_scripted_sessions
from tests.support.terminal.direct_typing import open_compose
from tests.support.terminal.draft_editors import Boundaries, hide_toggle
from tests.support.ui.retained_root import _active, _render


async def prepared(draft: str) -> tuple[Any, Any, TerminalController]:
    environment = make_scripted_sessions()
    session = environment.factory.make_agent(
        host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
    )
    session._draft_store = FakeCredentialStore()
    await session.prepare_draft()
    session.on_draft_change(draft)

    def socket(_ticket: str) -> TerminalSocket:
        raise AssertionError("No connection expected")

    controller = TerminalController(
        socket, "lease", 1, "control", lambda _: None, stream_factory=_terminal_stream_factory
    )
    session._controller = controller
    return environment, session, controller


async def close(session: Any, controller: TerminalController) -> None:
    session._controller = None
    await controller.close()
    await session.dispose()


@pytest.mark.parametrize("phone", [False, True])
@pytest.mark.parametrize("boundary", ["disabled", "fenced"])
@pytest.mark.parametrize("initial", ["", "kept multiline\ndraft"])
def test_delayed_ineligible_carrier_and_saved_handlers_cannot_edit_after_reenable(
    phone: bool, boundary: str, initial: str
) -> None:
    async def scenario() -> None:
        environment, session, controller = await prepared(initial)
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        first = app.find("composer-draft")
        first_change = first.on_change
        try:
            if boundary == "disabled":
                state["enabled"] = False
            else:
                controller.fence_ui_input(True)
            state["bump"](1)
            await app.settle()
            gated = app.find("composer-draft")
            gated_change = gated.on_change
            assert gated._i != first._i and first._i not in app.session.index
            assert gated.disabled and not gated.can_request_focus
            app.session.apply_patch(gated._i, {"value": "late ineligible text"})
            if boundary == "disabled":
                state["enabled"] = True
            else:
                controller.fence_ui_input(False)
            state["bump"](2)
            await app.settle()
            current = app.find("composer-draft")
            assert current._i not in (first._i, gated._i)
            assert current.value == initial
            for native, handler in ((first, first_change), (gated, gated_change)):
                assert native._i not in app.session.index
                await app.session.dispatch_event(native._i, "change", "late ineligible text")
                await handler(SimpleNamespace(data="late ineligible text", control=native))
            session._draft_preparation.synchronize(session.store)
            assert session.store.draft == initial
            assert session.stage_question("Question?") == ("draft_conflict" if initial else "")
            assert session.store.draft == (initial or "Question?")
            await app.set_props_and_fire(
                "composer-draft", {"value": "new eligible edit"}, "change", "new eligible edit"
            )
            assert session.store.draft == "new eligible edit"
            assert not environment.submitted and not environment.prompted
        finally:
            await close(session, controller)

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
def test_fence_rekey_restores_focus_and_utf16_selection_without_typing_remounts(
    phone: bool,
) -> None:
    async def scenario() -> None:
        _environment, session, controller = await prepared("ab😀cd\nnext")
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        first = app.find("composer-draft")
        try:
            await app.session.dispatch_event(first._i, "focus", "")
            selection = {"base_offset": 1, "extent_offset": 4}
            app.session.apply_patch(first._i, {"selection": selection})
            await app.session.dispatch_event(
                first._i, "selection_change", {"selection": selection, "selected_text": "b😀"}
            )
            controller.fence_ui_input(True)
            state["bump"](1)
            await app.settle()
            gated = app.find("composer-draft")
            assert gated.disabled and not gated.autofocus
            controller.fence_ui_input(False)
            state["bump"](2)
            await app.settle()
            active = app.find("composer-draft")
            assert active._i not in (first._i, gated._i)
            assert active.autofocus
            assert active.selection == ft.TextSelection(1, 4)
            # A retired blur must not clear the replacement's focus intent.
            result = first.on_blur(SimpleNamespace())
            if asyncio.iscoroutine(result):
                await result
            await app.session.dispatch_event(active._i, "focus", "")
            for number, text in enumerate(("ab😀cd\nnext 中", "ab😀cd\nnext 中文"), start=3):
                app.session.apply_patch(active._i, {"value": text})
                state["bump"](number)
                await app.settle(1)
                assert app.find("composer-draft")._i == active._i
                await app.session.dispatch_event(active._i, "change", text)
                assert session.store.draft == text
            assert not any(m.action == MessageAction.INVOKE_METHOD for m in app.conn.sent)
        finally:
            await close(session, controller)

    asyncio.run(scenario())


def test_phone_root_delayed_gate_event_cannot_replace_the_preserved_last_keystroke() -> None:
    async def scenario() -> None:
        app, _controller, environment, pool, _now = await _render(
            bridge=RouteBridge(width=390, height=844), touch=True
        )
        try:
            await app.set_props_and_fire("agent-home-mac:term-1", {}, "click")
            await app.set_props_and_fire("agent-takeover-confirm", {}, "click")
            session = pool.foreground.session
            session.on_draft_change("kept")
            await open_compose(app)
            await app.set_props_and_fire("composer-draft", {}, "focus")
            first = _active(app, "composer-draft")
            writes: list[str] = []
            replace = session.store.replace_draft

            def record(text: str) -> Any:
                writes.append(text)
                return replace(text)

            session.store.replace_draft = record
            app.session.apply_patch(first._i, {"value": "legitimate last keystroke"})
            await app.session.dispatch_event(hide_toggle(app)._i, "click", "")
            await asyncio.sleep(0.02)
            gated = app.find("composer-draft")
            assert gated._i != first._i and gated.disabled
            app.session.apply_patch(gated._i, {"value": "late disabled phone value"})
            await app.settle()
            active = _active(app, "composer-draft")
            assert active._i not in (first._i, gated._i)
            assert active.value == "legitimate last keystroke" and not active.autofocus
            await app.session.dispatch_event(gated._i, "change", "late disabled phone value")
            session._draft_preparation.synchronize(session.store)
            assert session.store.draft == "legitimate last keystroke"
            assert writes.count("legitimate last keystroke") == 1
            assert "late disabled phone value" not in writes
            assert session.stage_question("Question?") == "draft_conflict"
            assert not environment.submitted and not environment.prompted
        finally:
            await app.set_props_and_fire("test-unmount", {}, "click")

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
def test_authoritative_model_replacement_does_not_restore_the_old_text_selection(
    phone: bool,
) -> None:
    async def scenario() -> None:
        _environment, session, controller = await prepared("original text")
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        first = app.find("composer-draft")
        try:
            selection = {"base_offset": 2, "extent_offset": 5}
            app.session.apply_patch(first._i, {"selection": selection})
            await app.session.dispatch_event(
                first._i, "selection_change", {"selection": selection, "selected_text": "igi"}
            )
            session.on_draft_change("Replacement question?")
            state["bump"](1)
            await app.settle()
            current = app.find("composer-draft")
            assert current._i == first._i
            assert current.value == "Replacement question?"
            assert current.selection is None
        finally:
            await close(session, controller)

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
def test_selection_received_inside_an_unrendered_fence_cannot_replace_accepted_caret(
    phone: bool,
) -> None:
    async def scenario() -> None:
        _environment, session, controller = await prepared("ab😀cd\nnext")
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        first = app.find("composer-draft")
        try:
            selection = {"base_offset": 1, "extent_offset": 4}
            app.session.apply_patch(first._i, {"selection": selection})
            await app.session.dispatch_event(
                first._i, "selection_change", {"selection": selection, "selected_text": "b😀"}
            )
            controller.fence_ui_input(True)
            app.session.apply_patch(first._i, {"selection": {"base_offset": 0, "extent_offset": 0}})
            controller.fence_ui_input(False)
            state["bump"](1)
            await app.settle()
            current = app.find("composer-draft")
            assert current._i != first._i
            assert current.selection == ft.TextSelection(1, 4)
        finally:
            await close(session, controller)

    asyncio.run(scenario())
