"""Row geometry and type per layout, and the tint of each kind of ref badge. One table keyed by
layout, from the approved boards: the gutter's lane gap and padding, the row height and the
subject and detail type sizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.theme import graph_palette

Layout = Literal["phone", "landscape", "tablet", "wide"]


@dataclass(frozen=True, slots=True)
class GraphStyle:
    lane_gap: int
    lane_pad: int
    row: int
    subject: float
    oid: float
    age: float
    lane_cap: int
    stacked: bool  # subject under the refs, oid and age; else one line


STYLES: dict[Layout, GraphStyle] = {
    "phone": GraphStyle(16, 14, 60, 14.5, 11.0, 11.5, 6, True),
    "landscape": GraphStyle(15, 12, 48, 14.0, 11.0, 11.5, 10, False),
    "tablet": GraphStyle(20, 16, 60, 16.0, 12.5, 13.0, 10, False),
    "wide": GraphStyle(18, 16, 56, 14.5, 11.5, 12.0, 10, True),
}


def layout_of(*, phone: bool, wide: bool, compact: bool) -> Layout:
    """The board a graph is drawn to: phone portrait, a phone on its side, a tablet upright, or
    the graph pane beside a commit."""
    if wide:
        return "wide"
    if phone:
        return "phone"
    return "landscape" if compact else "tablet"


@dataclass(frozen=True, slots=True)
class BadgeStyle:
    fill: str
    ink: str
    edge: str


def badge_style(kind: str) -> BadgeStyle:
    """Head in lime, base in violet, local branches in cyan, upstreams outlined, tags filled."""
    cyan = graph_palette()[2]
    if kind == "head":
        return BadgeStyle(
            ft.Colors.with_opacity(0.14, t.DONE), t.DONE, ft.Colors.with_opacity(0.45, t.DONE)
        )
    if kind == "base":
        return BadgeStyle(
            ft.Colors.with_opacity(0.16, t.PRIMARY_TEXT),
            t.PRIMARY_TEXT_LIGHT,
            ft.Colors.with_opacity(0.5, t.PRIMARY_TEXT),
        )
    if kind == "local":
        return BadgeStyle(
            ft.Colors.with_opacity(0.14, cyan), cyan, ft.Colors.with_opacity(0.45, cyan)
        )
    if kind == "upstream":
        return BadgeStyle(ft.Colors.TRANSPARENT, t.TEXT_SECONDARY, t.BORDER)
    return BadgeStyle(t.CARD_SELECTED, t.TEXT, t.BORDER)  # tag, split point
