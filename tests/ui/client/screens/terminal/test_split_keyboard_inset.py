"""Typing split on a browser that leaves the keyboard over the page: the terminal host and the
typing column stop above the measured inset, less whatever the page already removed."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.features.terminal.layout_budget import (
    FIELD_LINE_HEIGHT,
    TYPING_SPLIT_HEADER_HEIGHT,
    WINDOW_PADDING,
    typing_split_lines,
)
from relaydeck.client.screens.terminal.screen import mount_agent_screen
from tests.support.host_harness import render_component, run
from tests.support.terminal.sessions import _agent_session

DETAIL = 480.0
INSET = 346.0
SPLIT_TOP = WINDOW_PADDING + TYPING_SPLIT_HEADER_HEIGHT + 12
FIELD_CHROME = 48.0 + 8 + 44


@ft.component
def InsetHarness(session: Any) -> Any:
    inset, set_inset = ft.use_state(0.0)
    return ft.Column(
        expand=True,
        controls=[
            ft.Button("keyboard", key="raise-keyboard", on_click=lambda _e: set_inset(INSET)),
            ft.Container(
                expand=True,
                content=mount_agent_screen(  # type: ignore[call-arg]
                    session,
                    width_tier="expanded",
                    phone=False,
                    viewport_height=648 - inset,
                    viewport_width=951,
                    touch=True,
                    typing_split=True,
                    keyboard_inset=inset,
                    keyboard_up=inset > 0,
                ),
            ),
        ],
    )


async def _mounted(detail_height: float) -> tuple[Any, Any]:
    session, _ops, _factory, _submitted = _agent_session()
    session.open()
    await session.confirm_takeover()
    app = await render_component(InsetHarness, session)
    await app.set_props_and_fire(
        "agent-detail-panel", {}, "size_change", {"w": 1224.0, "h": detail_height}
    )
    return app, session


def _lines(column_height: float) -> int:
    return typing_split_lines(column_height - SPLIT_TOP - WINDOW_PADDING, FIELD_CHROME)


def _keyboard_down_lines() -> int:
    # With the keyboard down the draft takes at most 40 % of the column.
    column = DETAIL - SPLIT_TOP - WINDOW_PADDING
    return min(_lines(DETAIL), max(3, int(0.4 * column // FIELD_LINE_HEIGHT)))


def test_page_that_keeps_its_height_gets_the_whole_inset_as_bottom_margin() -> None:
    async def scenario() -> None:
        app, session = await _mounted(DETAIL)
        try:
            assert app.find("terminal-flex-host").margin.bottom == 0
            assert app.find("composer-draft").max_lines == _keyboard_down_lines()
            await app.set_props_and_fire("raise-keyboard", {}, "click")
            assert app.find("terminal-flex-host").margin.bottom == INSET
            assert app.find("composer-draft").max_lines == _lines(DETAIL - INSET)
        finally:
            await session.dispose()

    run(scenario())


def test_page_that_removed_the_keyboard_only_keeps_the_accessory_bar_clear() -> None:
    async def scenario() -> None:
        app, session = await _mounted(DETAIL)
        try:
            await app.set_props_and_fire("raise-keyboard", {}, "click")
            # The page shrank the detail by the keyboard alone (282 px); the bar remains.
            await app.set_props_and_fire(
                "agent-detail-panel", {}, "size_change", {"w": 1224.0, "h": DETAIL - 282}
            )
            assert app.find("terminal-flex-host").margin.bottom == INSET - 282
            assert app.find("composer-draft").max_lines == _lines(DETAIL - INSET)
        finally:
            await session.dispose()

    run(scenario())
