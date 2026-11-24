"""Existing setting controls retain native state, focus and controlled callbacks."""

import flet as ft
import pytest

from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls

ENTRIES = ("toggle-switch", "stepper", "segmented-switch")


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_setting_control_native_state_and_accessibility(entry, width, theme, state) -> None:
    async def scenario() -> None:
        case = Case(entry, width, theme, state)
        app = await render(case)
        subject = app.find("catalog-subject")
        assert subject.disabled is (state == "disabled")
        if entry == "toggle-switch":
            button = app.find("sample-control")
            assert isinstance(button, ft.TextButton)
            assert button.data == {"checked": state == "normal"}
            assert button.tooltip == f"{case.label}: {'on' if state == 'normal' else 'off'}"
            buttons = [button]
        elif entry == "stepper":
            assert isinstance(app.find("sample-control"), ft.Row)
            expected = case.label if state == "long-text" else "16"
            assert app.find("sample-control-value").value == expected
            buttons = [app.find("sample-control-minus"), app.find("sample-control-plus")]
            assert all(button.disabled is (state == "disabled") for button in buttons)
            assert [button.tooltip for button in buttons] == ["Smaller text", "Larger text"]
        else:
            assert isinstance(app.find("sample-control"), ft.Container)
            buttons = [app.find("sample-first"), app.find("sample-second")]
            assert buttons[0].data == {"selected": True} and buttons[0].disabled
            assert buttons[1].data == {"selected": False}
            assert buttons[1].disabled is (state == "disabled")
            for segment in buttons:
                assert isinstance(segment.content, ft.Container)
                assert isinstance(segment.content.content, ft.Text)
                assert isinstance(segment.content.content.style, ft.TextStyle)
        for button in buttons:
            assert isinstance(button, ft.TextButton)
            assert button.height >= 48
            assert button.style.side[ft.ControlState.FOCUSED].width >= 2
        if state == "disabled":
            assert not list(iter_controls([subject], active_only=True))
            assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state == "focus":
            assert any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region

    run(scenario())


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_setting_controls_apply_each_controlled_callback_once(width, theme) -> None:
    async def scenario() -> None:
        toggle = await render(Case("toggle-switch", width, theme))
        await toggle.set_props_and_fire("sample-control", {}, "click")
        assert toggle.find("sample-control").data == {"checked": False}
        await toggle.set_props_and_fire("sample-control", {}, "click")
        assert toggle.find("sample-control").data == {"checked": True}
        stepper = await render(Case("stepper", width, theme))
        await stepper.set_props_and_fire("sample-control-plus", {}, "click")
        assert stepper.find("sample-control-value").value == "17"
        await stepper.set_props_and_fire("sample-control-minus", {}, "click")
        assert stepper.find("sample-control-value").value == "16"
        switch = await render(Case("segmented-switch", width, theme))
        await switch.set_props_and_fire("sample-first", {}, "click")
        assert switch.find("sample-first").data == {"selected": True}
        await switch.set_props_and_fire("sample-second", {}, "click")
        assert switch.find("sample-second").data == {"selected": True}
        assert switch.find("sample-second").disabled
        assert not switch.find("sample-first").disabled

    run(scenario())
