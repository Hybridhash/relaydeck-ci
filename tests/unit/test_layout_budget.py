"""Estimated terminal rows per scenario."""

from __future__ import annotations

import pytest

from relaydeck.client.features.terminal.layout_budget import (
    MIN_ROWS_CLOSED,
    MIN_ROWS_KEYBOARD,
    SAFARI_ACCESSORY_BAR,
    keyboard_overlap,
    plan_detail,
)


def _plan(viewport: float, **kw: object) -> object:
    detail = viewport - 96
    return plan_detail(detail_height=detail, viewport_height=viewport, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("viewport", [1032, 1376])
@pytest.mark.parametrize("font", [None, 18.0, 24.0])
@pytest.mark.parametrize("picture", [False, True])
def test_keyboard_closed_dock_open_keeps_twelve_rows(
    viewport: float, font: float | None, picture: bool
) -> None:
    budget = _plan(viewport, font_size=font, dock_open=True, picture_visible=picture)
    assert not budget.collapse
    assert budget.terminal_rows >= MIN_ROWS_CLOSED - 1e-6
    assert budget.dock_inline == 0 or budget.dock_overlay == 0


def test_dock_becomes_overlay_when_inline_would_starve_terminal() -> None:
    budget = plan_detail(detail_height=604, viewport_height=700, font_size=None, dock_open=True)
    assert budget.dock_inline == 0 and budget.dock_overlay > 0
    closed = plan_detail(detail_height=604, viewport_height=700, font_size=None, dock_open=False)
    assert budget.terminal_rows == closed.terminal_rows  # overlay never costs rows


def test_dock_closed_gives_terminal_all_remaining_height() -> None:
    open_ = _plan(1032, font_size=None, dock_open=True)
    closed = _plan(1032, font_size=None, dock_open=False)
    assert closed.terminal_rows > open_.terminal_rows >= MIN_ROWS_CLOSED


@pytest.mark.parametrize("viewport", [560, 480])
def test_keyboard_open_collapses_and_keeps_six_rows(viewport: float) -> None:
    budget = _plan(viewport, font_size=None, dock_open=True, picture_visible=True)
    assert budget.keyboard_open and budget.collapse
    assert budget.dock_inline == 0 and budget.dock_overlay > 0
    assert budget.terminal_rows >= MIN_ROWS_KEYBOARD


def test_impossible_budget_never_goes_negative() -> None:
    budget = plan_detail(detail_height=120, viewport_height=300, font_size=None, dock_open=True)
    assert budget.terminal_rows == 0


def test_measured_row_height_replaces_estimate() -> None:
    from relaydeck.client.features.terminal.layout_budget import measured_row_height

    assert measured_row_height(44 + 26 * 20, 26) == 20
    assert measured_row_height(520, 3) is None and measured_row_height(0, 30) is None
    base = dict(detail_height=936, viewport_height=1032, font_size=None, dock_open=False)
    est = plan_detail(**base)  # type: ignore[arg-type]
    big = plan_detail(**base, measured_row=40.0)  # type: ignore[arg-type]
    assert big.terminal_rows == pytest.approx(est.terminal_rows * (16 / 40))


@pytest.mark.parametrize("detail", [604, 330, 264])
@pytest.mark.parametrize("picture", [False, True])
def test_overlay_never_leaves_the_panel(detail: float, picture: bool) -> None:
    for viewport in (700, 360):
        b = plan_detail(
            detail_height=detail,
            viewport_height=viewport,
            font_size=None,
            dock_open=True,
            picture_visible=picture,
        )
        assert b.dock_overlay >= 0
        assert b.overlay_bottom + b.dock_overlay <= detail


def test_560_panel_collapses_secondary_chrome_to_reach_twelve_rows() -> None:
    b = plan_detail(detail_height=560, viewport_height=700, font_size=None, dock_open=True)
    assert b.collapse and b.terminal_rows >= MIN_ROWS_CLOSED


@pytest.mark.parametrize("detail", [464, 330, 604])
def test_overlay_stays_below_header_and_keeps_six_rows(detail: float) -> None:
    from relaydeck.client.features.terminal.layout_budget import (
        HEADER_HEIGHT,
        MIN_ROWS_KEYBOARD,
        PANEL_SPACING,
        row_height,
    )

    b = plan_detail(detail_height=detail, viewport_height=560, font_size=None, dock_open=True)
    top = detail - b.overlay_bottom - b.dock_overlay
    assert top >= HEADER_HEIGHT + PANEL_SPACING + MIN_ROWS_KEYBOARD * row_height(None) - 1e-6


@pytest.mark.parametrize("picture", [False, True])
def test_zero_room_is_neither_inline_nor_overlay(picture: bool) -> None:
    b = plan_detail(
        detail_height=180,
        viewport_height=300,
        font_size=None,
        dock_open=True,
        picture_visible=picture,
    )
    assert b.collapse and b.dock_inline == 0 and b.dock_overlay == 0


@pytest.mark.parametrize(
    ("base", "height", "inset", "keyboard_up", "expected"),
    [
        (600, 600, 0, False, 0.0),  # no keyboard
        (600, 600, 335, True, 335.0),  # page kept its height: the whole inset is uncovered
        (600, 318, 335, True, 53.0),  # page removed the keyboard, not the accessory bar
        (600, 265, 335, True, 0.0),  # page removed keyboard and bar
        (600, 318, 0, True, SAFARI_ACCESSORY_BAR),  # no sample: assume the bar is uncovered
        (600, 600, 0, True, 0.0),  # no sample and no shrink: nothing known
        (600, 318, 0, False, 0.0),  # a plain window shrink without the keyboard
        (0, 318, 335, True, 335.0),  # no baseline yet: the safe side
    ],
)
def test_keyboard_overlap(
    base: float, height: float, inset: float, keyboard_up: bool, expected: float
) -> None:
    assert keyboard_overlap(base, height, inset, keyboard_up=keyboard_up) == expected


def test_visual_inset_marks_the_keyboard_open_on_a_page_that_keeps_its_height() -> None:
    assert plan_detail(detail_height=552, viewport_height=648, font_size=None, dock_open=False)
    shrunk = plan_detail(detail_height=552, viewport_height=302, font_size=None, dock_open=False)
    assert shrunk.keyboard_open and shrunk.collapse
