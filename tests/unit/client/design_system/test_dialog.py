"""Dialog slots retain caller ownership and native dismissal behavior."""

from dataclasses import fields

import flet as ft
import pytest

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.dialogs import Dialog
from relaydeck.devtools.catalog_model import THEMES, WIDTHS, Case, Theme
from tests.support.catalog.rendering import render
from tests.support.host_harness import run


@pytest.mark.parametrize("modal", [False, True])
@pytest.mark.parametrize("open", [False, True])
@pytest.mark.parametrize("radius", [None, 0, 16, 24])
@pytest.mark.parametrize("background", [None, T.CARD])
def test_dialog_retains_native_slots_and_flags(
    modal: bool, open: bool, radius: int | None, background: str | None
) -> None:
    content, title, button = ft.Text("Body"), ft.Text("Title"), ft.TextButton("Continue")
    actions = [button]

    def dismiss(event: object) -> None:
        pass

    actual = Dialog(
        key="example-dialog",
        content=content,
        title=title,
        actions=actions,
        open=open,
        modal=modal,
        bgcolor=background,
        radius=radius,
        content_padding=0,
        on_dismiss=dismiss,
    )
    expected = ft.AlertDialog(
        key="example-dialog",
        content=content,
        title=title,
        actions=actions,
        open=open,
        modal=modal,
        bgcolor=background,
        shape=ft.RoundedRectangleBorder(radius=radius) if radius is not None else None,
        content_padding=0,
        on_dismiss=dismiss,
    )
    for field in fields(expected):
        if not field.name.startswith("_"):
            value = getattr(actual, field.name)
            if field.name == "title":
                assert isinstance(value, ft.Semantics) and value.header
                value = value.content
            assert value == getattr(expected, field.name), field.name
    assert actual.content is content and actual.title.content is title
    assert actual.actions[0] is button and actual.on_dismiss is dismiss


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_dialog_actions_and_dismissal_are_accessible(width: int, theme: Theme) -> None:
    async def scenario() -> None:
        case = Case("dialog", width, theme, "focus")
        app = await render(case)
        dialog = app.find("sample-control")
        assert isinstance(dialog, ft.AlertDialog) and dialog.open
        assert isinstance(dialog.title, ft.Semantics) and dialog.title.header
        assert dialog.title.content.value == case.label
        assert dialog.content.label == "Catalog specimen " + case.key
        assert app.find("sample-action").height >= 48
        assert any(getattr(message.body, "name", None) == "focus" for message in app.conn.sent)
        await app.set_props_and_fire("sample-action", {}, "click")
        assert app.find("catalog-actions").value == "Example actions: 1"
        await app.set_props_and_fire("sample-control", {}, "dismiss")
        assert app.find("catalog-actions").value == "Example actions: 2"

    run(scenario())


@pytest.mark.parametrize("theme", THEMES)
def test_disabled_dialog_action_is_disabled_in_its_native_slot(theme: Theme) -> None:
    async def scenario() -> None:
        app = await render(Case("dialog", theme=theme, state="disabled"))
        assert app.find("sample-action").disabled
        assert not any(getattr(message.body, "name", None) == "focus" for message in app.conn.sent)

    run(scenario())
