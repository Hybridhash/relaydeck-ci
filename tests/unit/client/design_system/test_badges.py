"""Decorative frames retain caller ownership and native presentation."""

import flet as ft
import pytest

from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.theme import palette
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import render_component, run
from tests.support.ui.badge_cases import badge_view


@pytest.mark.parametrize("radius", [6, ft.BorderRadius.all(11)])
def test_badge_keeps_content_and_frame_objects(radius: ft.BorderRadiusValue) -> None:
    text = ft.Text("Example", key="text")
    padding = ft.Padding.symmetric(horizontal=6)
    border = ft.Border.all(1, "#ffffff")
    frame = Badge(
        content=text,
        key="frame",
        padding=padding,
        border_radius=radius,
        bgcolor="#000000",
        border=border,
        height=18,
        width=60,
    )
    assert type(frame) is ft.Container
    assert frame.content is text and text.key == "text"
    assert frame.padding is padding and frame.border is border
    assert frame.border_radius is radius
    assert frame.key == "frame" and frame.height == 18 and frame.width == 60
    assert frame.on_click is None and frame.alignment is None


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_badge_catalog_is_named_decorative_and_inherits_context(width, theme, state) -> None:
    async def scenario() -> None:
        case = Case("badge", width, theme, state)
        app = await render(case)
        frame = app.find("sample-control")
        assert isinstance(frame, ft.Container)
        assert frame.content.value == case.label
        assert frame.content.color == palette(case.dark).text
        assert frame.on_click is None
        assert app.find("catalog-subject").disabled is (state == "disabled")
        assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region
        assert not any(isinstance(node, ft.ProgressRing) for node in controls(app))

    run(scenario())


def test_host_chip_keeps_keyboard_carrier_and_selected_callback() -> None:
    async def scenario() -> None:
        selected: list[str] = []
        from relaydeck.client.widgets.sessions.pills import HostChip

        app = await render_component(HostChip, "host-a", "Example host", True, selected.append)
        button = app.find("host-chip-host-a")
        assert isinstance(button, ft.TextButton)
        assert button.height >= 48 and button.tooltip == "Example host"
        assert button.style.side[ft.ControlState.FOCUSED].width >= 2
        await app.set_props_and_fire("host-chip-host-a", {}, "click")
        assert selected == ["host-a"]

    run(scenario())


def test_disabled_suggestion_chips_keep_disabled_keyboard_carriers() -> None:
    async def scenario() -> None:
        app = await render_component(badge_view, "suggestions-disabled")
        for index in range(2):
            button = app.find(f"agent-rename-suggestion-{index}")
            assert isinstance(button, ft.TextButton) and button.disabled
            assert button.height == 44
            assert button.style.side[ft.ControlState.FOCUSED].width == 2
            assert button.content.height == 36

    run(scenario())
