"""Button dispatch preserves native class, defaults, identity and action delivery."""

from dataclasses import fields

import flet as ft
import pytest

from relaydeck.client.design_system.buttons import Button
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls

NATIVE = {"standard": ft.Button, "text": ft.TextButton, "filled": ft.FilledButton}


@pytest.mark.parametrize("variant", NATIVE)
def test_button_retains_native_class_and_defaults(variant) -> None:
    expected, actual = NATIVE[variant](), Button(variant=variant)
    assert type(actual) is type(expected)
    for field in fields(type(expected)):
        if not field.name.startswith("_"):
            assert getattr(actual, field.name) == getattr(expected, field.name), field.name


@pytest.mark.parametrize("variant", NATIVE)
def test_button_preserves_content_key_style_and_handler_identity(variant) -> None:
    content = ft.Row(controls=[ft.Text("Example")])
    key = ft.ValueKey("button")
    style = ft.ButtonStyle(padding=0)

    async def action(event) -> None:
        pass

    button = Button(
        content,
        variant=variant,
        key=key,
        style=style,
        on_click=action,
        disabled=True,
        height=48,
        data={"selected": True},
    )
    assert type(button) is NATIVE[variant]
    assert button.content is content and button.key is key and button.style is style
    assert button.on_click is action and button.disabled and button.height == 48
    assert button.data == {"selected": True}


@pytest.mark.parametrize("variant", ["standard", "filled"])
def test_button_preserves_native_legacy_color_conversion(variant) -> None:
    expected = NATIVE[variant]("Example", color="#ffffff", bgcolor="#000000")
    actual = Button("Example", variant=variant, color="#ffffff", bgcolor="#000000")
    assert type(actual) is type(expected)
    assert actual.style == expected.style


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_button_catalog_native_actions_focus_and_disabled_state(width, theme, state) -> None:
    async def scenario() -> None:
        case = Case("button", width, theme, state)
        app = await render(case)
        for index, (variant, native) in enumerate(NATIVE.items(), 1):
            button = app.find(f"sample-{variant}")
            assert type(button) is native
            assert button.content == case.label and button.tooltip == case.label
            assert button.height >= 48 and button.disabled is (state == "disabled")
            assert button.style.side[ft.ControlState.FOCUSED].width >= 2
            if state != "disabled":
                await app.set_props_and_fire(f"sample-{variant}", {}, "click")
                assert app.find("catalog-actions").value == f"Example actions: {index}"
        if state == "disabled":
            assert not list(iter_controls([app.find("catalog-subject")], active_only=True))
            assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state == "focus":
            assert any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region

    run(scenario())
