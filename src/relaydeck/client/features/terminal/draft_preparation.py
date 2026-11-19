"""Positive preparation barrier and synchronous native-draft reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from relaydeck.client.features.composer.api import (
    DraftFieldOwner,
    draft_key,
    install,
    resume_restore,
    unresolved,
)


@dataclass
class DraftPreparation:
    generation: int = 0
    completed: bool = False
    ready: bool = False
    reason: str = "Draft preparation has not completed."
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    native: Any = field(default=None, repr=False)
    rendered: str = field(default="", repr=False)
    owner: DraftFieldOwner | None = field(default=None, repr=False)
    current: Callable[[], bool] | None = field(default=None, repr=False)

    def remember(
        self, field_: Any, draft: str, owner: DraftFieldOwner, current: Callable[[], bool]
    ) -> None:
        self.native, self.rendered = field_, draft
        self.owner, self.current = owner, current

    def retire(self, owner: DraftFieldOwner | None = None) -> None:
        if owner is None or self.owner is owner:
            self.current = None
            if self.owner is not None:
                self.owner.invalidate()

    def fence(self, store: Any) -> None:
        # Preserve one pending active edit before fencing; later lifecycle
        # transitions cannot recapture a hidden field's raw value.
        self.synchronize(store)
        self.retire()

    def forget(self, owner: DraftFieldOwner | None = None) -> None:
        retiring = self.owner if owner is None else owner
        if retiring is not None:
            retiring.active = False
        if owner is None or self.owner is owner:
            self.native, self.rendered = None, ""
            self.owner, self.current = None, None

    def owns(self, owner: DraftFieldOwner, field_: Any) -> bool:
        return (
            owner.active
            and owner.eligible
            and self.owner is owner
            and self.native is field_
            and self.current is not None
            and self.current()
            and editable(field_)
        )

    def synchronize(self, store: Any) -> None:
        if (
            self.native is not None
            and self.owner is not None
            and self.owns(self.owner, self.native)
            and store.draft == self.rendered
        ):
            value = self.native.value
            if isinstance(value, str) and value != self.rendered:
                store.replace_draft(value)


def editable(field_: Any) -> bool:
    return (
        getattr(field_, "can_request_focus", False) is True
        and not getattr(field_, "disabled", False)
        and getattr(field_, "visible", True) is not False
    )


def edit_callback(session: Any, live: Callable[[], bool], epoch: int) -> Callable[[str], None]:
    state = session._draft_preparation
    native: Any = None
    owner: DraftFieldOwner | None = None
    default_owner = DraftFieldOwner()

    def current() -> bool:
        return live() and not session.ui_input_fenced and session._sessions.modes.text_edits_draft

    def field_generation() -> tuple[int, int, int]:
        controller = session._controller
        return (
            session._composer_epoch,
            id(controller),
            getattr(controller, "ui_input_generation", 0),
        )

    def change(text: str) -> None:
        if current() and owner is not None and state.owns(owner, native):
            session.on_draft_change(text)

    def remember(field_: Any, editor_owner: DraftFieldOwner | None = None) -> None:
        nonlocal native, owner
        native, owner = field_, default_owner if editor_owner is None else editor_owner
        if not owner.active:
            return
        if current() and owner.eligible and editable(field_):
            generation = field_generation()
            state.remember(
                field_,
                session.store.draft,
                owner,
                lambda: current() and field_generation() == generation,
            )
        elif epoch == session._composer_epoch:
            if state.owner is owner and state.owns(owner, state.native):
                state.fence(session.store)
            else:
                state.retire(owner)

    def fence(editor_owner: DraftFieldOwner) -> str | None:
        if state.owner is editor_owner and state.owns(editor_owner, state.native):
            state.fence(session.store)
            return str(session.store.draft)
        return None

    cast(Any, change).remember_field = remember
    cast(Any, change).forget_field = state.forget
    cast(Any, change).field_context = session
    cast(Any, change).field_live = current
    cast(Any, change).field_generation = field_generation
    cast(Any, change).fence_field = fence
    cast(Any, change).retire_field = state.retire
    return change


async def prepare(session: Any) -> None:
    state = session._draft_preparation
    if state.completed:
        return
    if state.task is not None:
        await asyncio.shield(state.task)
        return

    async def load() -> None:
        state.generation += 1
        session.draft_loading = True
        session._notify()
        before = session.store.export_draft()
        try:
            if session._draft_store is None:
                state.reason = "Secure draft storage is unavailable."
                return
            report = await resume_restore(
                session._draft_store, draft_key(session.host_id + ":" + session.agent_id)
            )
            if session.disposed or session._auth_revoked:
                return
            session._last_resume_restore = report
            if report.capability_error is not None:
                state.reason = "Stored draft could not be verified."
                session._notice = state.reason
                return
            if session.store.export_draft() == before and report.snapshot is not None:
                try:
                    session.store.import_draft(report.snapshot)
                except ValueError:
                    state.reason = "Stored draft could not be verified."
                    session._notice = state.reason
                    return
            state.ready, state.reason = True, ""
        finally:
            state.completed = True
            session.draft_loading = False
            session._notify()

    state.task = asyncio.create_task(load())
    await asyncio.shield(state.task)


def readiness(session: Any) -> str:
    state = session._draft_preparation
    if session.disposed or session.paused or not session.foreground or session._auth_revoked:
        return "This terminal is not active."
    if session.store.agent_status not in ("idle", "done", "working"):
        return "The recipient is unavailable or blocked."
    if session.phase == "exited" or session.ui_input_fenced:
        return "Input is unavailable."
    if not state.ready:
        return str(state.reason)
    if not session._sessions.submit_allowed():
        return "Finish the current text composition first."
    return ""


def stage(session: Any, text: str, *, replace: bool = False) -> str:
    reason = readiness(session)
    if reason:
        return reason
    session._draft_preparation.synchronize(session.store)
    session.set_workflow("agent-composer")
    if unresolved(session.store):
        return "An earlier send or review is unresolved."
    if not install(session.store, text, replace=replace):
        return "draft_conflict"
    session._notify()
    return ""


def clear(session: Any) -> None:
    state = session._draft_preparation
    state.generation += 1
    if state.task is not None and not state.task.done():
        state.task.cancel()
    state.forget()
    state.ready = False
    session._last_resume_restore = None
    session._last_pause_persist = None
