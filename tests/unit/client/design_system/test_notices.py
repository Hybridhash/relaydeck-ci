"""Notice frames preserve readable content and native keyboard actions."""

import flet as ft
import pytest

from relaydeck.client.design_system.notices import Notice
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls


def test_notice_preserves_content_key_and_frame_objects() -> None:
    child = ft.Semantics(label="Example message", live_region=True, content=ft.Text("Example"))
    key = ft.ValueKey("notice")
    padding = ft.Padding.all(16)
    margin = ft.Margin.only(top=12)
    border = ft.Border.all(1, "#ffffff")
    notice = Notice(
        content=child,
        key=key,
        padding=padding,
        margin=margin,
        border=border,
        border_radius=12,
        bgcolor="#000000",
        expand=True,
        alignment=ft.Alignment.CENTER,
    )
    assert type(notice) is ft.Container
    assert notice.content is child and notice.key is key
    assert notice.padding is padding and notice.margin is margin and notice.border is border
    assert notice.expand is True and notice.alignment == ft.Alignment.CENTER
    assert child.live_region and notice.on_click is None


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_notice_catalog_preserves_context_and_keyboard_action(width, theme, state) -> None:
    async def scenario() -> None:
        case = Case("notice", width, theme, state)
        app = await render(case)
        notice = app.find("sample-control")
        assert notice.content.controls[0].value == case.label
        button = app.find("sample-action")
        assert isinstance(button, ft.TextButton)
        assert button.tooltip == "Continue" and button.height >= 48
        assert button.style.side[ft.ControlState.FOCUSED].width >= 2
        subject = app.find("catalog-subject")
        assert subject.disabled is (state == "disabled")
        if state == "disabled":
            assert not list(iter_controls([subject], active_only=True))
            assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        else:
            await app.set_props_and_fire("sample-action", {}, "click")
            assert app.find("catalog-actions").value == "Example actions: 1"
        if state == "focus":
            assert any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region

    run(scenario())
