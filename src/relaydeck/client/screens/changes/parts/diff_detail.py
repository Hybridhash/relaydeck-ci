"""Compose one selected file with its read position, actions and diff renderer."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.features.changes.api import ReadingActions
from relaydeck.client.screens.changes.parts.commit_failure import BUSY_TEXT, GONE_TEXT, kind
from relaydeck.client.screens.changes.parts.diff_view import DiffFileView, state_text
from relaydeck.contracts.git import FileEntry


def diff_detail(
    model: m.ChangesModel,
    actions: ReadingActions,
    entry: FileEntry,
    rows: Sequence[Any],
    seen: Callable[[FileEntry], bool],
    toggle_viewed: Callable[[FileEntry], None] | None,
    *,
    on_copy: Callable[[], None] | None,
    back: bool,
    on_ask: Callable[[], None] | None = None,
    ask_reason: str = "",
    select_hunk: bool = False,
    recovery: ft.Control | None = None,
) -> ft.Control:
    pages = m.resident_pages(model)
    window = model.diff
    # A commit's retained comparison can expire under the reader: what is on screen stays, a dead
    # cursor is not offered again, and the graph can be read again.
    failed = model.scope == "commit" and model.diff_load.status == "error"
    gone = failed and kind(model.diff_load.error) == "gone"
    message = state_text(pages[-1] if pages and not rows else None)
    if model.diff_load.status == "loading" and not rows:
        message = "Loading…"
    elif model.diff_load.error and not rows:
        message = _failure_text(model)
    notice: ft.Control | None = None
    if failed and rows:
        notice = ft.Column(
            key="commit-notice",
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(_failure_text(model), color=t.TEXT_SECONDARY, size=13),
                *([recovery] if gone and recovery is not None else []),
            ],
        )
    leading = (
        ft.IconButton(
            ft.Icons.CHEVRON_LEFT,
            key="changes-files-back",
            tooltip="Back to the files",
            icon_size=24,
            icon_color=t.TEXT,
            width=t.MIN_TOUCH,
            height=t.MIN_TOUCH,
            on_click=lambda _: actions.select_file(None),
        )
        if back
        else None
    )
    header_actions: list[ft.Control] = []
    if toggle_viewed is not None:  # a commit being read has no Viewed marks
        header_actions.append(
            ft.Checkbox(
                key="changes-viewed",
                label="Viewed",
                value=seen(entry),
                on_change=lambda _: toggle_viewed(entry),
            )
        )
    if on_ask is not None:
        header_actions.append(
            Button(
                "Ask",
                variant="text",
                key="diff-ask",
                height=t.MIN_TOUCH,
                disabled=bool(ask_reason),
                tooltip=ask_reason,
                on_click=lambda _: on_ask(),
            )
        )
    if on_copy is not None:
        header_actions.append(
            ft.IconButton(
                ft.Icons.CONTENT_COPY,
                key="diff-copy",
                tooltip="Copy lines or a hunk",
                icon_size=18,
                icon_color=t.TEXT,
                width=t.MIN_TOUCH,
                height=t.MIN_TOUCH,
                on_click=lambda _: on_copy(),
            )
        )
    name = entry.path.rsplit("/", 1)[-1]
    folder = entry.path[: len(entry.path) - len(name)]
    counts = (
        (entry.additions, entry.deletions)
        if entry.additions is not None and entry.deletions is not None
        else None
    )
    anchor = m.anchor_for(model, entry.file_id)
    file_id = entry.file_id
    return ft.Container(
        expand=True,
        content=DiffFileView(  # type: ignore[call-arg]
            name,
            folder,
            rows,
            key=f"diff-{file_id}",
            message=message,
            has_more=window is not None and not window.complete and bool(pages) and not gone,
            on_more=actions.more_diff,
            has_earlier=m.earlier_page(model) is not None and not gone,
            notice=notice,
            message_extra=[recovery] if gone and not rows and recovery is not None else [],
            on_earlier=actions.earlier_diff,
            on_load=actions.load_diff if pages and pages[-1].render == "load_required" else None,
            header_actions=header_actions,
            on_hunk=actions.select_hunk if select_hunk else None,
            selected_hunk=model.selected_hunk,
            leading=leading,
            counts=counts,
            anchor=None
            if anchor is None
            else (anchor.page, anchor.hunk, anchor.fragment, anchor.row),
            on_place=lambda row: actions.remember(
                m.Anchor(file_id, row.page, row.hunk, row.fragment, row.line)
            ),
        ),
    )


def _failure_text(model: m.ChangesModel) -> str:
    if model.scope == "commit":
        return {"gone": GONE_TEXT, "busy": BUSY_TEXT, "other": "Could not load this file."}[
            kind(model.diff_load.error)
        ]
    return (
        "This file changed. Refresh to see the latest."
        if model.updated_available
        else "Could not load this file."
    )
