"""Changes for one agent: scope switch, file list or commits, and the selected file's diff."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.features.ask_agent.api import AskScope, Identity
from relaydeck.client.features.changes.api import (
    ChangesSource,
    ReadingBookmark,
    ReviewStateStore,
    ViewContext,
    ViewKey,
)
from relaydeck.client.screens.changes.controller import use_changes
from relaydeck.client.screens.changes.parts.ask_controls import question_overlay, use_ask_controls
from relaydeck.client.screens.changes.parts.ask_strip import ask_strip as ask_strip
from relaydeck.client.screens.changes.parts.commit_route import FILE, level_of, use_commit_route
from relaydeck.client.screens.changes.parts.commit_view import (
    CommitReader,
    commit_hint,
    commit_lost,
)
from relaydeck.client.screens.changes.parts.copy_sheet import copy_sheet
from relaydeck.client.screens.changes.parts.diff_detail import diff_detail
from relaydeck.client.screens.changes.parts.diff_view import display_rows
from relaydeck.client.screens.changes.parts.file_list import file_ref
from relaydeck.client.screens.changes.parts.graph_split import COMMIT_FLEX, GRAPH_FLEX, graph_split
from relaydeck.client.screens.changes.parts.header import action_bar, changes_header, updated_pill
from relaydeck.client.screens.changes.parts.pickers import base_picker, file_pager, file_picker
from relaydeck.client.screens.changes.parts.review_keys import use_review_keys
from relaydeck.client.screens.changes.parts.screen_list import _hint
from relaydeck.client.screens.changes.parts.screen_list import _list_area as _list_area
from relaydeck.contracts.git import FileEntry, WorktreeSummary

COMPACT_BELOW = 500.0  # dp of height under which the screen uses the one-row header


@ft.component
def ChangesScreen(
    host_id: str,
    agent_id: str,
    worktree_id: str | None,
    agent_title: str,
    source: ChangesSource,
    *,
    summary: WorktreeSummary | None = None,
    refresh_failed: bool = False,
    checked: Callable[[], int | None] | None = None,
    on_recheck: Callable[[], None] | None = None,
    reviews: ReviewStateStore | None = None,
    server: str = "",
    account: str = "",
    wide: bool = False,
    phone: bool = True,
    foreground: bool = True,
    section: str = "changes",
    step: int = 0,
    on_level: Callable[[str, bool, int], None] | None = None,
    on_back: Callable[[], None],
    on_terminal: Callable[[], None],
    on_copy: Callable[[str], None] | None = None,
    context_line: str = "",
    ask_status: str = "unknown",
    ask_identity: Identity | None = None,
    ask_unavailable: str = "",
    on_ask: Callable[[AskScope, str, ReadingBookmark, str], bool] | None = None,
    bookmark: ReadingBookmark | None = None,
) -> ft.Control:
    model, actions = use_changes(
        source,
        host_id,
        agent_id,
        worktree_id,
        summary,
        bookmark,
        foreground,
        graph_first=section in ("commit", "commit-file"),
    )
    local_reviews: Any = ft.use_ref(None)
    if local_reviews.current is None:
        local_reviews.current = ReviewStateStore()
    store: ReviewStateStore = reviews if reviews is not None else local_reviews.current
    _tick, set_tick = ft.use_state(0)
    query, set_query = ft.use_state(bookmark.query if bookmark else "")
    picker_query, set_picker_query = ft.use_state("")
    generated_open, set_generated_open = ft.use_state(bookmark.generated if bookmark else False)
    folded, set_folded = ft.use_state(bookmark.folded if bookmark else frozenset[str]())

    def toggle_dir(path: str) -> None:
        # From the current set, not this render's: sibling folds between renders must all hold.
        set_folded(lambda now: now - {path} if path in now else now | {path})

    sheet, set_sheet = ft.use_state("")  # "", "copy" or "picker"

    reading = model.commit
    # A commit open in the Graph tab has its own files, diff and question; the list keeps its own.
    rmodel = reading.model if reading is not None else model
    ractions = (actions.commit or actions) if reading is not None else actions
    commit_key = reading.opening if reading is not None else None
    size, set_size = ft.use_state((0.0, 0.0))  # what the shell really gives this screen, in dp
    width, surface_height = size
    # A short, wide screen (a phone on its side) gets the one-row header.
    compact = 0 < surface_height < COMPACT_BELOW and width > surface_height
    split = graph_split(width)
    side_by_side = wide and split.side_by_side
    pushed = reading is not None and not side_by_side
    # A phone shows a file of the list instead of the list.
    file_pushed = (
        not wide
        and model.view != "graph"
        and (model.selected_file is not None or m.reselecting(model))
    )
    level = level_of(model, pushed, file_pushed)

    def step_to(target: str) -> None:
        # The browser went back: to this level (close the commit, or just its open file), or out.
        if target == "commit":
            ractions.select_file(None)
        elif target == "changes" and level == FILE:
            actions.select_file(None)
        elif target == "changes":
            actions.close_commit()
        else:
            on_back()

    use_commit_route(
        level=level,
        section=section,
        step=step,
        restoring=bool(bookmark is not None and bookmark.commit is not None),
        on_level=on_level,
        step_to=step_to,
    )

    def measure(event: Any) -> None:
        if abs(event.width - width) >= 2 or abs(event.height - surface_height) >= 2:
            set_size((event.width, event.height))

    def place() -> tuple[object, ...]:
        current = actions.current()
        return current.view, current.graph.mode, current.commit.opening if current.commit else None

    asking = use_ask_controls(
        ractions,
        context=place,
        title=agent_title,
        status=ask_status,
        unavailable=ask_unavailable,
        wide=wide,
        on_ask=on_ask,
        identity=ask_identity,
        close_other=lambda: set_sheet(""),
        bookmark=lambda: ReadingBookmark.capture(actions.current(), query, generated_open, folded),
    )
    context = None
    if model.worktree_id is not None:
        context = ViewContext(
            server, account, host_id, model.worktree_id, model.scope, model.base_revision
        )
    snapshot = model.snapshot or ""

    def view_key(entry: FileEntry) -> ViewKey | None:
        return None if context is None else context.key(file_ref(entry), snapshot)

    def seen(entry: FileEntry) -> bool:
        key = view_key(entry)
        return key is not None and store.is_viewed(key)

    def toggle_viewed(entry: FileEntry) -> None:
        key = view_key(entry)
        if key is not None:
            store.toggle(key)
            set_tick(lambda value: value + 1)

    def forget_stale() -> None:
        # Marks of an older snapshot of this list can never match again; the whole list must be
        # known before a mark on a file that has not been paged in yet is called stale.
        if context is not None and model.snapshot is not None and model.files_next is None:
            store.retain(context, {file_ref(f): snapshot for f in model.files})

    ft.use_effect(forget_stale, dependencies=[model.snapshot, model.files_next is None])

    def close_copy_sheet() -> None:
        # A Copy sheet is drawn for one file in one snapshot; it does not outlive either.
        if sheet == "copy":
            set_sheet("")

    ft.use_effect(
        close_copy_sheet, dependencies=[rmodel.selected_file, rmodel.snapshot, commit_key]
    )

    def pick(file_id: str) -> None:
        set_sheet("")
        ractions.select_file(file_id)

    # Only a diff that offers hunk selection asks about its selected hunk; a single-pane diff asks
    # about the whole file, while a selection kept from a wider layout stays for the way back.
    hunk_selectable = side_by_side if reading is not None else wide

    def chosen_hunk() -> tuple[int, int] | None:
        return rmodel.selected_hunk if hunk_selectable else None

    def ask_diff() -> None:
        """Ask about the hunk selected in the diff, else the file."""
        place = chosen_hunk()
        if place:
            asking.open("hunk", page=place[0], hunk=place[1])
        else:
            asking.open("file")

    def ask_diff_reason() -> str:
        place = chosen_hunk()
        if place:
            return str(asking.reason("hunk", page=place[0], hunk=place[1]))
        return str(asking.reason("file"))

    open_entry = next((f for f in model.files if f.file_id == model.selected_file), None)
    keys = use_review_keys(
        wide=wide,
        blocked=bool(sheet)
        or asking.active
        or model.picker_open
        or (model.view == "graph" and reading is None),
        on_next=lambda: ractions.step_file(1),
        on_previous=lambda: ractions.step_file(-1),
        on_viewed=lambda: toggle_viewed(open_entry)
        if open_entry is not None and reading is None
        else None,
    )

    def listing() -> ft.Control:
        """The list or graph. Built only when it is on screen: a diff or a pushed commit that
        hides it must not pay for every row."""
        return _list_area(
            model,
            actions,
            phone=phone,
            wide=wide,
            query=query,
            on_query=set_query,
            generated_open=generated_open,
            on_toggle_generated=lambda: set_generated_open(lambda now: not now),
            compact=compact,
            folded=folded if wide else None,
            on_toggle_dir=toggle_dir,
            on_recheck=on_recheck,
            on_search_focus=keys.focus,
            seen=seen,
            now_ms=int(time.time() * 1000),
        )

    # Ordinary detail shows beside the list when wide, or instead of it for a
    # selected/reselecting phone file; the graph never uses it.
    show_detail = model.view != "graph" and (
        wide or model.selected_file is not None or m.reselecting(model)
    )
    detail: ft.Control = _hint("Choose a file to see its changes.")
    if show_detail and m.reselecting(model):
        # the file is further down a long list; pages are still arriving
        detail = _hint(
            "Finding your file…",
            [PillButton("Files", lambda: actions.select_file(None), key="changes-files-back")],
        )
    elif show_detail and model.selected_file is not None:
        window = model.diff
        first_page = window.pages[0][0] if window is not None and window.pages else 0
        rows = display_rows(m.resident_pages(model), first_page)
        entry = next((f for f in model.files if f.file_id == model.selected_file), None)
        detail = (
            diff_detail(
                model,
                actions,
                entry,
                rows,
                seen,
                toggle_viewed,
                on_copy=(lambda: set_sheet("copy")) if on_copy is not None and rows else None,
                back=not wide,
                on_ask=ask_diff,
                ask_reason=ask_diff_reason(),
                select_hunk=wide,
            )
            if entry is not None
            else _hint(
                "Loading…" if model.snapshot is None else "This file is no longer in the list."
            )
        )
    commit_pane: ft.Control | None = None
    if reading is not None:
        commit_pane = CommitReader(
            model,
            reading,
            actions,
            ractions,
            wide=side_by_side,
            now_ms=int(time.time() * 1000),
            on_back=actions.close_commit,
            on_ask=lambda: asking.open("commit"),
            ask_reason=asking.reason("commit"),
            on_copy=on_copy,
            on_lines=(lambda: set_sheet("copy")) if on_copy is not None else None,
            on_pick=lambda: set_sheet("picker"),
            on_ask_diff=ask_diff,
            ask_diff_reason=ask_diff_reason(),
        )
    if model.view == "graph" and side_by_side:
        measured = split.graph is not None
        body: ft.Control = ft.Row(
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Container(
                    key="graph-pane",
                    width=split.graph,
                    expand=None if measured else GRAPH_FLEX,
                    bgcolor=t.PANEL,
                    content=ft.Column(spacing=0, controls=[listing()]),
                ),
                ft.VerticalDivider(width=1, color=t.BORDER),
                ft.Container(
                    key="commit-slot",
                    expand=True if measured else COMMIT_FLEX,
                    content=ft.Column(
                        spacing=0,
                        controls=[
                            commit_pane
                            or (
                                commit_lost(actions, pane=True)
                                if model.commit_lost
                                else commit_hint()
                            )
                        ],
                    ),
                ),
            ],
        )
    elif model.view == "graph" and commit_pane is None and model.commit_lost:
        body = ft.Column(
            expand=True, spacing=0, controls=[commit_lost(actions, pane=False), listing()]
        )
    elif model.view == "graph":
        body = commit_pane or listing()
    elif wide:
        body = ft.Row(
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.Container(
                    width=t.LIST_WIDTH,
                    bgcolor=t.PANEL,
                    content=ft.Column(spacing=0, controls=[listing()]),
                ),
                ft.VerticalDivider(width=1, color=t.BORDER),
                detail,
            ],
        )
    elif model.selected_file or m.reselecting(model):
        body = ft.Column(
            expand=True,
            spacing=0,
            controls=[detail, file_pager(model, actions, lambda: set_sheet("picker"))]
            if model.selected_file
            else [detail],
        )
    else:
        body = listing()
    focused = pushed or (not wide and bool(model.selected_file or m.reselecting(model)))
    if not focused:  # focused hides only the header; the detail above still renders
        controls: list[ft.Control] = [
            changes_header(
                model,
                actions,
                context_line or agent_title,
                summary=summary,
                on_back=on_back,
                on_copy=on_copy,
                now_ms=int(time.time() * 1000),
                refresh_failed=refresh_failed,
                checked=checked,
                compact=compact,
                newest_first=not phone,
            ),
            body,
        ]
    elif model.updated_available and reading is None:
        controls = [
            ft.Container(padding=ft.Padding.symmetric(vertical=6), content=updated_pill(actions)),
            body,
        ]
    else:
        controls = [body]
    if not model.picker_open and not pushed and (wide or not focused):
        controls.append(
            action_bar(
                model,
                on_terminal,
                on_copy,
                on_ask=None if reading is not None else asking.open,
                ask_reason=asking.reason(),
                ask_title=agent_title if wide else "",
            )
        )
    if sheet == "copy" and on_copy is not None:
        controls.append(
            copy_sheet(
                ractions,
                on_copy,
                lambda: set_sheet(""),
                on_ask=asking.open,
                ask_reason=asking.reason,
            )
        )
    if sheet == "picker":
        controls.append(
            file_picker(
                rmodel,
                ractions,
                picker_query,
                seen=(lambda _: False) if reading is not None else seen,
                generated_open=generated_open,
                on_query=set_picker_query,
                on_toggle_generated=lambda: set_generated_open(lambda now: not now),
                on_pick=pick,
                on_close=lambda: set_sheet(""),
            )
        )
    if model.picker_open:
        controls.append(base_picker(model, actions))
    if model.reading_notice:
        controls.insert(
            0, ft.Text(model.reading_notice, key="changes-reading-notice", color=t.TEXT_SECONDARY)
        )
    # Opaque: a terminal opened earlier stays mounted beneath this pane and would show through.
    return question_overlay(
        ft.Container(
            key="changes-surface",
            bgcolor=t.GROUND,
            on_size_change=measure,
            content=ft.Column(key="changes-screen", spacing=0, controls=controls),
        ),
        asking.panel,
        wide=wide,
    )
