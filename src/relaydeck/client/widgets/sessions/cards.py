"""Console agent cards and collapsed status tiles. `AgentCard` is the full console row;
`CollapsedAgentTile` is its 48px strip form.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.primitives import StatusPill, action_button, text_style
from relaydeck.client.design_system.surfaces import selected_outline
from relaydeck.client.design_system.theme import agent_identity, agent_subtitle_rest, space_tint
from relaydeck.client.models.changes_summary import ChangesRow
from relaydeck.client.widgets.sessions.pills import AgentPill, SpaceChip

_BAR = 3
_SELECTED_BAR = 6
_SELECTED_LINE = ft.Colors.with_opacity(0.45, T.TEXT)


def _highlight_ring() -> ft.BoxShadow:
    """Static 4px ring in the accent, shown briefly on a renamed card (no animation)."""
    return ft.BoxShadow(
        color=ft.Colors.with_opacity(0.18, T.PRIMARY_FILL),
        spread_radius=4,
        blur_radius=0,
        offset=ft.Offset(0, 0),
    )


def _changes_row(
    agent_id: str,
    changes: ChangesRow | None,
    on_changes: Callable[[str], Any],
    blocked: bool,
    ink: str,
    left: float,
) -> Any:
    """The card's bottom strip: branch, line counts, five share blocks and a chevron.

    Full width under a hairline, MIN_TOUCH high in every state so cards never change height.
    """
    key = f"agent-changes-{agent_id}"
    strip_fill = None if blocked else T.PANEL
    border = ft.Border(top=ft.BorderSide(1, T.BLOCKED_SMALL_TEXT if blocked else T.BORDER))
    padding = ft.Padding.only(left=left, right=T.CARD_PAD_H - 2)
    if changes is None or not changes.label:
        return ft.Container(
            key=f"{key}-reserved", height=T.MIN_TOUCH, bgcolor=strip_fill, border=border
        )

    def ink_of(color: str) -> str:
        return ink if blocked else color

    mono = text_style(FAMILY_MONO, T.TYPE_META, ink)
    controls: list[Any] = [
        ft.Icon(ft.Icons.ALT_ROUTE, size=15, color=ink, key=f"{key}-icon"),
    ]
    if changes.branch:
        # The branch shrinks first and the dirty dot stays right beside it.
        name: list[Any] = [
            ft.Text(
                changes.branch,
                key=f"{key}-branch",
                style=text_style(FAMILY_MONO, T.TYPE_META, T.BLOCKED_TEXT if blocked else T.TEXT),
                no_wrap=True,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
                expand_loose=True,
            )
        ]
        if changes.dirty:
            name.append(
                ft.Text(
                    "•",
                    key=f"{key}-dirty",
                    style=text_style(FAMILY_MONO, T.TYPE_META, ink_of(T.PRESENCE_WARN)),
                )
            )
        controls.append(ft.Row(expand=True, spacing=4, controls=name))
    figure = [
        ft.TextSpan(text, style=ft.TextStyle(color=color, weight=ft.FontWeight.W_700))
        for text, color in (
            (changes.plus, ink_of(T.DIFF_ADD_SIGN)),
            (f" {changes.minus}" if changes.plus else changes.minus, ink_of(T.DIFF_DELETE_SIGN)),
        )
        if text.strip()
    ]
    if changes.note:
        figure.append(
            ft.TextSpan(
                f" {changes.note}" if figure else changes.note, style=ft.TextStyle(color=ink)
            )
        )
    controls.append(
        ft.Text(
            spans=figure,
            key=f"{key}-figure",
            style=mono,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=not changes.branch,
        )
    )
    if changes.blocks:
        colours = {"add": T.DIFF_ADD_SIGN, "delete": T.DIFF_DELETE_SIGN, "none": T.BORDER}
        controls.append(
            ft.Row(
                key=f"{key}-blocks",
                spacing=2,
                controls=[
                    ft.Container(width=8, height=8, border_radius=2, bgcolor=ink_of(colours[b]))
                    for b in changes.blocks
                ],
            )
        )
    if changes.openable:
        controls.append(ft.Icon(ft.Icons.CHEVRON_RIGHT, size=18, color=ink, key=f"{key}-open"))
    return action_button(
        key=key,
        height=T.MIN_TOUCH,
        radius=0,
        tooltip=changes.label,
        on_press=lambda: on_changes(agent_id),
        content=ft.Container(
            height=T.MIN_TOUCH,
            bgcolor=strip_fill,
            border=border,
            padding=padding,
            alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=controls
            ),
        ),
    )


def _with_strip(pad: ft.Padding, strip: Any, body: ft.Column) -> Any:
    """The card body, padded, with the changes strip edge to edge below it when there is one."""
    if strip is None:
        return body
    return ft.Column(spacing=0, controls=[ft.Container(padding=pad, content=body), strip])


def AgentCard(
    agent_id: str,
    name: str,
    status: str,
    subtitle: str,
    meta_left: str,
    meta_right: str,
    selected: bool,
    on_select: Callable[[str], Any],
    agent: str = "",
    compact: bool = False,
    subject: str = "",
    highlighted: bool = False,
    on_name_double_tap: Callable[[], Any] | None = None,
    on_long_press: Callable[[], Any] | None = None,
    pinned: bool = False,
    space: str = "",
    changes: ChangesRow | None = None,
    on_changes: Callable[[str], Any] | None = None,
) -> Any:
    """Full agent card: title + pill, subject line (optional), subtitle, meta row. Blocked cards
    are solid orange with dark text; working/done/idle/ unknown are dark cards with colored
    pills.
    """
    blocked = status == T.STATUS_BLOCKED
    title_ink = T.BLOCKED_TEXT if blocked else T.TEXT
    line_ink = T.BLOCKED_TEXT if blocked else T.TEXT_SECONDARY
    meta_ink = T.BLOCKED_SMALL_TEXT if blocked else T.TEXT_MUTED
    identity = agent_identity(agent, True)
    rest = agent_subtitle_rest(subtitle, agent)
    if space and (rest == space or rest.startswith(f"{space} · ")):
        rest = rest[len(space) :].removeprefix(" · ")  # the chip shows the space
    meta_value = f" · {rest}" if rest and rest != subtitle else rest
    status_word = T.STATUS_LABELS.get(status, T.STATUS_LABELS[T.STATUS_UNKNOWN])
    tooltip = f"{identity.label} agent, {status_word}, {name}" if agent else name
    edge = T.PRIMARY_FILL if highlighted else T.BLOCKED if blocked else T.BORDER
    title: Any = ft.Text(
        name,
        style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_TITLE, title_ink),
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
        expand=True,
    )
    if on_name_double_tap is not None:
        title = ft.GestureDetector(
            key=f"agent-name-{agent_id}",
            expand=True,
            on_double_tap=lambda _e: on_name_double_tap(),
            content=title,
        )
    # Selected: the agent bar doubles, the card lifts and a grey line runs along the bottom; the
    # padding gives back the extra border so the content never shifts.
    left = T.CARD_PAD_H - (_SELECTED_BAR - _BAR if selected else 0)
    pad = ft.Padding.only(
        left=left,
        right=T.CARD_PAD_H,
        top=T.CARD_PAD_V,
        bottom=T.CARD_PAD_V - (1 if selected else 0),
    )
    button = action_button(
        key=f"agent-card-{agent_id}",
        radius=T.RADIUS_CARD,
        tooltip=None if compact else tooltip,
        on_press=lambda: on_select(agent_id),
        content=ft.Container(
            padding=None if on_changes is not None else pad,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=ft.BorderRadius.all(T.RADIUS_CARD),
            bgcolor=T.BLOCKED if blocked else T.CARD_SELECTED if selected else T.CARD,
            border=ft.Border(
                left=ft.BorderSide(_SELECTED_BAR if selected else _BAR, identity.color),
                top=ft.BorderSide(1, edge),
                right=ft.BorderSide(1, edge),
                bottom=ft.BorderSide(2, _SELECTED_LINE) if selected else ft.BorderSide(1, edge),
            ),
            shadow=[_highlight_ring()] if highlighted else None,
            content=_with_strip(
                pad,
                _changes_row(agent_id, changes, on_changes, blocked, meta_ink, left)
                if on_changes is not None
                else None,
                ft.Column(
                    spacing=3,
                    controls=[
                        ft.Row(
                            spacing=T.GAP_CARD,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                title,
                                *(
                                    [
                                        ft.Icon(
                                            ft.Icons.PUSH_PIN,
                                            key=f"agent-pinned-{agent_id}",
                                            size=14,
                                            color=meta_ink,
                                        )
                                    ]
                                    if pinned
                                    else []
                                ),
                                StatusPill(status, inverted=blocked),
                            ],
                        ),
                        *(
                            [
                                ft.Text(
                                    subject,
                                    key=f"agent-subject-{agent_id}",
                                    style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_LINE, line_ink),
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                )
                            ]
                            if subject
                            else []
                        ),
                        ft.Row(
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                AgentPill(
                                    identity.label,
                                    identity.color,
                                    key=f"agent-badge-{agent_id}",
                                    ink=T.BLOCKED_TEXT if blocked else None,
                                ),
                                *(
                                    [
                                        SpaceChip(
                                            space,
                                            space_tint(space),
                                            key=f"agent-space-{agent_id}",
                                            ink=T.BLOCKED_TEXT if blocked else None,
                                        )
                                    ]
                                    if space
                                    else []
                                ),
                                *(
                                    [
                                        ft.Text(
                                            meta_value,
                                            style=text_style(
                                                FAMILY_INTERFACE,
                                                T.TYPE_CARD_LINE_STRONG
                                                if blocked
                                                else T.TYPE_CARD_LINE,
                                                line_ink,
                                            ),
                                            max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            expand=True,
                                        )
                                    ]
                                    if meta_value
                                    else []
                                ),
                                *(
                                    # Unread rides at the end of this row, which every card has, so
                                    # reading never removes a row and the card keeps its height.
                                    [
                                        ft.Text(
                                            meta_right,
                                            key=f"agent-unread-text-{agent_id}",
                                            style=text_style(FAMILY_MONO, T.TYPE_META, meta_ink),
                                            no_wrap=True,
                                            expand=not meta_value,
                                            text_align=ft.TextAlign.RIGHT,
                                        )
                                    ]
                                    if meta_right and not meta_left
                                    else []
                                ),
                            ],
                        ),
                        *(
                            [
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        ft.Text(
                                            meta_left,
                                            style=text_style(FAMILY_MONO, T.TYPE_META, meta_ink),
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            expand=True,
                                        ),
                                        ft.Text(
                                            meta_right,
                                            style=text_style(FAMILY_MONO, T.TYPE_META, meta_ink),
                                            no_wrap=True,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                )
                            ]
                            if meta_left
                            else []
                        ),
                    ],
                ),
            ),
        ),
    )
    if on_long_press is not None:
        # The button's own press recognizer also owns the hold, so no second recognizer competes.
        button.on_long_press = lambda _e: on_long_press()
    return button


def _tile_style(status: str) -> tuple[str, str, int]:
    """(fill, ink, border_width) for a collapsed tile.

    Blocked tiles are solid orange; working/done/idle carry a 2px
    status border with status-colored initials; unknown is muted.
    """
    if status == T.STATUS_BLOCKED:
        return T.BLOCKED, T.BLOCKED_TEXT, 0
    if status == T.STATUS_WORKING:
        return T.CARD, T.WORKING, 2
    if status == T.STATUS_DONE:
        return T.CARD, T.DONE, 2
    if status == T.STATUS_IDLE:
        return T.CARD, T.IDLE_FILL, 2
    return T.CARD, T.UNKNOWN_TEXT, 1


def CollapsedAgentTile(
    agent_id: str,
    name: str,
    status: str,
    selected: bool,
    on_select: Callable[[str], Any],
) -> Any:
    """48px collapsed tile: two-letter initials, status colors, tooltip."""
    fill, ink, border_width = _tile_style(status)
    label = T.STATUS_LABELS.get(status, T.STATUS_LABELS[T.STATUS_UNKNOWN])
    return action_button(
        key=f"agent-tile-{agent_id}",
        width=T.MIN_TOUCH,
        height=T.MIN_TOUCH,
        radius=14,
        tooltip=f"{name}, {label.lower()}",
        on_press=lambda: on_select(agent_id),
        content=ft.Container(
            border_radius=ft.BorderRadius.all(14),
            bgcolor=fill,
            border=ft.Border.all(border_width, ink) if border_width else None,
            shadow=selected_outline(selected),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                T.initials(name),
                style=text_style(FAMILY_MONO, T.TYPE_TILE_INITIALS, ink),
            ),
        ),
    )
