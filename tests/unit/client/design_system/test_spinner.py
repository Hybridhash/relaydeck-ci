"""Native progress, accessibility labels and deterministic reduced-motion specimens."""

from dataclasses import fields

import flet as ft
import pytest

from relaydeck.client.design_system.loading_spinner import Spinner
from relaydeck.devtools.catalog_model import STATES, THEMES, WIDTHS, Case
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls


def test_spinner_retains_all_native_defaults() -> None:
    expected, actual = ft.ProgressRing(), Spinner()
    assert type(actual) is type(expected)
    assert actual.value is None
    for field in fields(ft.ProgressRing):
        if not field.name.startswith("_"):
            assert getattr(actual, field.name) == getattr(expected, field.name), field.name


@pytest.mark.parametrize("value", [None, 0.0, 0.5, 1.0])
def test_spinner_preserves_caller_progress_and_visibility(value) -> None:
    key = ft.ValueKey("progress")
    spinner = Spinner(key=key, value=value, visible=False, width=20, height=20, stroke_width=2)
    assert type(spinner) is ft.ProgressRing and spinner.key is key
    assert spinner.value == value and not spinner.visible
    assert spinner.width == 20 and spinner.height == 20 and spinner.stroke_width == 2


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", STATES)
def test_spinner_catalog_has_native_name_progress_and_static_motion(width, theme, state) -> None:
    async def scenario() -> None:
        case = Case("spinner", width, theme, state)
        app = await render(case)
        spinner = app.find("sample-control")
        assert type(spinner) is ft.ProgressRing
        assert spinner.semantics_label == case.label and spinner.semantics_value == 50
        assert spinner.value == 0.5
        assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        assert not any(
            isinstance(c, ft.Button | ft.TextButton | ft.TextField) for c in controls(app)
        )
        subject = app.find("catalog-subject")
        assert subject.disabled is (state == "disabled")
        if state == "disabled":
            assert not list(iter_controls([subject], active_only=True))
        if state in ("error", "loading"):
            assert app.find(f"catalog-{state}-context").live_region
        if state == "loading":
            loading = app.find("usage-loading-icon")
            assert loading.data == {"animated": False}

    run(scenario())
