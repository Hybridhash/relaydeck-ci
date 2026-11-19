"""Headings retain caller controls, wrapping, layout and supporting content."""

import json
from dataclasses import fields

import flet as ft
import pytest

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.headers import ScreenHeader, SectionHeader
from relaydeck.client.design_system.primitives import text_style
from relaydeck.devtools.catalog_model import THEMES, WIDTHS, Case, Theme
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import render_component, run


@pytest.mark.parametrize("no_wrap", [False, True])
@pytest.mark.parametrize("expand", [None, True])
@pytest.mark.parametrize("max_lines", [None, 1, 2])
def test_heading_retains_native_text_properties(
    no_wrap: bool, expand: bool | None, max_lines: int | None
) -> None:
    style = text_style("Bricolage Grotesque", T.TYPE_TITLE, T.TEXT, -0.02)
    props = dict(
        key="title",
        style=style,
        no_wrap=no_wrap,
        expand=expand,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS,
        tooltip="Full title",
    )
    heading, expected = ScreenHeader("Example", **props), ft.Text("Example", **props)
    assert isinstance(heading, ft.Semantics) and heading.header
    actual = heading.content
    assert isinstance(actual, ft.Text)
    for field in fields(expected):
        if not field.name.startswith("_"):
            assert getattr(actual, field.name) == getattr(expected, field.name), field.name


@pytest.mark.parametrize("kind", ["text", "button", "semantic"])
def test_preconfigured_heading_retains_its_control_and_properties(kind: str) -> None:
    control = (
        ft.Text("Example", key="title", expand=True, tooltip="Full title")
        if kind == "text"
        else ft.TextButton("Choose", key="choose", on_click=lambda event: None)
        if kind == "button"
        else ft.Semantics(label="Example", content=ft.Text("Example"))
    )
    before = {field.name: getattr(control, field.name) for field in fields(control)}
    heading = ScreenHeader(control)
    assert isinstance(heading, ft.Semantics) and heading.header
    assert heading.content is control
    for name in ("expand", "expand_loose", "col", "visible", "disabled"):
        assert getattr(heading, name) == getattr(control, name)
    assert {field.name: getattr(control, field.name) for field in fields(control)} == before


def test_section_heading_preserves_supporting_control_identity() -> None:
    count = ft.Text("2 of 2", key="count")
    control = SectionHeader(
        "EXAMPLE",
        "Title",
        key=None,
        title_key=None,
        expand=True,
        eyebrow_no_wrap=True,
        title_no_wrap=True,
        title_overflow=ft.TextOverflow.ELLIPSIS,
        after_title=[count],
    )
    assert control.key is None and control.expand
    assert control.controls[0].no_wrap
    heading = control.controls[1]
    assert isinstance(heading, ft.Semantics) and heading.header
    assert heading.content.key is None and heading.content.no_wrap
    assert heading.content.overflow == ft.TextOverflow.ELLIPSIS
    assert control.controls[2] is count


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_catalog_heading_text_remains_visible_and_named(width: int, theme: Theme) -> None:
    async def scenario() -> None:
        case = Case("screen-header", width, theme)
        app = await render(case)
        heading = app.find("sample-control")
        assert isinstance(heading, ft.Text)
        assert heading.visible and heading.value == case.label
        assert heading.semantics_label in (None, case.label)
        assert any(
            isinstance(node, ft.Semantics) and node.header and node.content is heading
            for node in controls(app)
        )

    run(scenario())


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("expand", [None, False, True, 2])
def test_heading_annotation_keeps_layout_and_state_on_both_controls(
    visible: bool, disabled: bool, expand: bool | int | None
) -> None:
    child = ft.Text(
        "Title",
        key="title",
        expand=expand,
        expand_loose=True,
        col={"sm": 6, "lg": 4},
        visible=visible,
        disabled=disabled,
    )
    heading = ScreenHeader(child)
    assert isinstance(heading, ft.Semantics) and heading.header
    assert heading.content is child
    assert child.key == "title"
    assert json.loads(heading.key) == ["heading", "str", "title"]
    for name in ("expand", "expand_loose", "col", "visible", "disabled"):
        assert getattr(heading, name) == getattr(child, name)
    assert child.expand == expand and child.visible is visible and child.disabled is disabled


def test_component_title_keeps_hook_state_and_native_identity_after_parent_update() -> None:
    @ft.component
    def Title() -> ft.Control:
        count, set_count = ft.use_state(0)
        return ScreenHeader(
            ft.TextButton(
                f"Count {count}",
                key="title-count",
                height=48,
                on_click=lambda event: set_count(lambda value: value + 1),
            )
        )

    @ft.component
    def View() -> ft.Column:
        tick, set_tick = ft.use_state(0)
        return ft.Column(
            controls=[
                ScreenHeader(Title()),
                ft.TextButton(
                    f"Refresh {tick}",
                    key="parent-refresh",
                    on_click=lambda event: set_tick(lambda value: value + 1),
                ),
            ]
        )

    async def scenario() -> None:
        app = await render_component(View)
        native_id = app.find("title-count")._i
        await app.set_props_and_fire("title-count", {}, "click")
        await app.set_props_and_fire("title-count", {}, "click")
        assert app.find("title-count").content == "Count 2"
        await app.set_props_and_fire("parent-refresh", {}, "click")
        assert app.find("title-count").content == "Count 2"
        assert app.find("title-count")._i == native_id

    run(scenario())
