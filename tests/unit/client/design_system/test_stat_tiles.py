"""Metric tiles preserve native content, state and layout contracts."""

import flet as ft
import pytest

from relaydeck.client.design_system.stat_tiles import StatTile
from relaydeck.client.design_system.theme import palette
from relaydeck.client.screens.usage.parts.primitives import MatchedRow
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import render_component, run


@pytest.mark.parametrize("key", ["metric", 1, ft.ValueKey("metric"), ft.ScrollKey("metric")])
def test_stat_tile_preserves_native_key_and_supplied_objects(key) -> None:
    content = ft.Row(controls=[ft.Text("123")])
    padding = ft.Padding.all(12)
    radius = ft.BorderRadius.all(12)
    border = ft.Border.all(1, "#ffffff")
    tile = StatTile(
        content=content,
        key=key,
        padding=padding,
        border_radius=radius,
        bgcolor="#000000",
        border=border,
        expand=2,
        height=52,
        alignment=ft.Alignment.CENTER_LEFT,
    )
    assert type(tile) is ft.Container
    assert tile.key is key and tile.content is content
    assert tile.padding is padding and tile.border_radius is radius and tile.border is border
    assert tile.expand == 2 and tile.height == 52 and tile.alignment == ft.Alignment.CENTER_LEFT


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_stat_tile_catalog_keeps_visible_values_and_context(width, theme, state) -> None:
    async def scenario() -> None:
        case = Case("stat-tile", width, theme, state)
        app = await render(case)
        tile = app.find("sample-control")
        assert tile.content.controls[0].value == case.label
        assert tile.content.controls[1].value == "123"
        assert tile.content.controls[1].color == palette(case.dark).text
        assert app.find("catalog-subject").disabled is (state == "disabled")
        assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region
        assert not any(isinstance(node, ft.ProgressRing) for node in controls(app))

    run(scenario())


def test_matched_metric_cards_keep_optional_native_frame_properties() -> None:
    async def scenario() -> None:
        key = ft.ValueKey("metric")
        original = ft.Container(key=key, content=ft.Column(controls=[ft.Text("123")]))
        app = await render_component(MatchedRow, cards=[original, None], expands=[2, 1])
        clone = next(
            node for node in controls(app) if isinstance(node, ft.Container) and node.key == key
        )
        assert clone is not original
        assert clone.expand == 2
        assert clone.padding is None and clone.border_radius is None
        assert clone.border is None and clone.bgcolor is None
        assert original.content.controls[0].value == "123"
        assert original.expand is None

    run(scenario())
