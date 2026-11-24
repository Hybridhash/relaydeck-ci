"""Native property/event ordering across editing gates and keyboard dismissal."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

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
from tests.support.terminal.draft_editors import Boundaries, DraftView, hide_toggle
from tests.support.ui.retained_root import _active, _render


@pytest.mark.parametrize("phone", [False, True])
@pytest.mark.parametrize("boundary", ["disabled", "fenced"])
@pytest.mark.parametrize("draft", ["", "kept multiline\ndraft"])
def test_reenable_never_promotes_rejected_native_values(
    phone: bool, boundary: str, draft: str
) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        session = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        session.on_draft_change(draft)
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        native_id = app.find("composer-draft")._i
        owner = session._draft_preparation.owner
        assert owner is not None
        generation = owner.generation
        if boundary == "disabled":
            state["enabled"] = False
        else:
            session._controller = SimpleNamespace(ui_input_fenced=True)
        state["bump"](1)
        await app.settle()
        try:
            assert not owner.eligible and owner.generation > generation
            gated_id = app.find("composer-draft")._i
            assert gated_id != native_id and native_id not in app.session.index
            app.session.apply_patch(gated_id, {"value": "late rejected edit"})
            await app.session.dispatch_event(gated_id, "change", "late rejected edit")
            assert session.store.draft == draft
            state["enabled"] = True
            session._controller = None
            state["bump"](2)
            await app.settle()
            assert app.find("composer-draft")._i not in (native_id, gated_id)
            assert gated_id not in app.session.index
            assert app.find("composer-draft").value == draft
            session._draft_preparation.synchronize(session.store)
            assert session.store.draft == draft
            assert session.stage_question("Question?") == ("draft_conflict" if draft else "")
            assert session.store.draft == (draft if draft else "Question?")
            await app.set_props_and_fire(
                "composer-draft", {"value": "new eligible edit"}, "change", "new eligible edit"
            )
            assert session.store.draft == "new eligible edit"
            assert not environment.submitted and not environment.prompted
        finally:
            session._controller = None
            await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
def test_prop_only_disable_preserves_one_pending_eligible_edit(phone: bool) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        session = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        session.on_draft_change("kept")
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        native_id = app.find("composer-draft")._i
        app.session.apply_patch(native_id, {"value": "pending eligible keystroke"})
        state["enabled"] = False
        state["bump"](1)
        await app.settle()
        try:
            assert session.store.draft == "pending eligible keystroke"
            gated_id = app.find("composer-draft")._i
            assert gated_id != native_id
            app.session.apply_patch(gated_id, {"value": "later disabled edit"})
            await app.session.dispatch_event(gated_id, "change", "later disabled edit")
            state["enabled"] = True
            state["bump"](2)
            await app.settle()
            session._draft_preparation.synchronize(session.store)
            assert (
                session.store.draft
                == app.find("composer-draft").value
                == "pending eligible keystroke"
            )
            assert session.stage_question("Question?") == "draft_conflict"
        finally:
            await session.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [False, True])
def test_desktop_keyboard_pulse_does_not_recapture_a_disabled_patch(pending: bool) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        session = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()
        session.on_draft_change("kept")
        app = await render_component(DraftView, session)
        field = app.find("composer-draft")
        hide = hide_toggle(app)
        expected = "pending before keyboard hide" if pending else "kept"
        try:
            if pending:
                app.session.apply_patch(field._i, {"value": expected})
            await app.session.dispatch_event(hide._i, "click", "")
            await asyncio.sleep(0.01)
            assert app.find("composer-draft").disabled
            gated_id = app.find("composer-draft")._i
            assert gated_id != field._i
            app.session.apply_patch(gated_id, {"value": "late while disabled"})
            await app.session.dispatch_event(gated_id, "change", "late while disabled")
            await app.settle()
            session._draft_preparation.synchronize(session.store)
            assert session.store.draft == app.find("composer-draft").value == expected
        finally:
            await session.dispose()

    asyncio.run(scenario())


def test_phone_keyboard_hide_preserves_pending_edit_then_rejects_later_patches() -> None:
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
            native_id = _active(app, "composer-draft")._i
            app.session.apply_patch(native_id, {"value": "legitimate last keystroke"})
            await app.session.dispatch_event(hide_toggle(app)._i, "click", "")
            await asyncio.sleep(0.02)
            assert app.find("composer-draft").disabled
            assert session.store.draft == "legitimate last keystroke"
            gated_id = app.find("composer-draft")._i
            assert gated_id != native_id
            await app.session.dispatch_event(gated_id, "change", "legitimate last keystroke")
            app.session.apply_patch(gated_id, {"value": "late disabled patch"})
            await app.session.dispatch_event(gated_id, "change", "late disabled patch")
            await app.settle()
            session._draft_preparation.synchronize(session.store)
            assert (
                session.store.draft
                == app.find("composer-draft").value
                == "legitimate last keystroke"
            )
            assert session.stage_question("Question?") == "draft_conflict"
            assert not environment.submitted and not environment.prompted
        finally:
            await app.set_props_and_fire("test-unmount", {}, "click")

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
def test_fence_cycle_without_a_render_cannot_promote_native_text(phone: bool) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        session = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
        )
        session._draft_store = FakeCredentialStore()
        await session.prepare_draft()

        def socket(_ticket: str) -> TerminalSocket:
            raise AssertionError("No connection expected")

        controller = TerminalController(
            socket, "lease", 1, "control", lambda _: None, stream_factory=_terminal_stream_factory
        )
        session._controller = controller
        state: dict[str, Any] = {"enabled": True}
        app = await render_component(Boundaries, session, phone, state)
        native_id = app.find("composer-draft")._i
        try:
            controller.fence_ui_input(True)
            app.session.apply_patch(native_id, {"value": "received during fence"})
            controller.fence_ui_input(False)
            # Even if both fence transitions precede the next render, Ask may
            # not promote the raw property received in that interval.
            assert session.stage_question("Question?") == ""
            assert session.store.draft == "Question?"
            state["bump"](1)
            await app.settle()
            assert app.find("composer-draft").value == "Question?"
            await app.set_props_and_fire(
                "composer-draft",
                {"value": "eligible after fence"},
                "change",
                "eligible after fence",
            )
            assert session.store.draft == "eligible after fence"
        finally:
            session._controller = None
            await controller.close()
            await session.dispose()

    asyncio.run(scenario())
