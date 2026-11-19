"""Composer focus and send timing diagnostics."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.platform.diagnostics.terminal import diagnostics_logger


def use_composer_focus_log(
    on_focus_change: Callable[[bool], Any] | None,
) -> tuple[Callable[[bool], Any], Callable[[], None]]:
    last_send: Any = ft.use_ref(None)

    def note_send() -> None:
        last_send.current = time.monotonic()

    def focus_changed(focused: bool) -> Any:
        since_send = (
            -1
            if last_send.current is None
            else max(0, round((time.monotonic() - last_send.current) * 1000))
        )
        diagnostics_logger().info("composer_focus focused=%s since_send_ms=%s", focused, since_send)
        return on_focus_change(focused) if on_focus_change is not None else None

    return focus_changed, note_send
