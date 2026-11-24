"""How the terminal makes room for a keyboard: the split's overlap and the phone's held height."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.features.terminal.api import keyboard_overlap


def use_keyboard_overlap(
    detail_height: float,
    detail_width: float,
    keyboard_inset: float,
    *,
    keyboard_up: bool,
    split: bool,
) -> float:
    """Overlap in px for the split layout; 0 without a split. The detail height measured while
    the keyboard is down is the baseline, so what the page already removed for the keyboard
    is not subtracted twice.
    """
    base: Any = ft.use_ref((0.0, 0.0))  # (width, height) of the no-keyboard detail
    base_width, base_height = base.current
    if not keyboard_up and keyboard_inset <= 0:
        base.current = (detail_width, detail_height)
    elif base_width != detail_width:
        base.current = (detail_width, 0.0)
    if not split:
        return 0.0
    return keyboard_overlap(base.current[1], detail_height, keyboard_inset, keyboard_up=keyboard_up)


def use_terminal_hold(
    host_height: float,
    detail_width: float,
    keyboard_inset: float,
    keyboard_up: bool,
    phone: bool,
) -> float:
    """Height to keep drawing the phone terminal at while the keyboard is up, or 0. The pane
    keeps its rows when the page shrinks for the keyboard (a raw attach ignores the phone's
    size), so a shorter grid would draw the pane's lower rows over each other.
    """
    base: Any = ft.use_ref((0.0, 0.0))  # (width, host height) with the keyboard down
    if not keyboard_up and keyboard_inset <= 0:
        base.current = (detail_width, host_height)
    elif base.current[0] != detail_width:
        base.current = (detail_width, 0.0)
    held = float(base.current[1])
    if not phone or keyboard_inset <= 0 or held <= host_height + 2:
        return 0.0
    return held
