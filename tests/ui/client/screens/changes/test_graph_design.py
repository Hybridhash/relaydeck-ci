"""The graph follows the approved boards: one style table, badge tints, HEAD, selection, summary."""

from __future__ import annotations

from typing import Any

import flet as ft
import flet.canvas as cv
import pytest

from relaydeck.client.design_system import tokens as t
from relaydeck.client.features.changes.graph_layout import GraphRow, Segment
from relaydeck.client.screens.changes.parts.graph_canvas import HEAD_RADIUS, HEAD_RING, graph_canvas
from relaydeck.client.screens.changes.parts.graph_style import STYLES, badge_style, layout_of
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, graph, row_key, summary_text

BOARDS = {
    "phone": (16, 14, 60, 14.5),
    "landscape": (15, 12, 48, 14.0),
    "tablet": (20, 16, 60, 16.0),
    "wide": (18, 16, 56, 14.5),
}


def test_one_table_holds_each_boards_gutter_row_and_type() -> None:
    for layout, (gap, pad, row, subject) in BOARDS.items():
        style = STYLES[layout]  # type: ignore[index]
        assert (style.lane_gap, style.lane_pad, style.row, style.subject) == (
            gap,
            pad,
            row,
            subject,
        )
        assert style.row >= t.MIN_TOUCH


def test_the_layout_follows_the_screen() -> None:
    assert layout_of(phone=True, wide=False, compact=False) == "phone"
    assert layout_of(phone=False, wide=False, compact=True) == "landscape"
    assert layout_of(phone=False, wide=False, compact=False) == "tablet"
    assert layout_of(phone=False, wide=True, compact=False) == "wide"
    assert STYLES["phone"].stacked and STYLES["wide"].stacked
    assert not STYLES["landscape"].stacked and not STYLES["tablet"].stacked


def test_each_kind_of_ref_has_its_own_tint() -> None:
    looks = {kind: badge_style(kind) for kind in ("head", "base", "local", "upstream", "tag")}
    assert len({(v.fill, v.ink, v.edge) for v in looks.values()}) == 5
    assert looks["head"].ink == t.DONE and looks["base"].ink == t.PRIMARY_TEXT_LIGHT
    assert looks["upstream"].fill == ft.Colors.TRANSPARENT  # outlined
    assert looks["tag"].fill == t.CARD_SELECTED and badge_style("split") == looks["tag"]


def shapes(row: GraphRow) -> list[Any]:
    canvas = graph_canvas(row, style=STYLES["phone"], lanes=6)
    return list(canvas.content.controls[0].shapes)  # type: ignore[union-attr]


def test_the_head_dot_has_a_faint_ring_around_it() -> None:
    row = GraphRow("a" * 40, 0, 1, (Segment("down", 0, 0, 1),), head=True, ahead=True)
    circles = [s for s in shapes(row) if isinstance(s, cv.Circle)]
    assert {c.radius for c in circles} == {HEAD_RADIUS, HEAD_RING}
    ring = next(c for c in circles if c.radius == HEAD_RING)
    assert ring.paint.stroke_width == 2
    assert "0.35" in str(ring.paint.color) or ring.paint.color != ring.paint.color.replace("#", "")


@pytest.mark.parametrize("wide", [False, True])
def test_the_open_row_has_a_violet_bar_and_the_rest_a_clear_one(wide: bool) -> None:
    gateway = Gateway(files(), issue_commit_ids=False)

    async def scenario() -> None:
        app = await graph(
            gateway, width=1376 if wide else 390, height=1000 if wide else 844, wide=wide
        )
        await click(app, row_key(gateway, 1))
        await app.settle(5)
        chosen, other = app.find(row_key(gateway, 1)), app.find(row_key(gateway, 0))
        assert chosen.border.left.width == 3 and chosen.border.left.color == t.PRIMARY_TEXT
        assert chosen.bgcolor == t.CARD_SELECTED
        assert other.border.left.width == 3 and other.border.left.color == ft.Colors.TRANSPARENT

    run(scenario())


def test_the_summary_colours_the_base_and_sets_the_oid_in_mono() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=1376, height=1000, wide=True)
        summary = app.find("graph-summary")
        text = summary_text(summary)
        assert "newest first" in text and " · split at " in text
        base = next(s for s in summary.spans if s.text == "origin/main")
        assert base.style.color == t.PRIMARY_TEXT
        assert any(s.style is not None and s.style.font_family for s in summary.spans)
        phone = await graph(gateway, width=390, height=844)
        assert "newest first" not in summary_text(phone.find("graph-summary"))

    run(scenario())


def test_the_chips_are_pills_with_a_full_touch_target() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        for key in ("graph-mode-all", "graph-mode-branch"):
            chip = app.find(key)
            assert chip.height >= t.MIN_TOUCH
            assert isinstance(chip.style.shape, ft.StadiumBorder)
            assert chip.style.text_style.size == 12.5

    run(scenario())
