"""Native editors preserve SDK defaults, ownership refs and keyboard contracts."""

from dataclasses import fields

import flet as ft
import pytest
from flet.components.hooks.use_ref import MutableRef

from relaydeck.client.design_system.inputs import SearchField, TextInput
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls


@pytest.mark.parametrize("builder", [TextInput, SearchField])
def test_input_retains_native_defaults(builder) -> None:
    expected, actual = ft.TextField(), builder()
    assert type(actual) is type(expected)
    for field in fields(ft.TextField):
        if not field.name.startswith("_"):
            assert getattr(actual, field.name) == getattr(expected, field.name), field.name


@pytest.mark.parametrize("builder", [TextInput, SearchField])
@pytest.mark.parametrize("carrier", [ft.Ref, MutableRef])
def test_input_keeps_refs_selection_and_handler_objects(builder, carrier) -> None:
    owner = carrier()
    selection = ft.TextSelection(base_offset=1, extent_offset=3)
    style = ft.TextStyle(size=16)
    key = ft.ValueKey("editor")

    def change(event) -> None:
        pass

    field = builder(
        ref=owner,
        key=key,
        value="Example",
        selection=selection,
        text_style=style,
        on_change=change,
        can_request_focus=False,
        disabled=True,
    )
    assert type(field) is ft.TextField
    assert owner.current is field and field.key is key
    assert field.selection is selection and field.text_style is style
    assert field.on_change is change
    assert field.value == "Example" and field.disabled and not field.can_request_focus


@pytest.mark.parametrize("entry", ["text-input", "search-field"])
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_input_catalog_keeps_native_focus_selection_and_changes(entry, width, theme, state) -> None:
    async def scenario() -> None:
        case = Case(entry, width, theme, state)
        app = await render(case)
        field = app.find("sample-control")
        assert type(field) is ft.TextField
        assert field.label == case.label and field.value == case.label
        assert field.size_constraints.min_height >= 48
        assert field.disabled is (state == "disabled")
        if state == "disabled":
            assert not list(iter_controls([app.find("catalog-subject")], active_only=True))
            assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        else:
            identity = field._i
            await app.set_props_and_fire("sample-control", {"value": "Changed"}, "change")
            assert app.find("sample-control").value == "Changed"
            assert app.find("sample-control")._i == identity
            await app.set_props_and_fire("sample-control", {}, "submit")
            assert app.find("catalog-actions").value == "Example actions: 1"
        if state == "focus":
            assert any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if state == "error":
            assert field.error == "Example validation error"
        if state == "loading":
            assert app.find("catalog-loading-context").live_region

    run(scenario())
