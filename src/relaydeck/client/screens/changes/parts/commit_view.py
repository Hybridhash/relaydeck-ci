"""The commit open in the Graph tab: what it is, the files it changed and one file's diff. Wide
screens show it beside the graph; narrower ones show it as a screen of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.design_system.lists.virtual_list import Place
from relaydeck.client.design_system.notices import Notice, message_box
from relaydeck.client.features.ask_agent.api import short_oid
from relaydeck.client.features.changes.api import (
    ChangesActions,
    ChangesModel,
    CommitKey,
    CommitReading,
    ReadingActions,
)
from relaydeck.client.screens.changes.parts.commit_failure import BUSY_TEXT, GONE_TEXT, kind
from relaydeck.client.screens.changes.parts.diff_detail import diff_detail
from relaydeck.client.screens.changes.parts.diff_view import display_rows
from relaydeck.client.screens.changes.parts.file_list_view import FileListView
from relaydeck.client.screens.changes.parts.graph_rows import ref_badges
from relaydeck.client.screens.changes.parts.header import age_text
from relaydeck.client.screens.changes.parts.pickers import file_pager
from relaydeck.contracts.git import FileEntry

SUBJECT_WIDE = 22.0  # the board's subject in the pane beside the graph
HEADER_ROOM = t.MIN_TOUCH  # the one-line commit header over an open file


@dataclass(frozen=True, slots=True)
class _Listed:
    """Where the reader was in a commit's file list, with the commit and the files the list showed
    then: the place is kept only while the rows the reader was among are still those rows."""

    key: CommitKey
    files: tuple[FileEntry, ...]
    place: Place

    def kept(self, key: CommitKey, files: tuple[FileEntry, ...]) -> Place | None:
        """The place for `files` of commit `key`: the same list, or that list with more files
        under it while the reader was among the regular rows (the files that arrive follow
        those; only the generated group and its files move down). Another commit, or a list read
        afresh, starts at the top."""
        if key != self.key:
            return None
        if files is self.files or files == self.files:
            return self.place
        regular = sum(1 for f in self.files if not f.generated)
        if self.place.index < regular and files[: len(self.files)] == self.files:
            return self.place
        return None


def commit_hint() -> ft.Control:
    """The wide screen's right pane while no commit is chosen."""
    return _message("Choose a commit to see its changes.", key="commit-none")


LOST_TEXT = "This commit is no longer in the first page of the graph. Find it below or refresh."


def commit_lost(actions: ChangesActions, *, pane: bool) -> ft.Control:
    """The commit the reader had open is not on the refreshed first page: say so, in the commit's
    place (a wide screen's right pane) or above the graph."""
    refresh = PillButton("Refresh graph", actions.refresh, key="commit-lost-refresh")
    if pane:
        return _message(LOST_TEXT, refresh, key="commit-lost")
    return Notice(
        key="commit-lost",
        padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE, vertical=8),
        content=ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[ft.Text(LOST_TEXT, color=t.TEXT_SECONDARY, size=13), refresh],
        ),
    )


def _message(text: str, *extra: ft.Control, key: str = "commit-state") -> ft.Control:
    return message_box(text, extra, key=key)


def _parents(reading: CommitReading) -> str:
    parents = reading.commit.parents
    if not parents:
        return "root commit"
    return "parent " + " + ".join(short_oid(p) for p in parents)


def _top_bar(on_back: Callable[[], None]) -> ft.Control:
    return ft.Container(
        key="commit-back",
        height=t.MIN_TOUCH,
        padding=ft.Padding.only(right=12),
        alignment=ft.Alignment.CENTER_LEFT,
        on_click=lambda _: on_back(),
        tooltip="Back to the graph",
        content=ft.Row(
            tight=True,
            spacing=2,
            controls=[
                ft.Icon(ft.Icons.CHEVRON_LEFT, size=22, color=t.TEXT),
                ft.Text("Graph", size=15, weight=ft.FontWeight.W_700, color=t.TEXT),
            ],
        ),
    )


def _file_header(
    reading: CommitReading,
    *,
    on_back: Callable[[], None],
    on_ask: Callable[[], None] | None,
    ask_reason: str,
) -> ft.Control:
    """One line over an open file: the way back to the commit, its id and subject, its question."""
    commit = reading.commit
    controls: list[ft.Control] = [
        ft.Container(
            key="changes-files-back",
            height=t.MIN_TOUCH,
            padding=ft.Padding.only(left=4, right=8),
            alignment=ft.Alignment.CENTER_LEFT,
            tooltip="Back to the commit",
            on_click=lambda _: on_back(),
            content=ft.Row(
                tight=True,
                spacing=2,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.CHEVRON_LEFT, size=22, color=t.TEXT),
                    ft.Text(
                        short_oid(commit.oid),
                        font_family=FAMILY_MONO,
                        size=13,
                        weight=ft.FontWeight.W_700,
                        color=t.TEXT,
                    ),
                ],
            ),
        ),
        ft.Text(
            commit.subject or "(no message)",
            key="commit-subject",
            expand=True,
            size=14,
            weight=ft.FontWeight.W_600,
            color=t.TEXT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=commit.subject or None,
        ),
    ]
    if on_ask is not None:
        controls.append(
            Button(
                "Ask commit",
                variant="text",
                key="commit-ask",
                height=t.MIN_TOUCH,
                disabled=bool(ask_reason),
                tooltip=ask_reason or "Ask about this commit",
                on_click=lambda _: on_ask(),
            )
        )
    return ft.Container(
        key="commit-file-header",
        height=HEADER_ROOM,
        bgcolor=t.PANEL,
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        padding=ft.Padding.only(right=8),
        content=ft.Row(
            spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=controls
        ),
    )


def _details(
    outer: ChangesModel,
    reading: CommitReading,
    *,
    now_ms: int,
    on_ask: Callable[[], None] | None,
    ask_reason: str,
    on_copy: Callable[[str], None] | None,
    subject_size: float,
) -> ft.Control:
    commit = reading.commit
    buttons: list[ft.Control] = []
    if on_ask is not None:
        buttons.append(
            Button(
                "Ask about this commit",
                variant="filled",
                key="commit-ask",
                height=t.MIN_TOUCH,
                disabled=bool(ask_reason),
                tooltip=ask_reason or "Ask about this commit",
                style=ft.ButtonStyle(bgcolor=t.PRIMARY_FILL, color=t.ON_PRIMARY),
                on_click=lambda _: on_ask(),
            )
        )
    if on_copy is not None:
        buttons.append(
            ft.OutlinedButton(
                "Copy commit",
                key="commit-copy",
                height=t.MIN_TOUCH,
                tooltip="Copy the commit's full id",
                on_click=lambda _: on_copy(commit.oid),
            )
        )
    return ft.Container(
        key="commit-details",
        padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE, vertical=8),
        content=ft.Column(
            spacing=6,
            tight=True,
            controls=[
                ft.Text("COMMIT", size=11, color=t.TEXT_MUTED, weight=ft.FontWeight.W_600),
                ft.Text(
                    commit.subject or "(no message)",
                    key="commit-subject",
                    size=subject_size,
                    weight=ft.FontWeight.W_700,
                    color=t.TEXT,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Row(
                    spacing=10,
                    wrap=True,
                    controls=[
                        ft.Text(
                            short_oid(commit.oid), font_family=FAMILY_MONO, size=12, color=t.TEXT
                        ),
                        ft.Text(age_text(commit.time_ms, now_ms), size=12, color=t.TEXT_MUTED),
                        ft.Text(
                            _parents(reading),
                            key="commit-parents",
                            font_family=FAMILY_MONO,
                            size=12,
                            color=t.TEXT_MUTED,
                        ),
                    ],
                ),
                ref_badges(commit, outer),
                ft.Row(spacing=8, wrap=True, controls=buttons) if buttons else ft.Container(),
            ],
        ),
    )


def _summary(model: ChangesModel) -> str:
    count = len(model.files)
    text = f"{count}{'+' if model.files_next is not None else ''} "
    text += "file changed" if count == 1 and model.files_next is None else "files changed"
    if model.additions is not None and model.deletions is not None:
        text += f" · +{model.additions:,} −{model.deletions:,}"
    return text


def _files(
    reading: CommitReading,
    actions: ChangesActions,
    own: ReadingActions,
    *,
    generated_open: bool,
    on_toggle_generated: Callable[[], None],
    on_pick: Callable[[str], None],
    place: Place | None,
    on_scroll: Callable[[Place], None],
) -> ft.Control:
    """The commit's files, filling the pane under the details, or why there are none yet."""
    model = reading.model
    if model.snapshot is None:
        return _loading_or_failed(reading, actions, own)
    if not model.files:
        return _message("No file changes in this commit.", key="commit-empty")
    # A page that could not be fetched leaves the files shown; a dead cursor is not offered again.
    failure = kind(model.files_load.error) if model.files_load.status == "error" else None
    footer: list[ft.Control] = []
    if model.files_next is not None and failure != "gone":
        footer.append(
            Button(
                "Retry" if failure else "Show more files",
                variant="text",
                key="commit-more-files",
                height=t.MIN_TOUCH,
                on_click=lambda _: own.more_files(),
            )
        )
    listing = FileListView(
        name="commit-files",
        files=model.files,
        query="",
        selected=model.selected_file,
        seen=lambda _: False,
        on_pick=on_pick,
        prefix="commit-file",
        generated_open=generated_open,
        on_toggle_generated=on_toggle_generated,
        footer=footer,
        place=place,
        on_scroll=on_scroll,
    )
    return ft.Column(expand=True, spacing=0, controls=[listing, *_file_notice(failure, actions)])


def _file_notice(failure: str | None, actions: ChangesActions) -> list[ft.Control]:
    if failure is None:
        return []
    text = {"gone": GONE_TEXT, "busy": BUSY_TEXT}.get(failure, "Could not load more files.")
    controls: list[ft.Control] = [ft.Text(text, color=t.TEXT_SECONDARY, size=13)]
    if failure == "gone":
        controls.append(_refresh_graph(actions))
    return [
        ft.Container(
            key="commit-notice",
            padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE, vertical=4),
            content=ft.Column(
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=controls,
            ),
        )
    ]


def _refresh_graph(actions: ChangesActions) -> ft.Control:
    button: ft.Control = PillButton("Refresh graph", actions.refresh, key="commit-refresh-graph")
    return button


def _loading_or_failed(
    reading: CommitReading, actions: ChangesActions, own: ReadingActions
) -> ft.Control:
    load = reading.model.files_load
    if load.status != "error":
        return _message("Loading commit…", key="commit-loading")
    failure = kind(load.error)
    if failure == "gone":
        return _message(GONE_TEXT, _refresh_graph(actions))
    if failure == "busy":
        return _message(BUSY_TEXT, PillButton("Retry", own.refresh, key="commit-retry"))
    return _message(
        "Could not load this commit.", PillButton("Retry", own.refresh, key="commit-retry")
    )


def _diff(
    model: ChangesModel,
    own: ReadingActions,
    *,
    back: bool,
    on_lines: Callable[[], None] | None,
    recovery: ft.Control,
    on_ask: Callable[[], None] | None = None,
    ask_reason: str = "",
    select_hunk: bool = False,
) -> ft.Control:
    window = model.diff
    first = window.pages[0][0] if window is not None and window.pages else 0
    rows = display_rows(m.resident_pages(model), first)
    entry = next((f for f in model.files if f.file_id == model.selected_file), None)
    if entry is None:
        return _message("Choose a file to see its changes.", key="commit-choose")
    return diff_detail(
        model,
        own,
        entry,
        rows,
        lambda _: False,
        None,
        on_copy=on_lines if rows else None,
        back=back,
        on_ask=on_ask,
        ask_reason=ask_reason,
        select_hunk=select_hunk,
        recovery=recovery,
    )


@ft.component
def CommitReader(
    outer: ChangesModel,
    reading: CommitReading,
    actions: ChangesActions,
    own: ReadingActions,
    *,
    wide: bool,
    now_ms: int,
    on_back: Callable[[], None],
    on_ask: Callable[[], None] | None,
    ask_reason: str,
    on_copy: Callable[[str], None] | None,
    on_lines: Callable[[], None] | None,
    on_pick: Callable[[], None],
    on_ask_diff: Callable[[], None] | None = None,
    ask_diff_reason: str = "",
) -> ft.Control:
    """The right pane of a wide screen, or the whole commit screen of a narrow one. `actions`
    are the list's (refreshing the graph); `own` act on the commit's files and diff.
    """
    model = reading.model
    generated_open, set_generated_open = ft.use_state(False)
    # The commit whose details the reader went back to from its open file.
    overview, set_overview = ft.use_state(cast("CommitKey | None", None))
    first = model.files[0].file_id if model.files else None
    # Where the reader was in the file list, kept while a file has the pane (a `_Listed`).
    listed: Any = ft.use_ref(None)

    def select_first() -> None:
        set_overview(None)  # another commit, or another layout: start at the open file
        if listed.current is not None and listed.current.key != reading.key:
            listed.current = None
        if wide and model.selected_file is None and first is not None:
            own.select_file(first)

    ft.use_effect(select_first, dependencies=[reading.key, wide, first])

    # The runner notes where the reader is in a file as they scroll, without a render, so the
    # reading the screen published can be behind the one the runner holds. A file opens at the
    # place the runner holds now, read when the pane changes file, level or layout and not on
    # every render: a scroll never reaches the screen, and a render never moves the reader.
    def held_anchors() -> tuple[m.Anchor, ...]:
        live = own.current()
        return live.anchors if live.snapshot == model.snapshot else model.anchors

    anchors = ft.use_memo(held_anchors, [reading.key, wide, overview, model.selected_file])

    def open_file(file_id: str) -> None:
        set_overview(None)
        own.select_file(file_id)

    def note_place(place: Place) -> None:
        listed.current = _Listed(reading.key, model.files, place)

    def files() -> ft.Control:
        held: _Listed | None = listed.current
        return _files(
            reading,
            actions,
            own,
            generated_open=generated_open,
            on_toggle_generated=lambda: set_generated_open(lambda now: not now),
            on_pick=open_file,
            place=held.kept(reading.key, model.files) if held is not None else None,
            on_scroll=note_place,
        )

    def details() -> ft.Control:
        return _details(
            outer,
            reading,
            now_ms=now_ms,
            on_ask=on_ask,
            ask_reason=ask_reason,
            on_copy=on_copy,
            subject_size=SUBJECT_WIDE if wide else 16.0,
        )

    def diff(back: bool) -> ft.Control:
        return _diff(
            model if model.anchors == anchors else replace(model, anchors=anchors),
            own,
            back=back,
            on_lines=on_lines,
            recovery=_refresh_graph(actions),
            on_ask=on_ask_diff,
            ask_reason=ask_diff_reason,
            select_hunk=wide,
        )

    def pane(*controls: ft.Control) -> ft.Control:
        return ft.Column(key="commit-pane", expand=True, spacing=0, controls=list(controls))

    if wide:
        if model.selected_file is not None and overview != reading.key:
            header = _file_header(
                reading,
                on_back=lambda: set_overview(reading.key),
                on_ask=on_ask,
                ask_reason=ask_reason,
            )
            return pane(header, diff(False), file_pager(model, own, on_pick))
        return pane(details(), _section(model), files())
    if model.selected_file is not None:
        return pane(diff(True), file_pager(model, own, on_pick))
    return pane(_top_bar(on_back), details(), _section(model), files())


def _section(model: ChangesModel) -> ft.Control:
    if model.snapshot is None:
        return ft.Container()
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE, vertical=4),
        content=ft.Text(_summary(model), key="commit-summary", size=12, color=t.TEXT_MUTED),
    )
