"""Mounted sheet specimens retain named actions and dismissal callbacks."""

import flet as ft
import pytest

from relaydeck.devtools.catalog_model import THEMES, WIDTHS, Case, Theme
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import run


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_sheet_actions_and_dismissal_remain_accessible(width: int, theme: Theme) -> None:
    async def scenario() -> None:
        case = Case("sheet", width, theme, "focus")
        app = await render(case)
        sheet = app.find("sample-control")
        assert isinstance(sheet, ft.BottomSheet) and sheet.open
        assert sheet.content.content.label == "Catalog specimen " + case.key
        assert any(isinstance(node, ft.Text) and node.value == case.label for node in controls(app))
        button = app.find("sample-action")
        assert isinstance(button, ft.TextButton)
        assert button.tooltip or button.content
        assert button.height is not None and button.height >= 48
        assert any(getattr(message.body, "name", None) == "focus" for message in app.conn.sent)
        await app.set_props_and_fire("sample-action", {}, "click")
        assert app.find("catalog-actions").value == "Example actions: 1"
        await app.set_props_and_fire("sample-control", {}, "dismiss")
        assert app.find("catalog-actions").value == "Example actions: 2"

    run(scenario())


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_disabled_sheet_action_is_disabled_inside_the_sheet(width: int, theme: Theme) -> None:
    async def scenario() -> None:
        app = await render(Case("sheet", width, theme, "disabled"))
        button = app.find("sample-action")
        assert button.disabled
        assert not any(getattr(message.body, "name", None) == "focus" for message in app.conn.sent)

    run(scenario())
