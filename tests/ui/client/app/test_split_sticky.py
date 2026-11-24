"""Typing mode on touch devices outlives the draft's focus.

Send, blur, terminal and scroll taps keep the split; Done, route change, rotation to
portrait and losing control end it. Desktop never enters the split. The field stays the
same control throughout (no remount).
"""

from __future__ import annotations

import asyncio
from typing import Any

from relaydeck.client.app.hooks.support import TYPING_BLUR_DEBOUNCE_S
from relaydeck.client.app.navigation import RouteBridge
from tests.support.host_harness import run
from tests.support.ui.retained_root import _active, _open, _render

DRAFT = "composer-draft"


def _split(app: Any) -> bool:
    return app.find("rail-slot").visible is False


async def _typing(bridge: RouteBridge, *, touch: bool = True) -> tuple[Any, Any, Any]:
    app, controller, sessions, pool, now = await _render(bridge=bridge, touch=touch)
    await _open(app, "home-mac:term-1")
    await app.settle()
    await app.set_props_and_fire(DRAFT, {}, "focus")
    await app.settle()
    return app, controller, sessions


def _landscape() -> RouteBridge:
    return RouteBridge(route="/", width=1376, height=396)


async def _blur(app: Any) -> None:
    await app.set_props_and_fire(DRAFT, {}, "blur")
    await asyncio.sleep(TYPING_BLUR_DEBOUNCE_S + 0.2)
    await app.settle()


def test_blur_keeps_the_split_on_touch_devices() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        assert _split(app)
        await _blur(app)
        assert _split(app)

    run(scenario())


def test_terminal_taps_keep_the_split_while_composing() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        await _blur(app)
        # The scroll overlay is used without leaving typing mode.
        await app.set_props_and_fire("terminal-scroll-up", {}, "click")
        await app.settle()
        assert _split(app)

    run(scenario())


def test_done_leaves_typing_mode() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        await app.set_props_and_fire("agent-typing-done", {}, "click")
        await app.settle()
        assert not _split(app)
        await app.set_props_and_fire(DRAFT, {}, "focus")
        await app.settle()
        assert _split(app)

    run(scenario())


def test_send_keeps_typing_mode() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        await app.set_props_and_fire(DRAFT, {}, "change", "hello")
        await app.settle()
        await app.set_props_and_fire("composer-send", {}, "click")
        await app.settle()
        assert _split(app)

    run(scenario())


def test_route_change_leaves_typing_mode() -> None:
    async def scenario() -> None:
        bridge = _landscape()
        app, _c, _s = await _typing(bridge)
        bridge.set_route("/hosts")
        await app.settle()
        assert not _split(app)

    run(scenario())


def test_rotation_to_portrait_leaves_typing_mode_on_touch_devices() -> None:
    async def scenario() -> None:
        bridge = _landscape()
        app, _c, _s = await _typing(bridge)
        bridge.set_size(820, 1180)  # portrait tablet
        await app.settle()
        assert not _split(app)

    run(scenario())


def test_losing_control_leaves_typing_mode() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        await app.set_props_and_fire("agent-disconnect", {}, "click")
        await app.settle()
        assert not _split(app)

    run(scenario())


def test_desktop_never_enters_the_split_and_keeps_its_blur_exit() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape(), touch=False)
        assert not _split(app)

    run(scenario())


def test_sticky_blur_keeps_the_editor_and_explicit_exit_retires_it() -> None:
    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        field = _active(app, DRAFT)._i
        await _blur(app)
        assert _active(app, DRAFT)._i == field
        draft = _active(app, DRAFT).value
        await app.set_props_and_fire("agent-typing-done", {}, "click")
        await app.settle()
        restored = _active(app, DRAFT)
        assert restored._i != field and field not in app.session.index
        assert restored.value == draft and not restored.autofocus
        assert restored.can_request_focus and not restored.disabled

    run(scenario())


def test_attach_and_the_native_picker_round_trip_keep_the_split() -> None:
    """Tapping attach opens the native photo picker; the field
    blurs while it is up and refocuses when it returns. Typing mode stays on."""

    async def scenario() -> None:
        app, _c, _s = await _typing(_landscape())
        assert _split(app)
        await app.set_props_and_fire("composer-attach-picture", {}, "click")
        await app.settle()
        await _blur(app)  # the picker takes focus
        assert _split(app)
        await app.set_props_and_fire(DRAFT, {}, "focus")  # the picker returns
        await app.settle()
        assert _split(app)

    run(scenario())
