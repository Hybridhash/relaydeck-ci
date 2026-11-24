"""`LiveRegion`: the declarative `ft.Semantics` wrapper that surfaces announcements to
assistive technology (Flet `Semantics.live_region`).
"""

from __future__ import annotations

from typing import Any

import flet as ft


@ft.component
def LiveRegion(text: str, content: Any = None) -> Any:
    """A `live_region` Semantics node whose label carries the announcement.

    Pass the visible status control as `content` to keep it on screen
    while exposing `text` to assistive technology; otherwise a plain
    `ft.Text` is rendered.
    """
    return ft.Semantics(
        label=text,
        live_region=True,
        content=content if content is not None else ft.Text(text),
    )


__all__ = ["LiveRegion"]
