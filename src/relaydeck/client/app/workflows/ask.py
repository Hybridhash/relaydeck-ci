"""Root-owned route/identity observer for contextual questions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import flet as ft

from relaydeck.client.app.workflows.command_context import recipient, refresh
from relaydeck.client.app.workflows.page_tasks import PageTasks, connection_for
from relaydeck.client.features.ask_agent.handoff import AskHandoff, Identity, PendingAsk
from relaydeck.client.features.ask_agent.model import AskScope, build_prompt
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.models.routing import CHANGES_SECTIONS
from relaydeck.client.screens.changes.screen import ask_strip
from relaydeck.domain.models import unscope_key


class AskRuntime:
    def __init__(self, changed: Callable[[], None]) -> None:
        self.handoff = AskHandoff()
        self.changed = changed
        self.identity: tuple[str, Any] | None = None
        self.current: dict[str, Any] = {}
        self.return_active = False
        self.active = True
        self.driving = False
        self._queued = False
        self._rerun = False
        self._rerun_replace = False
        self._wake_pending = False
        self._schedule: Callable[[Callable[[], Coroutine[Any, Any, Any]]], None] | None = None

    def enqueue(self, **kwargs: Any) -> None:
        self.wake(replace=bool(kwargs.get("replace", False)))

    def wake(self, *, replace: bool = False) -> None:
        """Synchronous wakeup for render effects: at most one drive is scheduled
        or running; further wakes coalesce into one rerun after it ends."""
        if self.driving or self._queued:
            self._rerun = True
            self._rerun_replace = self._rerun_replace or replace
            return
        if self._schedule is None:
            self._wake_pending = True
            self._rerun_replace = self._rerun_replace or replace
            return
        self._queued = True
        self._schedule(lambda: self._drive_loop(replace))

    async def _drive_loop(self, replace: bool = False) -> None:
        self._queued = False
        if self.driving:
            self._rerun = True
            self._rerun_replace = self._rerun_replace or replace
            return
        self.driving = True
        try:
            while True:
                try:
                    await self.drive(replace=replace)
                except BaseException:
                    self._rerun = False
                    self._rerun_replace = False
                    raise
                if not self._rerun:
                    break
                replace = self._rerun_replace
                self._rerun = False
                self._rerun_replace = False
        finally:
            self.driving = False

    def gate(self, key: str) -> tuple[Identity, str, str]:
        c = self.current
        epoch, status, reason = recipient(self.state(), key)
        return Identity(c["account"], c["transport"], key, epoch), status, reason

    def state(self) -> Any:
        read = self.current.get("read_state")
        return read() if read is not None else self.current["state"]

    def route(self) -> tuple[str, str, str]:
        c = self.current
        read = c.get("read_route")
        if read is None:
            return c["destination"], c["selection"], c["section"]
        route = read()
        return route.destination, route.selection, route.section

    def page_live(self) -> bool:
        current = self.current.get("page_live")
        return bool(current()) if current is not None else True

    def close(self) -> None:
        self.active = False
        self.handoff.clear()

    def start(
        self,
        scope: AskScope,
        question: str,
        bookmark: ReadingBookmark,
        title: str,
        *,
        recipient_key: str | None = None,
        identity: Identity | None = None,
    ) -> bool:
        c = self.current
        destination, key, section = self.route()
        live_identity, _status, reason = self.gate(key)
        if (
            not self.active
            or not self.page_live()
            or identity is None
            or not identity.matches(live_identity)
            or recipient_key is not None
            and recipient_key != key
            or reason
            or destination != "agents"
            or section not in CHANGES_SECTIONS
        ):
            return False
        try:
            prompt = build_prompt(scope, question)
        except ValueError:
            return False
        if not self.handoff.begin(PendingAsk(identity, scope, prompt, bookmark, title)):
            return False
        self.changed()
        c["open_terminal"](identity.key)
        return True

    async def drive(self, *, replace: bool = False) -> None:
        if not self.active or not self.page_live():
            self.handoff.clear()
            return
        request = self.handoff.pending
        c = self.current
        if request is None:
            returned = self.handoff.returned
            on_return = (
                returned is not None
                and c["destination"] == "agents"
                and c["section"] in CHANGES_SECTIONS
                and c["selection"] == returned[0]
            )
            if self.return_active and not on_return:
                self.handoff.returned = None
            self.return_active = on_return
            return
        destination, key, section = self.route()
        identity, _status, reason = self.gate(key)
        terminal = destination == "agents" and not section
        changes = destination == "agents" and section in CHANGES_SECTIONS
        before = (request.state, request.reason)
        proceed = self.handoff.observe(identity, terminal=terminal, changes=changes)
        if (
            self.handoff.pending is request
            and request.state in ("pending_navigation", "waiting_for_readiness", "draft_conflict")
            and reason
        ):
            request.state, request.reason = "refused", reason
            proceed = False
        if proceed or replace and request.state == "draft_conflict":
            entry = c["entry"]
            if reason:
                request.state, request.reason = "refused", reason
            elif not c["foreground"]:
                self.handoff.pending = None
            elif entry is not None:
                session = entry.session
                split = unscope_key(identity.key)
                if (
                    entry.kind != "agent"
                    or entry.key != identity.key
                    or entry.epoch != identity.epoch
                    or split != (session.host_id, session.agent_id)
                    or session.disposed
                    or session.paused
                    or not session.foreground
                ):
                    self.handoff.pending = None
                else:
                    refresh(session, self.state(), identity.key)
                    try:
                        await self.handoff.drain(session, replace=replace)
                    finally:
                        # Repaint on every outcome, but only for this request:
                        # a replacement that arrived mid-send is never touched.
                        if self.active and self.handoff.pending is request:
                            if before != (request.state, request.reason):
                                self.changed()
                            before = (request.state, request.reason)
        if request.state == "staged":
            entry = c["entry"]
            if (
                entry is not None
                and entry.key == request.identity.key
                and len(entry.session.store.messages) > request.staged_count
            ):
                request.state = "settled"
        after = self.handoff.pending
        if self.active and (after is not request or before != (request.state, request.reason)):
            self.changed()

    def terminal(self) -> None:
        self.current["open_terminal"](self.current["selection"])

    def dismiss(self) -> None:
        self.handoff.pending = None
        self.changed()

    def back(self) -> None:
        result = self.handoff.back()
        if result is not None:
            self.changed()
            self.current["navigate"]("agents", result[0], section=result[1].section())

    def replace(self) -> None:
        self.enqueue(replace=True)

    def strip(self, wide: bool) -> Any:
        request = self.handoff.pending
        c = self.current
        if (
            request is None
            or c["destination"] != "agents"
            or c["section"]
            or c["selection"] != request.identity.key
        ):
            return None
        return ask_strip(
            request,
            wide=wide,
            on_back=self.back,
            on_dismiss=self.dismiss,
            on_replace=self.replace,
        )

    def bookmark(self, key: str) -> ReadingBookmark | None:
        returned = self.handoff.returned
        return returned[1] if returned is not None and returned[0] == key else None


def use_ask_runtime(**current: Any) -> AskRuntime:
    _tick, set_tick = ft.use_state(0)
    runtime_ref: Any = ft.use_ref(None)
    page = ft.context.page
    if runtime_ref.current is None:

        def changed() -> None:
            if connection_for(page) is not None:
                set_tick(lambda n: n + 1)

        runtime_ref.current = AskRuntime(changed)
    runtime: AskRuntime = runtime_ref.current
    identity = current["account"], current["transport"]
    if runtime.identity is not None and (
        identity[0] != runtime.identity[0] or identity[1] is not runtime.identity[1]
    ):
        runtime.handoff.clear()
    runtime.identity = identity
    current["page_live"] = lambda: connection_for(page) is not None
    runtime.current = current
    entry = current["entry"]
    session = entry.session if entry is not None and entry.kind == "agent" else None
    owner: Any = ft.use_ref(None)
    connection = connection_for(page)

    def subscribe() -> None:
        tasks = PageTasks(page, runtime.handoff.clear)
        owner.current = tasks
        runtime._schedule = tasks.run
        if session is not None:
            tasks.watch(session, runtime._drive_loop)
        if runtime._wake_pending:
            runtime._wake_pending = False
            runtime.wake(replace=runtime._rerun_replace)
            runtime._rerun_replace = False

    def cleanup() -> None:
        if owner.current is not None:
            if not owner.current.live():
                runtime.handoff.clear()
            owner.current.close()
            owner.current = None
            runtime._schedule = None

    ft.use_effect(subscribe, dependencies=[session, connection], cleanup=cleanup)
    # Dependency-less but synchronous: renders only wake the single-flight
    # drive on the page task owner, so no render can cancel a send.
    ft.use_effect(runtime.wake)
    ft.use_effect(lambda: None, dependencies=[], cleanup=runtime.close)
    return runtime
