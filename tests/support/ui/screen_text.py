"""Everything a reader can read in a rendered tree."""

from __future__ import annotations

from typing import Any

import flet as ft

from tests.support.ui.focus import iter_controls


def screen_text(app: Any) -> list[str]:
    found: list[str] = []
    for control in iter_controls([app.page.views[0].controls], active_only=True):
        if isinstance(control, ft.Text) and isinstance(control.value, str):
            found.append(control.value)
        elif isinstance(control, ft.Button) and isinstance(control.content, str):
            found.append(control.content)
    return found
