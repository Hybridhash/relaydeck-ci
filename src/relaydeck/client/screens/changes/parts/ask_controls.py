"""Small screen-local question state; transport and intent survive in the root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from relaydeck.client.features.ask_agent.api import (
    DEFAULT_QUESTION,
    AskScope,
    Identity,
    build_prompt,
    eligibility,
    target,
    validate,
)
from relaydeck.client.features.changes.api import ReadingActions, ReadingBookmark
from relaydeck.client.screens.changes.parts.ask_sheet import AskSheet


@dataclass
class AskControls:
    open: Callable[..., None]
    reason: Callable[..., str]
    panel: Any
    active: bool


def use_ask_controls(
    actions: ReadingActions,
    *,
    title: str,
    status: str,
    unavailable: str,
    wide: bool,
    on_ask: Callable[[AskScope, str, ReadingBookmark, str], bool] | None,
    identity: Identity | None,
    close_other: Callable[[], None],
    bookmark: Callable[[], ReadingBookmark],
    context: Callable[[], object] | None = None,
) -> AskControls:
    initial: AskScope | None = None
    scope, set_scope = ft.use_state(initial)
    question, set_question = ft.use_state(DEFAULT_QUESTION)
    latch: Any = ft.use_ref(False)
    opened_identity: Any = ft.use_ref(None)
    opened_send: Any = ft.use_ref(None)
    opened_context: Any = ft.use_ref(None)

    def current_context() -> object:
        return context() if context is not None else None

    def same_identity() -> bool:
        old = opened_identity.current
        return (
            old.matches(identity) if old is not None and identity is not None else old is identity
        )

    def reason(kind: str = "branch", **place: Any) -> str:
        try:
            target(actions.current(), kind, **place)
        except ValueError as exc:
            return str(exc)
        return (
            unavailable
            or eligibility(status)
            or ("Questions are unavailable." if on_ask is None else "")
        )

    def open_question(kind: str = "branch", **place: Any) -> None:
        if reason(kind, **place):
            return
        frozen = target(actions.current(), kind, **place)
        close_other()
        if frozen.kind == "hunk" and frozen.page is not None and frozen.hunk is not None:
            actions.select_hunk((frozen.page, frozen.hunk))
        latch.current = False
        opened_identity.current = identity
        opened_send.current = on_ask
        opened_context.current = current_context()
        set_question(DEFAULT_QUESTION)
        set_scope(frozen)

    def send(latest_question: str) -> None:
        if (
            scope is None
            or latch.current
            or opened_send.current is None
            or not same_identity()
            or opened_context.current != current_context()
        ):
            return
        if validate(actions.current(), scope) or reason(
            scope.kind, page=scope.page, hunk=scope.hunk, row=scope.row
        ):
            return
        set_question(latest_question)
        try:
            build_prompt(scope, latest_question)
        except ValueError:
            return
        latch.current = True
        if opened_send.current(scope, latest_question, bookmark(), title):
            set_scope(None)
        else:
            latch.current = False

    panel = None
    if scope is not None:
        error = "The recipient changed. Open the question again." if not same_identity() else ""
        if opened_context.current != current_context():
            error = "The review context changed. Open the question again."
        error = error or validate(actions.current(), scope) or unavailable or eligibility(status)
        try:
            build_prompt(scope, question)
        except ValueError as exc:
            error = error or str(exc)
        panel = AskSheet(
            scope,
            question,
            title,
            status=status,
            reason=error,
            older=actions.current().updated_available,
            on_question=set_question,
            on_send=send,
            on_close=lambda: set_scope(None),
            wide=wide,
        )
    return AskControls(open_question, reason, panel, scope is not None)


def question_overlay(content: ft.Control, panel: ft.Control | None, *, wide: bool) -> ft.Control:
    """Keep the reader mounted beneath one modal question panel."""
    return ft.Stack(
        expand=True,
        controls=[
            ft.Container(
                key="changes-reader",
                left=0,
                right=0,
                top=0,
                bottom=0,
                disabled=panel is not None,
                content=content,
            ),
            ft.Container(
                key="changes-question-overlay",
                top=0,
                alignment=ft.Alignment.BOTTOM_RIGHT,
                left=None if wide else 0,
                right=0,
                bottom=0,
                visible=panel is not None,
                content=panel,
            ),
        ],
    )
