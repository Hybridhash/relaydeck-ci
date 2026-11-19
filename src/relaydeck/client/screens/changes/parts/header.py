"""The Changes header (branch, base, totals, tabs) and its bottom action bar."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.features.changes.api import ChangesActions
from relaydeck.client.screens.changes.parts.tabs import changes_tabs as _tabs
from relaydeck.contracts.git import WorktreeSummary

_MINUTE, _HOUR, _DAY = 60, 3600, 86400
_JUST_NOW = 5


def age_text(then_ms: int | None, now_ms: int) -> str:
    """ "12 s ago", "3 min ago"; a clock a little ahead of the Mac's reads as "just now"."""
    if then_ms is None:
        return "just now"
    seconds = max(0, (now_ms - then_ms) // 1000)
    if seconds < _JUST_NOW:
        return "just now"
    if seconds < _MINUTE:
        return f"{seconds} s ago"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE} min ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR} h ago"
    return f"{seconds // _DAY} d ago"


def changes_header(
    model: m.ChangesModel,
    actions: ChangesActions,
    context_line: str,
    *,
    summary: WorktreeSummary | None,
    on_back: Callable[[], None],
    on_copy: Callable[[str], None] | None,
    now_ms: int,
    refresh_failed: bool = False,
    compact: bool = False,
    newest_first: bool = False,
    checked: Callable[[], int | None] | None = None,
) -> ft.Control:
    """Back and copy, who and where, branch ← base, totals with a share bar, and the tabs.

    On a short, wide screen (`compact`) the back button, title, branch ← base and tabs share one
    row, and the graph's summary moves into the graph's own band."""
    branch = model.branch or (model.short_oid or "")
    if compact:
        return _compact_header(model, actions, summary, on_back, now_ms, refresh_failed)
    copy_label = "Copy branch name" if model.branch else "Copy commit"
    top: list[ft.Control] = [
        ft.Container(
            key="changes-back",
            height=t.MIN_TOUCH,
            padding=ft.Padding.only(right=12),
            alignment=ft.Alignment.CENTER_LEFT,
            on_click=lambda _: on_back(),
            tooltip="Back to Console",
            content=ft.Row(
                tight=True,
                spacing=2,
                controls=[
                    ft.Icon(ft.Icons.CHEVRON_LEFT, size=22, color=t.TEXT),
                    ft.Text("Console", size=15, weight=ft.FontWeight.W_700, color=t.TEXT),
                ],
            ),
        ),
        ft.Container(expand=True),
    ]
    if on_copy is not None and branch:
        top.append(
            ft.IconButton(
                ft.Icons.CONTENT_COPY,
                key="changes-copy-branch",
                tooltip=copy_label,
                icon_size=18,
                icon_color=t.TEXT,
                width=t.MIN_TOUCH,
                height=t.MIN_TOUCH,
                style=ft.ButtonStyle(
                    bgcolor=t.CARD, side=ft.BorderSide(1, t.BORDER), shape=ft.CircleBorder()
                ),
                on_click=lambda _: on_copy(branch),
            )
        )
    controls: list[ft.Control] = [
        ft.Row(spacing=0, controls=top),
        ft.Text(context_line, color=t.TEXT_MUTED, size=13, max_lines=1),
        ScreenHeader("Changes", size=26, weight=ft.FontWeight.W_800, color=t.TEXT),
        _reference(model, actions, branch, summary, now_ms, checked=checked),
    ]
    if model.condition == "ok" and model.snapshot is not None:
        controls.append(
            graph_summary(model, now_ms, newest_first=newest_first)
            if model.view == "graph"
            else _totals(model)
        )
    controls.append(_tabs(model, actions, summary))
    if refresh_failed and model.snapshot is not None:
        controls.append(
            ft.Text(
                "Could not check for updates. Trying again.",
                key="changes-stale",
                size=12,
                color=t.PRESENCE_WARN,
            )
        )
    if model.updated_available:
        controls.append(updated_pill(actions))
    return ft.Container(
        key="changes-header",
        padding=ft.Padding.only(left=t.PAD_PHONE, right=t.PAD_PHONE, top=4, bottom=10),
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        content=ft.Column(spacing=8, tight=True, controls=controls),
    )


COMPACT_HEADER = 62  # dp: the row of back, title, branch ← base and tabs, with its padding
ACTION_BAR = 72  # dp: the bottom bar's 48 dp buttons with their padding


def _back_button(on_back: Callable[[], None]) -> ft.Control:
    return ft.Container(
        key="changes-back",
        height=t.MIN_TOUCH,
        padding=ft.Padding.only(right=8),
        alignment=ft.Alignment.CENTER_LEFT,
        on_click=lambda _: on_back(),
        tooltip="Back to Console",
        content=ft.Row(
            tight=True,
            spacing=2,
            controls=[
                ft.Icon(ft.Icons.CHEVRON_LEFT, size=20, color=t.TEXT),
                ft.Text("Console", size=14, weight=ft.FontWeight.W_700, color=t.TEXT),
            ],
        ),
    )


def _compact_header(
    model: m.ChangesModel,
    actions: ChangesActions,
    summary: WorktreeSummary | None,
    on_back: Callable[[], None],
    now_ms: int,
    refresh_failed: bool,
) -> ft.Control:
    branch = model.branch or (model.short_oid or "")
    row = ft.Row(
        key="changes-header-row",
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            _back_button(on_back),
            ft.Text("Changes", size=18, weight=ft.FontWeight.W_800, color=t.TEXT),
            ft.Container(
                expand=True,
                content=_reference(model, actions, branch, summary, now_ms, with_notes=False),
            ),
            _tabs(model, actions, summary, compact=True),
        ],
    )
    controls: list[ft.Control] = [row]
    if model.condition == "ok" and model.snapshot is not None and model.view != "graph":
        controls.append(_totals(model))
    if refresh_failed and model.snapshot is not None:
        controls.append(
            ft.Text(
                "Could not check for updates. Trying again.",
                key="changes-stale",
                size=12,
                color=t.PRESENCE_WARN,
            )
        )
    if model.updated_available:
        controls.append(updated_pill(actions))
    return ft.Container(
        key="changes-header",
        padding=ft.Padding.only(left=t.PAD_PHONE, right=t.PAD_PHONE, top=2, bottom=4),
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        content=ft.Column(spacing=4, tight=True, controls=controls),
    )


def updated_pill(actions: ChangesActions) -> ft.Control:
    """Newer content is ready; nothing moves until the reader asks for it."""
    return ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            Button(
                "Updated · tap to refresh",
                variant="filled",
                key="changes-updated",
                icon=ft.Icons.ARROW_UPWARD,
                height=t.MIN_TOUCH,
                style=ft.ButtonStyle(
                    bgcolor=t.PRIMARY_FILL, color=t.ON_PRIMARY, shape=ft.StadiumBorder()
                ),
                on_click=lambda _: actions.refresh(),
            )
        ],
    )


def _action_label(text: str, color: str, weight: ft.FontWeight) -> ft.Text:
    return ft.Text(
        text,
        size=14,
        weight=weight,
        color=color,
        max_lines=1,
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
    )


def action_bar(
    model: m.ChangesModel,
    on_terminal: Callable[[], None],
    on_copy: Callable[[str], None] | None,
    *,
    on_ask: Callable[[], None] | None = None,
    ask_reason: str = "",
    ask_title: str = "",
) -> ft.Control:
    """The fixed bottom bar: the agent's terminal, a question, and copying the branch.

    Terminal and Copy are as wide as their labels and icons need, so nothing clips at any phone
    width; Ask takes what is left and cuts a long label with an ellipsis.
    """
    branch = model.branch or (model.short_oid or "")
    inset = ft.Padding.symmetric(horizontal=14)
    shape = ft.RoundedRectangleBorder(radius=12)
    buttons: list[ft.Control] = [
        ft.OutlinedButton(
            content=ft.Row(
                tight=True,
                spacing=6,
                controls=[
                    ft.Icon(ft.Icons.TERMINAL, size=18, color=t.TEXT),
                    _action_label("Terminal", t.TEXT, ft.FontWeight.W_700),
                ],
            ),
            key="changes-terminal",
            height=t.MIN_TOUCH,
            style=ft.ButtonStyle(
                bgcolor=t.RAISED, side=ft.BorderSide(1, t.BORDER), shape=shape, padding=inset
            ),
            on_click=lambda _: on_terminal(),
        )
    ]
    if on_ask is not None:
        buttons.append(
            ft.OutlinedButton(
                content=_action_label(
                    f"Ask {ask_title}" if ask_title else "Ask", t.TEXT, ft.FontWeight.W_700
                ),
                key="changes-ask",
                expand=True,
                height=t.MIN_TOUCH,
                disabled=bool(ask_reason),
                tooltip=ask_reason or "Ask about these changes",
                style=ft.ButtonStyle(shape=shape, padding=inset),
                on_click=lambda _: on_ask(),
            )
        )
    if on_copy is not None and branch:
        buttons.append(
            Button(
                variant="filled",
                content=ft.Row(
                    tight=True,
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.ALT_ROUTE, size=18, color=t.ON_PRIMARY),
                        _action_label(
                            "Copy branch" if model.branch else "Copy commit",
                            t.ON_PRIMARY,
                            ft.FontWeight.W_800,
                        ),
                    ],
                ),
                key="changes-copy-action",
                height=t.MIN_TOUCH,
                style=ft.ButtonStyle(bgcolor=t.PRIMARY_FILL, shape=shape, padding=inset),
                on_click=lambda _: on_copy(branch),
            )
        )
    return ft.Container(
        key="changes-actions",
        bgcolor=t.NAV,
        border=ft.Border(top=ft.BorderSide(1, t.BORDER)),
        padding=ft.Padding.only(left=t.PAD_PHONE, right=t.PAD_PHONE, top=10, bottom=14),
        content=ft.Row(spacing=10, controls=buttons),
    )


def _chip(text: str, *, key: str, accent: bool) -> ft.Control:
    return Badge(
        key=key,
        height=26,
        padding=ft.Padding.symmetric(horizontal=8),
        border_radius=6,
        bgcolor=t.CARD_SELECTED if accent else t.RAISED,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            text,
            font_family=FAMILY_MONO,
            size=12.5,
            color=t.PRIMARY_TEXT_LIGHT if accent else t.TEXT_SECONDARY,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )


def _reference(
    model: m.ChangesModel,
    actions: ChangesActions,
    branch: str,
    summary: WorktreeSummary | None,
    now_ms: int,
    *,
    with_notes: bool = True,
    checked: Callable[[], int | None] | None = None,
) -> ft.Control:
    """branch ← base · N commits ahead · as of …; the base chip opens the base picker."""
    parts: list[ft.Control] = []
    if branch:
        parts.append(_chip(branch, key="changes-reference", accent=True))
    base = model.base_name or (summary.base_name if summary is not None else None)
    if base:
        parts.append(ft.Icon(ft.Icons.WEST, size=14, color=t.TEXT_MUTED))
        parts.append(
            ft.Container(
                key="changes-change-base",
                height=t.MIN_TOUCH,
                alignment=ft.Alignment.CENTER_LEFT,
                tooltip="Change the base branch",
                on_click=lambda _: actions.open_bases(),
                content=_chip(f"{base} ▾", key="changes-base", accent=False),
            )
        )
    chips = ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=parts,
        scroll=ft.ScrollMode.HIDDEN,
    )
    if not with_notes:
        return chips
    # The graph's summary line says all of this once, below.
    ahead = summary.branch_scope.commits if summary is not None and model.view != "graph" else None
    lead = f"{ahead} commit{'s' if ahead != 1 else ''} ahead" if ahead else ""
    aged = model.snapshot is not None and model.as_of_ms is not None and model.view != "graph"
    if not lead and not aged:
        return chips
    return ft.Column(
        spacing=6,
        tight=True,
        controls=[
            chips,
            CheckedNote(
                lead=lead,
                taken_ms=model.as_of_ms if aged else None,
                # A later check counts only while the list is still what the check found.
                checked=None if model.updated_available else checked,
            ),
        ],
    )


NOTE_TICK_S = 10.0  # the note's age is refreshed this often, by itself


@ft.component
def CheckedNote(lead: str, taken_ms: int | None, checked: Callable[[], int | None] | None) -> Any:
    """ "N commits ahead · as of 12 s ago". The age follows the newest check and its own clock, so
    a check that found nothing new, or the passing of time, updates this text and nothing else."""
    _tick, set_tick = ft.use_state(0)
    timer: Any = ft.use_ref(None)

    def start() -> None:
        loop = asyncio.get_running_loop()

        def again() -> None:
            set_tick(lambda value: value + 1)
            timer.current = loop.call_later(NOTE_TICK_S, again)

        timer.current = loop.call_later(NOTE_TICK_S, again)

    def stop() -> None:
        if timer.current is not None:
            timer.current.cancel()
            timer.current = None

    ft.use_effect(start, dependencies=[], cleanup=stop)
    notes = [lead] if lead else []
    if taken_ms is not None:
        newest = max(taken_ms, (checked() if checked is not None else None) or 0)
        notes.append(f"as of {age_text(newest, int(time.time() * 1000))}")
    return ft.Text(" · ".join(notes), key="changes-asof", size=12, color=t.TEXT_MUTED)


def graph_summary_spans(
    model: m.ChangesModel, now_ms: int, newest_first: bool = False
) -> list[ft.TextSpan]:
    """The summary with the base name in violet and the split's oid in mono."""
    page = model.graph.page
    if page is None:
        return [ft.TextSpan("Loading graph…")]
    noun = "commit" if page.ahead == 1 else "commits"
    spans = [
        ft.TextSpan(f"{page.ahead} {noun} ahead of "),
        ft.TextSpan(page.base_name, ft.TextStyle(color=t.PRIMARY_TEXT)),
    ]
    if page.behind:
        spans.append(ft.TextSpan(f" · {page.behind} behind"))
    spans.append(ft.TextSpan(" · split at "))
    spans.append(ft.TextSpan(page.split_oid[:7], ft.TextStyle(font_family=FAMILY_MONO)))
    spans.append(ft.TextSpan(f" · as of {age_text(page.as_of_ms, now_ms)}"))
    if newest_first:
        spans.append(ft.TextSpan(" · newest first"))
    return spans


def graph_summary(
    model: m.ChangesModel, now_ms: int, *, newest_first: bool = False, inline: bool = False
) -> ft.Text:
    """The summary line; `inline` sits in the chips' band, on one line that gives way to them."""
    return ft.Text(
        spans=graph_summary_spans(model, now_ms, newest_first),
        key="graph-summary",
        size=12,
        color=t.TEXT_MUTED,
        max_lines=1 if inline else 2,
        no_wrap=inline,
        overflow=ft.TextOverflow.ELLIPSIS if inline else ft.TextOverflow.CLIP,
        expand=inline,
    )


def _totals(model: m.ChangesModel) -> ft.Control:
    """ "7 files  +1,248  −312" with a green and red share bar."""
    added, removed = model.additions or 0, model.deletions or 0
    total = added + removed
    count = len(model.files) if model.files_next is None else None
    files = f"{count} file{'s' if count != 1 else ''}" if count is not None else "Files"
    bar: list[ft.Control] = (
        [
            ft.Container(expand=max(1, round(100 * added / total)), bgcolor=t.DIFF_ADD_SIGN),
            ft.Container(expand=max(1, round(100 * removed / total)), bgcolor=t.DIFF_DELETE_SIGN),
        ]
        if total
        else [ft.Container(expand=1, bgcolor=t.BORDER)]
    )
    return ft.Container(
        key="changes-totals",
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        border_radius=14,
        bgcolor=t.CARD,
        border=ft.Border.all(1, t.BORDER),
        content=ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(files, size=14, weight=ft.FontWeight.W_700, color=t.TEXT),
                ft.Text(
                    f"+{added:,}",
                    font_family=FAMILY_MONO,
                    size=14,
                    weight=ft.FontWeight.W_700,
                    color=t.DIFF_ADD_SIGN,
                ),
                ft.Text(
                    f"−{removed:,}",
                    font_family=FAMILY_MONO,
                    size=14,
                    weight=ft.FontWeight.W_700,
                    color=t.DIFF_DELETE_SIGN,
                ),
                ft.Container(
                    expand=True,
                    height=8,
                    border_radius=4,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    content=ft.Row(spacing=0, controls=bar),
                ),
            ],
        ),
    )
