"""The "Copy…" sheet: the file's resident hunks, each copied as a diff or as code, or line by
line. Every button is at least 48 dp; code rows in the diff are not tappable, so this sheet
is how a hunk or a line is chosen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.design_system.lists.virtual_list import Place, Row, VirtualList
from relaydeck.client.features.changes.api import (
    CopyForm,
    CopyLine,
    HunkGroup,
    ReadingActions,
    hunk_groups,
    resolve,
    target_for,
)


@ft.component
def CopySheet(
    groups: Sequence[HunkGroup],
    on_hunk: Callable[[HunkGroup, CopyForm], None],
    on_line: Callable[[HunkGroup, CopyLine], None],
    on_close: Callable[[], None],
    on_ask: Callable[..., None] | None = None,
    ask_reason: Callable[..., str] = lambda *args, **kwargs: "",
) -> ft.Control:
    chosen, set_chosen = ft.use_state(-1)
    last, set_last = ft.use_state(0)  # the hunk whose lines were opened last: Back returns to it
    # The rows are cached across renders; what they call is read at the time of the click.
    latest: Any = ft.use_ref(None)
    latest.current = (on_hunk, on_ask, ask_reason)

    def open_lines(index: int) -> None:
        set_last(index)
        set_chosen(index)

    group = groups[chosen] if 0 <= chosen < len(groups) else None
    if group is None:
        title = "From this file"
        # A long file has many hunks: only those near the screen become controls.
        listing: ft.Control = VirtualList(  # type: ignore[call-arg]
            key="copy-hunks",
            name="changes-copy-list",
            count=len(groups),
            height=HUNK_ROW,
            row=lambda i: _hunk_row(i, groups[i], latest, open_lines),
            keys=lambda i: f"{groups[i].page}-{groups[i].hunk}",
            estimate=HUNK_ROW,
            revision=groups,
            context=("hunks", len(groups)),
            place=Place(min(last, max(0, len(groups) - 1))) if last else None,
        )
    else:
        title = group.header or "Lines"
        # A long hunk is a long list: only the lines near the screen become controls.
        listing = VirtualList(  # type: ignore[call-arg]
            key="copy-lines",
            name="changes-copy-list",
            count=len(group.lines),
            height=t.MIN_TOUCH,
            row=lambda i: _line_row(group.lines[i], group, on_line, latest),
            context=("lines", chosen, group.page, group.hunk),
        )
    leading: list[ft.Control] = []
    if group is not None:
        leading.append(PillButton("Hunks", lambda: set_chosen(-1), key="changes-copy-back"))
    return ft.Container(
        key="changes-copy-sheet",
        bgcolor=t.RAISED,
        padding=12,
        height=360,
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Row(
                    spacing=8,
                    controls=[
                        *leading,
                        ft.Text(
                            title,
                            size=15,
                            weight=ft.FontWeight.W_700,
                            color=t.TEXT,
                            font_family=FAMILY_MONO if group is not None else None,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                        PillButton("Close", on_close, key="changes-copy-close"),
                    ],
                ),
                listing,
            ],
        ),
    )


HUNK_ROW = 96.0  # dp; hunk rows are measured as they are built, this only starts the guess


def _hunk_row(index: int, group: HunkGroup, latest: Any, open_lines: Callable[[int], None]) -> Row:
    on_ask, ask_reason = latest.current[1], latest.current[2]
    unavailable = ask_reason("hunk", page=group.page, hunk=group.hunk, row=None) if on_ask else ""
    return Row(
        f"{group.page}-{group.hunk}",
        (index, group, on_ask is not None, unavailable),
        lambda: _hunk_card(index, group, latest, open_lines),
    )


def _hunk_card(
    index: int, group: HunkGroup, latest: Any, open_lines: Callable[[int], None]
) -> ft.Control:
    def on_hunk(chosen: HunkGroup, form: CopyForm) -> None:
        latest.current[0](chosen, form)

    def on_ask(*args: Any, **kwargs: Any) -> None:
        latest.current[1](*args, **kwargs)

    def ask_reason(*args: Any, **kwargs: Any) -> str:
        return str(latest.current[2](*args, **kwargs))

    shown = sum(line.copyable for line in group.lines)
    state = f"{shown} lines" if not group.partial else f"Part of a hunk · {shown} lines held"
    return ft.Container(
        key=f"changes-copy-hunk-{index}",
        padding=ft.Padding.only(top=6),
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Text(
                    group.header,
                    font_family=FAMILY_MONO,
                    size=12.5,
                    color=t.TEXT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(state, size=12, color=t.TEXT_MUTED),
                ft.Row(
                    spacing=0,
                    controls=[
                        Button(
                            "Copy diff",
                            variant="text",
                            key=f"changes-copy-hunk-{index}-diff",
                            height=t.MIN_TOUCH,
                            tooltip="Header and lines with + and - signs",
                            on_click=lambda _: on_hunk(group, "diff"),
                        ),
                        Button(
                            "Copy code",
                            variant="text",
                            key=f"changes-copy-hunk-{index}-code",
                            height=t.MIN_TOUCH,
                            tooltip="The new version, without signs",
                            on_click=lambda _: on_hunk(group, "code"),
                        ),
                        *_ask_button(
                            on_ask if latest.current[1] is not None else None,
                            ask_reason,
                            "hunk",
                            group.page,
                            group.hunk,
                            None,
                        ),
                        Button(
                            "Lines",
                            variant="text",
                            key=f"changes-copy-hunk-{index}-lines",
                            height=t.MIN_TOUCH,
                            on_click=lambda _: open_lines(index),
                        ),
                    ],
                ),
            ],
        ),
    )


def _line_row(
    line: CopyLine,
    group: HunkGroup,
    on_line: Callable[[HunkGroup, CopyLine], None],
    latest: Any,
) -> Row:
    on_ask, ask_reason = latest.current[1], latest.current[2]

    def ask(*args: Any, **kwargs: Any) -> None:
        latest.current[1](*args, **kwargs)

    place: Any = (line.page, line.hunk, line.row)
    unavailable = ask_reason("line", page=line.page, hunk=line.hunk, row=line.row) if on_ask else ""
    hidden = line.masked
    return Row(
        f"{line.page}-{line.hunk}-{line.row}",
        (line, on_ask is not None, unavailable),
        lambda: ft.Container(
            key=f"changes-copy-line-{place[0]}-{place[1]}-{place[2]}",
            height=t.MIN_TOUCH,
            padding=ft.Padding.symmetric(horizontal=8),
            alignment=ft.Alignment.CENTER_LEFT,
            on_click=(None if not line.copyable else lambda _, chosen=line: on_line(group, chosen)),
            tooltip="Hidden secret" if hidden else "Copy this line",
            content=ft.Row(
                spacing=10,
                controls=[
                    *_ask_button(
                        ask if on_ask is not None else None,
                        ask_reason,
                        "line",
                        line.page,
                        line.hunk,
                        line.row,
                    ),
                    ft.Text(
                        "" if line.number is None else str(line.number),
                        width=48,
                        text_align=ft.TextAlign.RIGHT,
                        font_family=FAMILY_MONO,
                        size=12,
                        color=t.DIFF_GUTTER_TEXT,
                    ),
                    ft.Text(
                        "Hidden secret" if hidden else line.text,
                        expand=True,
                        font_family=FAMILY_MONO,
                        size=12.5,
                        color=t.TEXT_MUTED if not line.copyable else t.TEXT,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
            ),
        ),
    )


def copy_sheet(
    actions: ReadingActions,
    on_copy: Callable[[str], None],
    close: Callable[[], None],
    *,
    on_ask: Callable[..., None] | None = None,
    ask_reason: Callable[..., str] = lambda *args, **kwargs: "",
) -> ft.Control:
    """The Copy sheet for the file and snapshot it is drawn for.

    A target takes its file and snapshot from the model the sheet was drawn with and is resolved
    against the model as it is when the reader chooses, so a click that arrives after the reader
    moved on copies nothing.
    """
    drawn = actions.current()

    def copy(group: HunkGroup, line: CopyLine | None, form: CopyForm = "code") -> None:
        target = target_for(drawn, group, line, form)
        text = None if target is None else resolve(actions.current(), target)
        if text:
            on_copy(text)
        close()

    return CopySheet(
        hunk_groups(drawn),
        lambda group, form: copy(group, None, form),
        copy,
        close,
        on_ask,
        ask_reason,
    )


def _ask_button(
    on_ask: Callable[..., None] | None,
    reason: Callable[..., str],
    kind: str,
    page: int,
    hunk: int,
    row: int | None,
) -> list[ft.Control]:
    if on_ask is None:
        return []
    place: dict[str, Any] = {"page": page, "hunk": hunk, "row": row}
    unavailable = reason(kind, **place)
    return [
        Button(
            "Ask",
            variant="text",
            key=f"changes-ask-{kind}-{page}-{hunk}-{row}",
            height=t.MIN_TOUCH,
            disabled=bool(unavailable),
            tooltip=unavailable,
            on_click=lambda _: on_ask(kind, **place),
        )
    ]
