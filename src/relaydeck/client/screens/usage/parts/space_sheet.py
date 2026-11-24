"""The Space Picker sheet: a bottom sheet on a phone, a dialog on a wide screen. The selection
is a draft until Done; Cancel discards it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.dialogs import Dialog
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.inputs import SearchField
from relaydeck.client.design_system.lists.virtual_list import Row, VirtualList
from relaydeck.client.design_system.sheets import Sheet
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.typography import ui
from relaydeck.client.screens.usage import model as sm
from relaydeck.client.screens.usage import pins as pin_store
from relaydeck.client.screens.usage.parts.primitives import overline

ROW = 50  # the fixed row height (spec §6.4: rows 50 tall)
ALL_SPACES = ""  # the picker's own id for "All spaces" (the page's state uses the same value)
SORT_LABEL = {sm.SORT_MOST: "Most tokens", sm.SORT_AZ: "A–Z", sm.SORT_RECENT: "Recent"}
# Most tokens -> A-Z -> Recent -> Most tokens
NEXT_SORT = {sm.SORT_MOST: sm.SORT_AZ, sm.SORT_AZ: sm.SORT_RECENT, sm.SORT_RECENT: sm.SORT_MOST}


@ft.component
def SpaceSheet(
    *,
    open: bool,
    rows: Sequence[sm.SpaceRow],
    selected: str,
    pins: Sequence[str],
    all_value: str,
    tokens: UsageColors,
    wide: bool,
    on_done: Callable[[str], Any],
    on_cancel: Callable[[], Any],
    on_pins: Callable[[tuple[str, ...]], Any],
) -> Any:
    """The picker. `selected` is the page's space ("" for all, a space id, or Not linked's id)."""
    pending, set_pending = ft.use_state(selected)
    query, set_query = ft.use_state("")
    sort, set_sort = ft.use_state(sm.SORT_MOST)

    def reset() -> None:
        set_pending(selected)
        set_query("")

    ft.use_effect(reset, dependencies=[open, selected])
    real_count = sum(1 for row in rows if not row.unlinked)
    arranged = sm.arrange_spaces(rows, query=query, sort=sort, pins=pins)

    def select(value: str) -> Callable[[Any], None]:
        return lambda _e: set_pending(value)

    def pin(space_id: str) -> Callable[[Any], None]:
        return lambda _e: on_pins(pin_store.toggle(pins, space_id))

    def space_row(row: sm.SpaceRow, *, key_prefix: str = "usage-space") -> ft.Control:
        chosen = row.key == pending
        name_color = tokens.text_faint if row.zero else tokens.text
        if row.unlinked:
            name_color = tokens.text_2
        star: ft.Control = ft.Container(width=0)
        if not row.unlinked:
            pinned = row.key in pins
            star = ft.IconButton(
                key=f"{key_prefix}-pin-{row.key}",
                icon=ft.Icons.STAR if pinned else ft.Icons.STAR_BORDER,
                icon_color=tokens.accent if pinned else tokens.text_muted,
                tooltip=("Unpin " if pinned else "Pin ") + row.name,
                on_click=pin(row.key),
                width=44,
                height=44,
            )
        row_control = ft.Container(
            key=f"{key_prefix}-option-{row.key}",
            height=ROW,
            padding=ft.Padding.only(left=6, right=8),
            border_radius=T.RADIUS_KEY,
            bgcolor=tokens.surface_accent if chosen else None,
            border=ft.Border.all(1, tokens.accent) if chosen else None,
            on_click=select(row.key),
            content=ft.Row(
                controls=[
                    star,
                    ft.Container(
                        expand=True,
                        content=ui(
                            row.name,
                            color=name_color,
                            size=15,
                            weight=700,
                            italic=row.unlinked,
                            max_lines=1,
                        ),
                    ),
                    ui(
                        row.tokens_text,
                        color=tokens.text_faint if row.zero else tokens.text_2,
                        size=14,
                        weight=600,
                        max_lines=1,
                    ),
                    ft.Icon(ft.Icons.CHECK, color=tokens.accent, size=18, visible=chosen),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        if row.unlinked:
            # Not linked is never pinnable, so it has no long-press handler (Flet rejects an empty
            # one).
            return row_control
        # The row is the tap target; the long-press detector wraps it for pinning.
        return ft.GestureDetector(
            key=f"{key_prefix}-press-{row.key}",
            on_long_press=pin(row.key),
            content=row_control,
        )

    items: list[Row] = []
    pinned_key = tuple(pins)

    def line(row: sm.SpaceRow, prefix: str = "usage-space") -> Row:
        """A space as a row that is built only when it is near the screen."""
        return Row(
            f"{prefix}-{row.key}",
            (row, row.key == pending, pinned_key, prefix),
            lambda: space_row(row, key_prefix=prefix),
        )

    def fixed(key: str, signature: object, build: Callable[[], ft.Control]) -> Row:
        return Row(key, signature, build)

    def all_spaces() -> ft.Control:
        return ft.Container(
            key="usage-space-option-all",
            height=ROW,
            padding=ft.Padding.symmetric(horizontal=12),
            border_radius=T.RADIUS_KEY,
            bgcolor=tokens.surface_accent if pending == ALL_SPACES else None,
            border=ft.Border.all(1, tokens.accent) if pending == ALL_SPACES else None,
            on_click=select(ALL_SPACES),
            content=ft.Row(
                controls=[
                    ft.Container(
                        expand=True,
                        content=ui(
                            "All spaces", color=tokens.text, size=15, weight=800, max_lines=1
                        ),
                    ),
                    ui(all_value, color=tokens.text_2, size=14, weight=600, max_lines=1),
                    ft.Icon(
                        ft.Icons.CHECK,
                        color=tokens.accent,
                        size=18,
                        visible=pending == ALL_SPACES,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def pinned_title() -> ft.Control:
        return ft.Container(
            height=ROW,
            alignment=ft.Alignment.BOTTOM_LEFT,
            padding=ft.Padding.only(bottom=8),
            content=overline("Pinned", color=tokens.text_muted, key="usage-space-pinned"),
        )

    def all_title() -> ft.Control:
        return ft.Container(
            height=ROW,
            padding=ft.Padding.only(bottom=6),
            content=ft.Row(
                controls=[
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.BOTTOM_LEFT,
                        content=overline(
                            f"All · {real_count}",
                            color=tokens.text_muted,
                            key="usage-space-all-title",
                        ),
                    ),
                    Button(
                        variant="text",
                        key="usage-space-sort",
                        content=ui(
                            SORT_LABEL[sort],
                            color=tokens.link,
                            size=13,
                            weight=700,
                            max_lines=1,
                        ),
                        tooltip="Sort: " + SORT_LABEL[NEXT_SORT[sort]] + " next",
                        on_click=lambda _e: set_sort(NEXT_SORT[sort]),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.END,
            ),
        )

    def nothing_found() -> ft.Control:
        return ft.Container(
            key="usage-space-empty",
            height=ROW,
            alignment=ft.Alignment.CENTER_LEFT,
            content=ui(
                f'No spaces match "{query.strip()}"',
                color=tokens.text_muted,
                size=14,
                weight=600,
                max_lines=2,
            ),
        )

    if arranged.results is None:
        items.append(fixed("all", (pending == ALL_SPACES, all_value), all_spaces))
        if arranged.pinned:
            items.append(fixed("pinned-title", None, pinned_title))
            items.extend(line(row, "usage-space-pinned") for row in arranged.pinned)
        items.append(fixed("all-title", (real_count, sort), all_title))
        items.extend(line(row) for row in arranged.rest)
    elif not arranged.results:
        items.append(fixed("empty", query.strip(), nothing_found))
    else:
        items.extend(line(row, "usage-space-result") for row in arranged.results)

    nav = ft.Row(
        controls=[
            Button(
                variant="text",
                key="usage-space-cancel",
                content=ui("Cancel", color=tokens.text_2, size=15, weight=700, max_lines=1),
                on_click=lambda _e: on_cancel(),
            ),
            ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ScreenHeader(
                    ui(
                        "Space",
                        color=tokens.text,
                        size=17,
                        weight=800,
                        max_lines=1,
                        align=ft.TextAlign.CENTER,
                    )
                ),
            ),
            Button(
                variant="text",
                key="usage-space-done",
                content=ui("Done", color=tokens.link, size=15, weight=800, max_lines=1),
                on_click=lambda _e: on_done(pending),
            ),
        ],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    search = SearchField(
        key="usage-space-search",
        hint_text=f"Search {real_count} spaces",
        value=query,
        on_change=lambda e: set_query(e.control.value or ""),
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        border_radius=T.RADIUS_KEY,
    )
    body = ft.Column(
        spacing=10,
        tight=True,
        controls=[
            nav,
            search,
            ft.Container(
                height=440 if wide else 420,
                content=VirtualList(
                    name="usage-space-list",
                    count=len(items),
                    height=ROW,
                    row=lambda i: items[i],
                    context=("spaces", query, sort),
                    expand=False,
                ),
            ),
        ],
    )
    if wide:
        return Dialog(
            key="usage-space-dialog",
            open=open,
            bgcolor=tokens.surface_sheet,
            radius=24,
            content=ft.Container(width=360, content=body),
            on_dismiss=lambda _e: on_cancel(),
        )
    return Sheet(
        body,
        key="usage-space-dialog",
        open=open,
        tokens=tokens,
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        on_dismiss=lambda _e: on_cancel(),
    )
