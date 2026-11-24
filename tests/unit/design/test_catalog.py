"""Every catalog case mounts existing controls without a product lifetime."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

from relaydeck.devtools.catalog import Catalog, main
from relaydeck.devtools.catalog_model import ENTRIES, LONG_TEXT, STATES, THEMES, WIDTHS, Case, cases
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import render_component, run
from tests.support.ui.snapshot import snapshot_json


@pytest.mark.parametrize("case", cases(), ids=lambda case: case.key)
def test_catalog_case_renders(case: Case) -> None:
    async def scenario() -> None:
        app = await render(case)
        preview = app.find(f"catalog-preview-{case.key}")
        assert preview.width == case.width
        assert preview.theme_mode == (ft.ThemeMode.DARK if case.dark else ft.ThemeMode.LIGHT)
        assert app.find("catalog-title").value == case.definition.symbol
        assert app.find("catalog-subject").disabled is (case.state == "disabled")
        if case.state == "error":
            if case.entry == "filter-field":
                assert app.find("sample-control").error_text == "Example validation error"
            elif case.entry in ("text-input", "search-field"):
                assert app.find("sample-control").error == "Example validation error"
            elif case.entry == "action-toast":
                assert any(
                    isinstance(c, ft.Icon) and c.icon == ft.Icons.ERROR_OUTLINE
                    for c in controls(app)
                )
            else:
                assert app.find("catalog-error-context").live_region
        if case.state == "loading" and case.entry != "loading-bars":
            assert app.find("catalog-loading-context").label == "Loading example"
        if case.state == "focus" and case.definition.focusable:
            assert any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)
        if case.state == "long-text":
            text = snapshot_json(app).casefold()
            if case.entry == "count-tile":
                assert "123456789012345" in text
            elif case.entry in ("loading-bars", "status-dot"):
                assert "No text-bearing property" in case.note
            else:
                assert LONG_TEXT.casefold() in text

    run(scenario())


def test_matrix_is_complete_and_case_ids_are_stable() -> None:
    matrix = cases()
    assert len(ENTRIES) == 40
    assert ENTRIES[0].id == "state-pill"
    assert WIDTHS == (320, 768, 1440)
    assert set(THEMES) == {"light", "dark"}
    assert len(matrix) == 40 * 3 * 2 * 6
    assert len({case.key for case in matrix}) == len(matrix)
    for entry in ENTRIES:
        assert {c.state for c in matrix if c.entry == entry.id} == set(STATES)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entry": "missing"},
        {"entry": "state-pill", "width": 319},
        {"entry": "state-pill", "state": "missing"},
        {"entry": "state-pill", "theme": "missing"},
    ],
)
def test_invalid_catalog_requests_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Case(**kwargs)


def test_listing_never_starts_a_server(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Listing must not start a renderer")

    monkeypatch.setattr(ft, "run", forbidden)
    assert main(["--list"]) == 0
    assert capsys.readouterr().out.splitlines() == [case.key for case in cases()]


def test_catalog_selectors_and_callbacks_use_only_local_state() -> None:
    async def scenario() -> None:
        app = await render_component(Catalog, Case("filter-field"))
        await app.set_props_and_fire("sample-control", {"value": "Typed example"}, "change")
        assert app.find("sample-control").value == "Typed example"
        await app.set_props_and_fire("catalog-entry", {"value": "toggle-switch"}, "select")
        assert app.find("catalog-title").value == "ToggleSwitch"
        await app.set_props_and_fire("catalog-width", {"value": "768"}, "select")
        await app.set_props_and_fire("catalog-theme", {"value": "dark"}, "select")
        await app.set_props_and_fire("catalog-state", {"value": "disabled"}, "select")
        assert app.find("catalog-preview-toggle-switch-768-dark-disabled").width == 768
        assert app.find("catalog-subject").disabled

    run(scenario())
