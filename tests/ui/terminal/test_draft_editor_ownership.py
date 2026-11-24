"""A mounted draft editor releases authority on unmount or session replacement."""

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from tests.support.fakes import FakeCredentialStore
from tests.support.host_harness import render_component
from tests.support.scripted_sessions import make_scripted_sessions
from tests.support.terminal.draft_editors import DraftView


@ft.component
def Editors(first: Any, second: Any, phone: bool = False) -> Any:
    switched, switch = ft.use_state(False)
    shown, show = ft.use_state(True)
    session = second if switched else first
    return ft.Column(
        controls=[
            ft.Button("Switch", key="switch-editor", on_click=lambda _: switch(True)),
            ft.Button("Hide", key="hide-editor", on_click=lambda _: show(False)),
            DraftView(session, phone) if shown else ft.Text("Hidden"),
        ]
    )


@pytest.mark.parametrize("boundary", ["unmount", "replace"])
def test_editor_cleanup_rejects_old_native_state_and_keeps_new_editor_live(boundary: str) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        first = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="first", target="first"
        )
        second = environment.factory.make_agent(
            host_id="host", host_epoch="epoch", agent_id="second", target="second"
        )
        for session, text in ((first, "first kept draft"), (second, "second kept draft")):
            session._draft_store = FakeCredentialStore()
            await session.prepare_draft()
            session.on_draft_change(text)
        app = await render_component(Editors, first, second)
        field = app.find("composer-draft")
        late_change = field.on_change
        owner = first._draft_preparation.owner
        assert owner is not None and owner.active
        stale = first.composer_callbacks()["on_draft_change"]
        try:
            await app.set_props_and_fire(
                "hide-editor" if boundary == "unmount" else "switch-editor", {}, "click"
            )
            assert not owner.active and first._draft_preparation.native is None
            if boundary == "unmount":
                app.session.apply_patch(field._i, {"value": "late edit"})
                await app.session.dispatch_event(field._i, "change", "late edit")
            stale.remember_field(field, owner)
            stale("late edit")
            await late_change(SimpleNamespace(data="late edit", control=field))
            first._draft_preparation.synchronize(first.store)
            first._bump_epoch()
            assert first.store.draft == "first kept draft"
            if boundary == "replace":
                await app.set_props_and_fire(
                    "composer-draft", {"value": "new editor change"}, "change", "new editor change"
                )
                assert second.store.draft == "new editor change"
            assert not environment.submitted and not environment.prompted
        finally:
            await first.dispose()
            await second.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phone", [False, True])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("hide_first", [False, True])
def test_session_replacement_isolates_native_ids_and_pending_text(
    phone: bool, pending: bool, hide_first: bool
) -> None:
    async def scenario() -> None:
        environment = make_scripted_sessions()
        sessions = [
            environment.factory.make_agent(
                host_id="host", host_epoch="epoch", agent_id=name, target=name
            )
            for name in ("first", "second")
        ]
        first, second = sessions
        for session, draft in zip(sessions, ("first draft", "second draft"), strict=True):
            session._draft_store = FakeCredentialStore()
            await session.prepare_draft()
            session.on_draft_change("" if pending else draft)
        app = await render_component(Editors, first, second, phone)
        old_id = app.find("composer-draft")._i
        if pending:
            app.session.apply_patch(old_id, {"value": "private pending first draft"})
        if hide_first:
            first.set_foreground(False)
        try:
            await app.set_props_and_fire("switch-editor", {}, "click")
            current = app.find("composer-draft")
            assert current._i != old_id
            assert current.value == second.store.draft == ("" if pending else "second draft")
            app.session.apply_patch(old_id, {"value": "late first-session edit"})
            await app.session.dispatch_event(old_id, "change", "late first-session edit")
            second._draft_preparation.synchronize(second.store)
            assert second.store.draft == ("" if pending else "second draft")
            assert first.store.draft == (
                "private pending first draft"
                if pending and hide_first
                else ""
                if pending
                else "first draft"
            )
            result = second.stage_question("Question for second?")
            assert result == ("" if pending else "draft_conflict")
            assert second.store.draft == ("Question for second?" if pending else "second draft")
            await app.set_props_and_fire(
                "composer-draft", {"value": "current editor edit"}, "change", "current editor edit"
            )
            assert second.store.draft == "current editor edit"
            assert not environment.submitted and not environment.prompted
        finally:
            for session in sessions:
                await session.dispose()

    asyncio.run(scenario())
