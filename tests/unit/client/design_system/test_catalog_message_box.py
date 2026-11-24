"""Message placeholders retain text semantics and keyboard actions."""

from __future__ import annotations

import flet as ft
import pytest

from relaydeck.devtools.catalog_model import THEMES, WIDTHS, Case, Theme
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import run


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_message_box_text_and_action_remain_accessible(width: int, theme: Theme) -> None:
    async def scenario() -> None:
        case = Case("message-box", width, theme)
        app = await render(case)
        message = next(
            node for node in controls(app) if isinstance(node, ft.Text) and node.value == case.label
        )
        assert message.text_align == ft.TextAlign.CENTER
        assert message.visible
        assert message.semantics_label in (None, case.label)
        action = app.find("sample-action")
        assert isinstance(action, ft.TextButton)
        assert action.tooltip == "Continue"
        assert action.height is not None and action.height >= 48
        await app.set_props_and_fire("sample-action", {}, "click")
        assert app.find("catalog-actions").value == "Example actions: 1"

    run(scenario())
