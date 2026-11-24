"""One file's diff: a fixed header over a lazily built, fixed-height list of monospace rows.
Sizes: Flet 1.0.3 does not tell a page the system text scale (`PageMediaData` carries
paddings, insets, pixel ratio, orientation and the clock format only, and `Text` has no scale
argument), so rows and columns cannot follow it.
"""

from __future__ import annotations

import asyncio
import math
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import flet as ft

import relaydeck.client.screens.changes.parts.hunk_selection as selection
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.design_system.lists.virtual_list import Place, VirtualList, scroll_list
from relaydeck.client.design_system.lists.virtual_list import Row as VirtualRow
from relaydeck.client.screens.changes.parts.diff_chrome import _message, _page_button
from relaydeck.contracts.git import DiffLine, FileDiff

FONT_SIZE = 12.0
LINE_BOX = 1.32  # JetBrains Mono ascent + descent, in em
TEXT_SCALE_MARGIN = 1.5  # system text enlargement the rows and columns allow for
ROW_HEIGHT = math.ceil(FONT_SIZE * LINE_BOX * TEXT_SCALE_MARGIN)
CHAR_WIDTH = 7.25  # JetBrains Mono advance at FONT_SIZE
MIN_GUTTER_DIGITS = 3
SIGN_CHARS = 2
TAB_COLUMNS = 8  # a tab is drawn at most this wide
PAD = 8
SCROLL_SETTLE_S = 0.05

RowPlace = tuple[int, int, int, int]  # page, hunk on the page, fragment, row in the hunk

RowKind = Literal["hunk", "context", "add", "delete", "no_newline"]
_SIGNS = {"add": "+", "delete": "−", "context": " ", "no_newline": " ", "hunk": ""}
_STATE_TEXT = {
    "load_required": "This file is large. Load it to see the changes.",
    "too_large": "Too large to show here. View it in a terminal on the Mac.",
    "withheld": "Content withheld: this file can hold secrets.",
    "binary": "Binary file; no text to show.",
    "invalid_encoding": "This file is not UTF-8 text, so it is not shown.",
    "unsupported": "This change cannot be shown as text.",
}


@dataclass(frozen=True, slots=True)
class DisplayRow:
    """One fixed-height row: a hunk header or a line, with its position for copy actions."""

    kind: RowKind
    text: str
    old: int | None = None
    new: int | None = None
    marks: tuple[tuple[int, int], ...] = ()
    masked: bool = False
    page: int = 0  # page index in the file's sequence, not among the resident pages
    hunk: int = 0  # index of the hunk (fragment) within its page
    fragment: int = 0
    line: int = -1  # index among the hunk's lines; -1 for its header

    @property
    def place(self) -> RowPlace:
        return (self.page, self.hunk, self.fragment, self.line)


def display_rows(pages: Sequence[FileDiff], first: int = 0) -> tuple[DisplayRow, ...]:
    """Flatten consecutive pages (the first is page `first`); a continued fragment has no header."""
    rows: list[DisplayRow] = []
    for offset, page in enumerate(pages):
        for hunk_index, hunk in enumerate(page.hunks):
            if hunk.fragment == 0:
                rows.append(DisplayRow("hunk", hunk.header, page=first + offset, hunk=hunk_index))
            rows.extend(
                _line(line, first + offset, hunk_index, hunk.fragment, number)
                for number, line in enumerate(hunk.lines)
            )
    return tuple(rows)


def display_columns(text: str) -> int:
    """Cells a line needs: wide glyphs take two, combining marks none, a tab up to eight."""
    cells = 0
    for char in text:
        if char == "\t":
            cells += TAB_COLUMNS
        elif unicodedata.combining(char):
            continue
        else:
            cells += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return cells


def max_columns(rows: Sequence[DisplayRow]) -> int:
    return max((display_columns(row.text) for row in rows), default=0)


def gutter_width(rows: Sequence[DisplayRow]) -> float:
    digits = max(
        (len(str(n)) for row in rows for n in (row.old, row.new) if n is not None),
        default=MIN_GUTTER_DIGITS,
    )
    return max(digits, MIN_GUTTER_DIGITS) * CHAR_WIDTH * TEXT_SCALE_MARGIN


def content_width(rows: Sequence[DisplayRow]) -> float:
    """Width of one row: two gutters, the sign and the longest line, each with the scale margin."""
    cells = SIGN_CHARS + max_columns(rows)
    return 2 * gutter_width(rows) + 2 * PAD + cells * CHAR_WIDTH * TEXT_SCALE_MARGIN


def state_text(page: FileDiff | None) -> str | None:
    """Message shown instead of rows, or None when there are rows to show."""
    if page is None or page.render == "ok":
        return None
    return _STATE_TEXT.get(page.render, _STATE_TEXT["unsupported"])


def _line(line: DiffLine, page: int, hunk: int, fragment: int, number: int) -> DisplayRow:
    return DisplayRow(
        line.kind,
        line.text,
        line.old,
        line.new,
        line.marks,
        line.masked,
        page,
        hunk,
        fragment,
        number,
    )


@ft.component
def DiffFileView(
    title: str,
    subtitle: str,
    rows: Sequence[DisplayRow],
    *,
    message: str | None = None,
    has_more: bool = False,
    on_more: Callable[[], None] | None = None,
    has_earlier: bool = False,
    on_earlier: Callable[[], None] | None = None,
    on_load: Callable[[], None] | None = None,
    notice: ft.Control | None = None,
    message_extra: Sequence[ft.Control] = (),
    header_actions: Sequence[ft.Control] = (),
    leading: ft.Control | None = None,
    counts: tuple[int, int] | None = None,
    anchor: RowPlace | None = None,
    on_place: Callable[[DisplayRow], None] | None = None,
    on_hunk: Callable[[tuple[int, int]], None] | None = None,
    selected_hunk: tuple[int, int] | None = None,
) -> ft.Control:
    """Header, then rows in one horizontal scroll sized to the longest line. `anchor` is where
    the reader was; the list is moved there when the rows appear and again whenever the
    resident rows change under the reader.
    """
    listing: Any = ft.use_ref(lambda: ft.Ref[ft.ListView]())
    reading: Any = ft.use_ref(None)
    if reading.current is None:
        reading.current = {"offset": 0.0, "place": None}
    state: dict[str, Any] = reading.current
    first = rows[0].place if rows else None

    selectable = on_hunk is not None

    def moved(at: Place) -> None:
        if not rows:
            return
        state["offset"] = selection.offset(rows, at.index, ROW_HEIGHT, selectable) + at.offset
        row = rows[at.index]
        state["place"] = row.place
        if on_place is not None:
            on_place(row)

    async def place_reader() -> None:
        target = state["place"] or anchor
        if not rows or target is None or listing.current is None:
            return
        index = next((i for i, row in enumerate(rows) if row.place == target), None)
        if index is None:
            # The page the reader was on is gone: reading on continues at the new page's top,
            # reading back at the end of the earlier page.
            offset = 0.0 if target[0] < rows[0].page else -1.0
        else:
            # The last row is shown by going to the end, which needs no knowledge of the viewport.
            offset = (
                -1.0
                if index == len(rows) - 1
                else selection.offset(rows, index, ROW_HEIGHT, on_hunk is not None)
            )
        if abs(offset - state["offset"]) < 1:
            return
        await asyncio.sleep(SCROLL_SETTLE_S)  # let the new rows reach the client first
        await scroll_list(listing.current, offset)
        state["offset"] = offset  # keep the -1 end sentinel: already at the end needs no move

    ft.use_effect(place_reader, dependencies=[first, len(rows), anchor])
    figure: list[ft.TextSpan] = [ft.TextSpan(subtitle)] if subtitle else []
    if counts is not None:
        gap = " · " if subtitle else ""
        figure.append(ft.TextSpan(f"{gap}+{counts[0]:,}", ft.TextStyle(color=t.DIFF_ADD_SIGN)))
        figure.append(ft.TextSpan(f" −{counts[1]:,}", ft.TextStyle(color=t.DIFF_DELETE_SIGN)))
    header = ft.Container(
        key="diff-header",
        bgcolor=t.PANEL,
        border=ft.Border(bottom=ft.BorderSide(1, t.BORDER)),
        padding=ft.Padding.only(left=4 if leading is not None else PAD, right=PAD, top=4, bottom=4),
        content=ft.Row(
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                *([leading] if leading is not None else []),
                ft.Column(
                    expand=True,
                    spacing=0,
                    tight=True,
                    controls=[
                        ft.Text(
                            title,
                            font_family=FAMILY_MONO,
                            size=15,
                            weight=ft.FontWeight.W_700,
                            color=t.TEXT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=title,
                        ),
                        ft.Text(
                            spans=figure,
                            font_family=FAMILY_MONO,
                            size=11,
                            color=t.TEXT_MUTED,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                ),
                *header_actions,
            ],
        ),
    )
    controls: list[ft.Control] = [header]
    if message is not None:
        controls.append(_message(message, on_load, message_extra))
    else:
        if has_earlier and on_earlier is not None:
            controls.append(_page_button("Show earlier", "diff-earlier", on_earlier))
        opened = state["place"] or anchor
        at = next((i for i, row in enumerate(rows) if row.place == opened), None)
        controls.append(
            _rows(
                rows, listing, moved, on_hunk, selected_hunk, Place(at) if at is not None else None
            )
        )
        if has_more and on_more is not None:
            controls.append(_page_button("Show more", "diff-more", on_more))
        if notice is not None:
            controls.append(notice)
    return ft.Column(spacing=0, controls=controls)


def _rows(
    rows: Sequence[DisplayRow],
    ref: ft.Ref[ft.ListView],
    on_scroll: Callable[[Place], None],
    on_hunk: Callable[[tuple[int, int]], None] | None = None,
    selected_hunk: tuple[int, int] | None = None,
    open_at: Place | None = None,
) -> ft.Control:
    """The rows of the resident pages; only those near the screen become controls."""
    gutter = gutter_width(rows)
    selectable = on_hunk is not None
    offsets: tuple[float, ...] | None = None
    if selectable:  # a selectable hunk header is taller than a code line
        tops, at = [], 0.0
        for row in rows:
            tops.append(at)
            at += selection.height(row, ROW_HEIGHT, True)
        offsets = (*tops, at)

    def described(index: int) -> VirtualRow:
        row = rows[index]
        chosen = selected_hunk if row.kind == "hunk" else None
        return VirtualRow(
            f"{row.page}-{row.hunk}-{row.fragment}-{row.line}",
            (row, gutter, index, chosen, selectable),
            lambda: selection.selectable_header(
                _row(index, row, gutter), row, on_hunk, selected_hunk
            ),
        )

    listing = VirtualList(
        name="diff-rows",
        count=len(rows),
        height=ROW_HEIGHT,
        row=described,
        context=("diff", rows[0].place if rows else None),
        offsets=offsets,
        open_at=open_at,
        on_scroll=on_scroll,
        ref=ref,
        expand=False,
    )
    return ft.Row(
        key="diff-scroll",
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[ft.Container(width=content_width(rows), content=listing, bgcolor=t.TERMINAL_BG)],
    )


def _row(index: int, row: DisplayRow, gutter: float) -> ft.Control:
    if row.kind == "hunk":
        return ft.Container(
            key=f"diff-row-{index}",
            height=ROW_HEIGHT,
            bgcolor=t.DIFF_HUNK_BG,
            padding=ft.Padding.only(left=PAD),
            alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Text(
                row.text,
                font_family=FAMILY_MONO,
                size=FONT_SIZE,
                color=t.DIFF_HUNK_TEXT,
                no_wrap=True,
            ),
        )
    background, mark, sign = _colours(row.kind)
    return ft.Container(
        key=f"diff-row-{index}",
        height=ROW_HEIGHT,
        bgcolor=background,
        alignment=ft.Alignment.CENTER_LEFT,
        content=ft.Row(
            spacing=0,
            controls=[
                _number(row.old, gutter),
                _number(row.new, gutter),
                ft.Text(
                    _SIGNS[row.kind],
                    width=SIGN_CHARS * CHAR_WIDTH * TEXT_SCALE_MARGIN,
                    text_align=ft.TextAlign.CENTER,
                    font_family=FAMILY_MONO,
                    size=FONT_SIZE,
                    color=sign,
                ),
                ft.Text(
                    spans=_spans(row, mark),
                    font_family=FAMILY_MONO,
                    size=FONT_SIZE,
                    color=t.TEXT_MUTED if row.kind == "no_newline" else t.TEXT,
                    no_wrap=True,
                    semantics_label=_spoken(row),
                ),
            ],
        ),
    )


def _spans(row: DisplayRow, mark: str | None) -> list[ft.TextSpan]:
    if not row.marks or mark is None:
        return [ft.TextSpan(row.text)]
    spans: list[ft.TextSpan] = []
    edge = 0
    for start, end in row.marks:
        if start > edge:
            spans.append(ft.TextSpan(row.text[edge:start]))
        spans.append(ft.TextSpan(row.text[start:end], style=ft.TextStyle(bgcolor=mark)))
        edge = end
    if edge < len(row.text):
        spans.append(ft.TextSpan(row.text[edge:]))
    return spans


def _colours(kind: RowKind) -> tuple[str | None, str | None, str]:
    if kind == "add":
        return t.DIFF_ADD_BG, t.DIFF_ADD_MARK, t.DIFF_ADD_SIGN
    if kind == "delete":
        return t.DIFF_DELETE_BG, t.DIFF_DELETE_MARK, t.DIFF_DELETE_SIGN
    return None, None, t.DIFF_GUTTER_TEXT


def _number(value: int | None, width: float) -> ft.Text:
    return ft.Text(
        "" if value is None else str(value),
        width=width,
        text_align=ft.TextAlign.RIGHT,
        font_family=FAMILY_MONO,
        size=FONT_SIZE,
        color=t.DIFF_GUTTER_TEXT,
    )


def _spoken(row: DisplayRow) -> str:
    what = {"add": "added", "delete": "removed", "context": "unchanged", "no_newline": "note"}
    line = row.new if row.kind != "delete" else row.old
    prefix = f"line {line}, " if line is not None else ""
    return f"{prefix}{what.get(row.kind, '')}: {'hidden secret' if row.masked else row.text}"
