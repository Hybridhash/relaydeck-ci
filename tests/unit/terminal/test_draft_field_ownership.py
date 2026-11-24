"""Native draft reconciliation requires a current, editable field owner."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from relaydeck.client.features.composer.draft_owner import DraftFieldOwner
from relaydeck.client.models.input.modes import WORKFLOW_AGENT_DIRECT
from tests.support.fakes import FakeCredentialStore
from tests.support.scripted_sessions import make_scripted_sessions


def native(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, can_request_focus=True, disabled=False, visible=True)


async def prepared() -> tuple[Any, Any]:
    environment = make_scripted_sessions()
    session = environment.factory.make_agent(
        host_id="host", host_epoch="epoch", agent_id="terminal", target="terminal"
    )
    session._draft_store = FakeCredentialStore()
    await session.prepare_draft()
    session.on_draft_change("kept draft")
    return session, environment


@pytest.mark.parametrize(
    "boundary", ["hidden", "direct", "disabled", "unfocusable", "invisible", "unmounted", "fenced"]
)
def test_inactive_field_cannot_reconcile_or_edit_kept_draft(boundary: str) -> None:
    async def scenario() -> None:
        session, environment = await prepared()
        field = native("kept draft")
        owner = DraftFieldOwner()
        callback = session.composer_callbacks()["on_draft_change"]
        callback.remember_field(field, owner)
        if boundary == "hidden":
            session.set_foreground(False)
        elif boundary == "direct":
            session.set_workflow(WORKFLOW_AGENT_DIRECT)
        elif boundary == "unmounted":
            callback.forget_field(owner)
            assert session._draft_preparation.native is None
        elif boundary == "fenced":
            session._controller = SimpleNamespace(ui_input_fenced=True)
        elif boundary == "disabled":
            field.disabled = True
        elif boundary == "unfocusable":
            field.can_request_focus = False
        else:
            field.visible = False
        try:
            field.value = "late edit"
            callback("late edit")
            callback.remember_field(field, owner)
            session._draft_preparation.synchronize(session.store)
            session._bump_epoch()
            assert session.store.draft == "kept draft"
            assert not environment.submitted and not environment.prompted
        finally:
            if boundary == "fenced":
                session._controller = None
            await session.dispose()

    asyncio.run(scenario())


def test_pending_active_native_edit_is_preserved_once_before_hiding() -> None:
    async def scenario() -> None:
        session, _ = await prepared()
        field = native("kept draft")
        callback = session.composer_callbacks()["on_draft_change"]
        callback.remember_field(field)
        field.value = "pending native draft"
        session.set_foreground(False)
        assert session.store.draft == "pending native draft"
        field.value = "late hidden edit"
        session._bump_epoch()
        session.set_foreground(True)
        assert session.stage_question("Question?") == "draft_conflict"
        assert session.store.draft == "pending native draft"
        await session.dispose()

    asyncio.run(scenario())


def test_stale_registration_and_cleanup_cannot_replace_a_new_owner() -> None:
    async def scenario() -> None:
        session, _ = await prepared()
        stale = session.composer_callbacks()["on_draft_change"]
        stale_owner = DraftFieldOwner()
        stale.remember_field(native("kept draft"), stale_owner)
        session._bump_composer_epoch()
        current = session.composer_callbacks()["on_draft_change"]
        current_owner = DraftFieldOwner()
        field = native("kept draft")
        current.remember_field(field, current_owner)
        stale.remember_field(native("late edit"), stale_owner)
        stale.forget_field(stale_owner)
        field.value = "current native draft"
        assert session.stage_question("Question?") == "draft_conflict"
        assert session.store.draft == "current native draft"
        await session.dispose()

    asyncio.run(scenario())


def test_noneditable_registration_reconciles_its_owned_predecessor_once() -> None:
    async def scenario() -> None:
        session, _ = await prepared()
        callback = session.composer_callbacks()["on_draft_change"]
        field = native("kept draft")
        callback.remember_field(field)
        field.value = "pending before prop-only gate"
        disabled = native("kept draft")
        disabled.disabled = True
        callback.remember_field(disabled)
        assert session.store.draft == "pending before prop-only gate"
        disabled.value = "late disabled edit"
        callback("late disabled edit")
        callback.remember_field(disabled)
        session._draft_preparation.synchronize(session.store)
        assert session.store.draft == "pending before prop-only gate"
        await session.dispose()

    asyncio.run(scenario())
