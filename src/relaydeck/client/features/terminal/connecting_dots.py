"""The "Connecting" chip shown over a terminal while the app takes control on its own. Three
accent dots bob up and down in turn.
"""

from __future__ import annotations

import flet as ft

from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.theme import Palette

DOT_SIZE = 8
BOB_MS = 320
CHIP_HEIGHT = 32
_LIFT = -0.6  # fraction of the dot's own height


@ft.component
def ConnectingDots(
    tokens: Palette, visible: bool, cover: bool = False, *, reduce_motion: bool = False
) -> ft.Control:
    """`cover` also hides the terminal beneath (it keeps laying out and reporting sizes), so a
    terminal that is still settling its size is never seen mid-resize."""
    visible = visible or cover
    up, set_up = ft.use_state(False)

    def flip(_: object) -> None:
        if visible and not reduce_motion:
            set_up(lambda current: not current)

    def start() -> None:
        # Nothing animates until a value changes: the first flip starts the chain, and each
        # finished bob flips the next.
        if visible and not reduce_motion:
            set_up(True)

    ft.use_effect(start, dependencies=[visible, reduce_motion])

    def dot(index: int) -> ft.Control:
        lifted = up if index % 2 == 0 else not up
        return ft.Container(
            key=f"terminal-connecting-dot-{index}",
            width=DOT_SIZE,
            height=DOT_SIZE,
            border_radius=DOT_SIZE / 2,
            bgcolor=tokens.accent,
            offset=ft.Offset(0, _LIFT if lifted and visible and not reduce_motion else 0),
            animate_offset=None
            if reduce_motion
            else ft.Animation(BOB_MS, ft.AnimationCurve.EASE_IN_OUT),
            on_animation_end=flip if index == 0 and not reduce_motion else None,
        )

    return ft.Container(
        key="terminal-connecting",
        visible=visible,
        ignore_interactions=True,
        bgcolor=tokens.terminal_bg if cover else None,
        alignment=ft.Alignment.BOTTOM_CENTER,
        padding=ft.Padding.only(bottom=16),
        content=Badge(
            height=CHIP_HEIGHT,  # the viewport stack expands its children; the chip must not
            bgcolor=tokens.raised,
            border_radius=CHIP_HEIGHT / 2,
            padding=ft.Padding.symmetric(horizontal=14),
            content=ft.Row(
                tight=True,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    dot(0),
                    dot(1),
                    dot(2),
                    ft.Container(width=4),
                    ft.Text("Connecting…", size=12, color=tokens.muted),
                ],
            ),
        ),
    )
