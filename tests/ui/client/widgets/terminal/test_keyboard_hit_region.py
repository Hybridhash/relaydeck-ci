"""The padded keyboard button remains one native action in the real input dock."""

from pathlib import Path

import flet as ft
import pytest

import relaydeck.client.widgets.terminal.direct_typing as direct_typing
from tests.support.host_harness import run
from tests.support.terminal.direct_typing import _direct
from tests.support.terminal.latch_text import _inputs
from tests.support.ui.focus import iter_controls
from tests.support.ui.phone_terminal import _ancestry, _shape


@pytest.mark.parametrize("tier", ["compact", "medium"])
def test_padded_keyboard_toggle_keeps_one_action_and_retained_owners(
    tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(direct_typing, "_typing_now", lambda: now[0])

    async def scenario() -> None:
        app, session, socket, focuses = await _direct(False, tier)
        try:
            button = app.find("agent-workflow-toggle")
            semantics = app.find("agent-keyboard-semantics")
            row = app.find("agent-tab-row")
            assert isinstance(button, ft.IconButton)
            assert button.width == button.height == row.height == 48
            assert button.padding == 16 and button.icon_size == 16
            assert semantics.content is button and semantics.button
            assert semantics.label == "Terminal keyboard" and semantics.selected
            assert row.controls[-1] is semantics and row.spacing == 4
            assert row.controls[0].expand and row.controls[0].height == 36
            assert _ancestry(app, button)[-3:] == (row, semantics, button)
            roots = app.page.views[0].controls
            controls = list(iter_controls(roots if isinstance(roots, list) else [roots]))
            assert [control for control in controls if control.key == button.key] == [button]
            assert [control for control in controls if control.key == semantics.key] == [semantics]
            terminal, controller = session.current_term, session.controller
            shape = _shape(_ancestry(app, terminal))
            callback = button.on_click
            # The native IconButton routes both padding and glyph through this one event.
            now[0] += 0.5
            await app.session.dispatch_event(button._i, "click", "")
            await app.settle()
            assert not app.find(button.key).selected
            assert app.find(button.key)._i == button._i
            assert _shape(_ancestry(app, terminal)) == shape
            assert session.current_term is terminal and session.controller is controller
            assert session.store.draft == "retained compose draft"
            count = len(focuses)
            now[0] += 0.5
            await app.session.dispatch_event(button._i, "click", "")
            await app.settle()
            assert app.find(button.key).selected and len(focuses) == count + 1
            await session.disconnect()
            await app.settle()
            assert app.find(button.key).disabled
            count, sent = len(focuses), list(_inputs(socket))
            now[0] += 0.5
            await callback(None)
            await app.settle()
            assert len(focuses) == count and _inputs(socket) == sent
            assert session.current_term is terminal and session.controller is controller
            assert session.store.draft == "retained compose draft"
        finally:
            await session.dispose()

    run(scenario())


def test_native_icon_button_keeps_padding_inside_the_disabled_gated_action() -> None:
    source = (
        Path(__file__).resolve().parents[5]
        / "extensions/terminal/third_party/flet-1.0.3-rd1/lib/src/controls/icon_button.dart"
    ).read_text()
    assert 'var padding = widget.control.getEdgeInsets("padding");' in source
    assert 'var iconSize = widget.control.getDouble("icon_size");' in source
    assert "Function()? onPressed = !widget.control.disabled" in source
    assert 'widget.control.triggerEvent("click");' in source
    variants = (
        "IconButton(",
        "IconButton.filled(",
        "IconButton.filledTonal(",
        "IconButton.outlined(",
    )
    for start in variants:
        block = source.split("button = " + start, 1)[1].split("onPressed: onPressed);", 1)[0]
        assert "padding: padding," in block and "iconSize: iconSize," in block
