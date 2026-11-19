"""Settings lays out inside its scroll column: nothing under it asks for a bounded height."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

from tests.support.host_harness import render_component, run
from tests.support.ui.navigation import _render_shell, _state
from tests.support.ui.unbounded_height import scroll_column_above, unbounded_height_violations

CASES = [
    pytest.param(1440, True, True, id="wide-dark"),
    pytest.param(1440, True, False, id="wide-light"),
    pytest.param(320, False, True, id="phone-dark"),
    pytest.param(390, False, False, id="phone-light"),
]


@pytest.mark.parametrize(("width", "wide", "dark"), CASES)
def test_settings_content_needs_no_bounded_height(width: int, wide: bool, dark: bool) -> None:
    async def scenario() -> list[str]:
        app = await _render_shell(
            _state(), destination="settings", viewport_width=width, is_wide=wide, dark=dark
        )
        await app.settle()
        assert app.find("host-home-mac") is not None
        return unbounded_height_violations(scroll_column_above(app, "hosts-list"))

    assert run(scenario()) == []


def _violations_of(*controls: Any) -> list[str]:
    @ft.component
    def Page() -> Any:
        return ft.Column(
            key="page",
            scroll=ft.ScrollMode.AUTO,
            controls=[ft.Container(key="inner", content=ft.Column(controls=list(controls)))],
        )

    async def scenario() -> list[str]:
        app = await render_component(Page)
        return unbounded_height_violations(scroll_column_above(app, "inner"))

    return run(scenario())


def test_the_check_flags_lists_scrolls_and_vertical_expand() -> None:
    assert _violations_of(ft.ListView(key="a", controls=[ft.Text("x")]))
    assert _violations_of(ft.Column(key="b", scroll=ft.ScrollMode.AUTO))
    assert _violations_of(ft.Column(key="c", expand=True, controls=[ft.Text("x")]))
    assert _violations_of(ft.Container(content=ft.Column(expand=True)))


def test_the_check_allows_plain_content_horizontal_expand_and_fixed_heights() -> None:
    assert _violations_of(ft.Column(controls=[ft.Text("x")])) == []
    assert _violations_of(ft.Row(controls=[ft.Column(expand=1, controls=[ft.Text("x")])])) == []
    assert _violations_of(ft.Container(height=200, content=ft.ListView(expand=True))) == []
