"""Heading annotations keep keyed owners and rendered flex across tree updates."""

from typing import Any

import flet as ft
import pytest
from flet.components.component import Component

from relaydeck.client.design_system.headers import ScreenHeader
from tests.support.host_harness import render_component, run


@pytest.mark.parametrize("operation", ["reorder", "insert"])
@pytest.mark.parametrize("carrier", ["native", "component"])
def test_keyed_heading_keeps_state_and_native_ids(operation: str, carrier: str) -> None:
    @ft.component
    def Title(name: str) -> ft.Control:
        count, set_count = ft.use_state(0)
        return ScreenHeader(
            ft.TextButton(
                f"{name}:{count}",
                key="counter-" + name,
                on_click=lambda event: set_count(lambda value: value + 1),
            )
        )

    @ft.component
    def View() -> ft.Column:
        changed, set_changed = ft.use_state(False)
        counts, set_counts = ft.use_state({"A": 0, "B": 0, "C": 0})
        names = (
            (["B", "A"] if operation == "reorder" else ["C", "A", "B"]) if changed else ["A", "B"]
        )

        def native(name: str) -> ft.Control:
            return ScreenHeader(
                ft.TextButton(
                    f"{name}:{counts[name]}",
                    key="counter-" + name,
                    on_click=lambda event: set_counts(
                        lambda values: {**values, name: values[name] + 1}
                    ),
                )
            )

        return ft.Column(
            controls=[
                *[
                    ScreenHeader(Title(name, key=name)) if carrier == "component" else native(name)
                    for name in names
                ],
                ft.TextButton("Change", key="change", on_click=lambda event: set_changed(True)),
            ]
        )

    async def scenario() -> None:
        app = await render_component(View)
        original = {name: app.find("counter-" + name)._i for name in ("A", "B")}
        await app.set_props_and_fire("counter-A", {}, "click")
        assert app.find("counter-A").content == "A:1"
        await app.set_props_and_fire("change", {}, "click")
        assert app.find("counter-A").content == "A:1"
        assert app.find("counter-B").content == "B:0"
        for name in ("A", "B"):
            assert app.find("counter-" + name)._i == original[name]

    run(scenario())


def test_heading_keys_keep_original_key_types_distinct() -> None:
    numeric = ScreenHeader(ft.Text("Number", key=1))
    textual = ScreenHeader(ft.Text("Text", key="1"))
    assert numeric.key != textual.key
    assert numeric.content.key == 1 and textual.content.key == "1"


def _native_parent(control: Any) -> Any:
    parent = control._parent()
    while isinstance(parent, Component):
        parent = parent._parent()
    return parent


def test_component_native_heading_keeps_parent_flex_and_updates() -> None:
    @ft.component
    def Title(flex: int) -> ft.Control:
        return ScreenHeader(ft.Text("Long title", key="flex-title", expand=flex, expand_loose=True))

    @ft.component
    def View() -> ft.Row:
        flex, set_flex = ft.use_state(1)
        return ft.Row(
            controls=[
                ScreenHeader(Title(flex, key="title-owner")),
                ft.TextButton("Change", key="change", on_click=lambda event: set_flex(2)),
            ]
        )

    async def scenario() -> None:
        app = await render_component(View)
        native_id = app.find("flex-title")._i
        for expected in (1, 2):
            child = app.find("flex-title")
            annotation = _native_parent(child)
            assert isinstance(annotation, ft.Semantics) and annotation.header
            assert annotation.expand == child.expand == expected
            assert annotation.expand_loose and child.expand_loose
            host = _native_parent(annotation)
            assert isinstance(host, ft.Row) and host._internals["host_expanded"]
            assert child._i == native_id
            if expected == 1:
                await app.set_props_and_fire("change", {}, "click")

    run(scenario())


@pytest.mark.parametrize("operation", ["reorder", "insert"])
@pytest.mark.parametrize("key_type", [ft.ValueKey, ft.ScrollKey], ids=["value", "scroll"])
def test_wrapped_heading_keys_keep_native_ids(operation: str, key_type: Any) -> None:
    from tests.support.catalog.rendering import controls

    @ft.component
    def View() -> ft.Column:
        changed, set_changed = ft.use_state(False)
        order = [("A", 1), ("B", "1")]
        if changed:
            order = list(reversed(order)) if operation == "reorder" else [("C", "new"), *order]
        return ft.Column(
            controls=[
                *[ScreenHeader(ft.Text(name, key=key_type(value))) for name, value in order],
                ft.TextButton("Change", key="change", on_click=lambda event: set_changed(True)),
            ]
        )

    async def scenario() -> None:
        app = await render_component(View)

        def ids() -> dict[str, int]:
            return {
                node.value: node._i
                for node in controls(app)
                if isinstance(node, ft.Text) and node.value in ("A", "B")
            }

        before = ids()
        await app.set_props_and_fire("change", {}, "click")
        assert ids() == before

    run(scenario())


@pytest.mark.parametrize("key_type", [ft.ValueKey, ft.ScrollKey], ids=["value", "scroll"])
@pytest.mark.parametrize("value", [False, True, 0, 1, -1, 0.5, 1.0, "", "1", "True", "مثال"])
def test_wrapped_heading_key_uses_the_sdk_effective_value(key_type: Any, value: Any) -> None:
    raw = ScreenHeader(ft.Text("Raw", key=value))
    wrapped = ScreenHeader(ft.Text("Wrapped", key=key_type(value)))
    assert raw.key == wrapped.key
    assert wrapped.content.key.value == value


@pytest.mark.parametrize("left,right", [(True, 1), (False, 0), (1, 1.0), (-2, -2.0)])
def test_heading_keys_retain_numeric_sdk_equality(left: Any, right: Any) -> None:
    assert (
        ScreenHeader(ft.Text("Left", key=left)).key == ScreenHeader(ft.Text("Right", key=right)).key
    )


def test_heading_keys_retain_special_float_identity() -> None:
    positive = ScreenHeader(ft.Text("Positive", key=float("inf")))
    negative = ScreenHeader(ft.Text("Negative", key=float("-inf")))
    assert positive.key != negative.key
    value = float("nan")
    assert (
        ScreenHeader(ft.Text("First", key=value)).key
        == ScreenHeader(ft.Text("Again", key=value)).key
    )
    assert (
        ScreenHeader(ft.Text("Other", key=float("nan"))).key
        != ScreenHeader(ft.Text("Again", key=value)).key
    )
