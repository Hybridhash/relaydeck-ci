"""Mounted catalog checks for names, keyboard carriers and status semantics."""

from __future__ import annotations

import flet as ft
import pytest

from relaydeck.devtools.catalog_model import ENTRIES, Case
from tests.support.catalog.rendering import controls, render
from tests.support.host_harness import run
from tests.support.ui.focus import iter_controls


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry.id)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_catalog_action_names_and_keyboard_carriers(entry, theme) -> None:
    async def scenario() -> None:
        app = await render(Case(entry.id, theme=theme))
        actions = [
            node
            for node in controls(app)
            if any(
                callable(getattr(node, prop, None))
                for prop in ("on_click", "on_change", "on_select", "on_submit")
            )
        ]
        if entry.focusable:
            assert actions, entry.id
        for node in actions:
            assert isinstance(node, ft.Button | ft.TextButton | ft.TextField), entry.id
            assert node.key
            if isinstance(node, ft.TextField):
                assert node.hint_text == "Search examples"
            else:
                text = [
                    c.value for c in iter_controls([node]) if isinstance(c, ft.Text) and c.value
                ]
                assert node.tooltip or text, node.key
                assert node.height is None or node.height >= 48
                if isinstance(node, ft.TextButton) and node.style is not None:
                    assert node.style.side[ft.ControlState.FOCUSED].width >= 2
        if entry.id == "action-toast":
            toast = next(c for c in controls(app) if isinstance(c, ft.SnackBar))
            assert toast.action.label == "Retry"
            assert callable(toast.action.on_click)
            assert any(isinstance(c, ft.Semantics) and c.live_region for c in controls(app))
        if entry.id == "status-dot":
            assert app.find("dot-working").tooltip == "Working"
        if entry.id == "live-region":
            assert any(
                isinstance(c, ft.Semantics) and c.live_region and c.label == "Example label"
                for c in controls(app)
            )

    run(scenario())


@pytest.mark.parametrize(
    "entry", [entry for entry in ENTRIES if entry.focusable], ids=lambda e: e.id
)
def test_disabled_catalog_excludes_actions_from_focus_audit(entry) -> None:
    async def scenario() -> None:
        app = await render(Case(entry.id, state="disabled"))
        subject = app.find("catalog-subject")
        assert subject.disabled
        assert not list(iter_controls([subject], active_only=True))
        assert not any(getattr(m.body, "name", None) == "focus" for m in app.conn.sent)

    run(scenario())
